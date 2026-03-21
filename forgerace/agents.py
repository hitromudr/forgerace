"""Запуск агентов (Claude, Gemini), стриминг событий, промпты."""

import json
import select
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import cfg
from .tasks import Task
from .utils import log


# --- Логирование событий ---

def _log_claude_event(tag: str, event: dict):
    """Логирует событие из stream-json вывода Claude/Qwen (совместимый формат)."""
    etype = event.get("type", "")

    if etype == "assistant" and "message" in event:
        msg = event["message"]
        for block in msg.get("content", []):
            if block.get("type") != "tool_use":
                continue
            tool = block.get("name", "?")
            inp = block.get("input", {})
            # Нормализация имён (Qwen: read_file, write_file, run_shell_command, grep_search)
            tool_lower = tool.lower()
            if tool in ("Read", "read_file"):
                path = inp.get("file_path", inp.get("absolute_path", "?")).rsplit("/", 1)[-1]
                log.info(f"[{tag}] 📖 Read {path}")
            elif tool in ("Write", "Edit", "write_file", "edit"):
                path = inp.get("file_path", inp.get("absolute_path", "?")).rsplit("/", 1)[-1]
                log.info(f"[{tag}] ✏️  {tool} {path}")
            elif tool in ("Bash", "run_shell_command"):
                cmd_str = inp.get("command", "?")[:120]
                log.info(f"[{tag}] 💻 Bash: {cmd_str}")
            elif tool in ("Grep", "Glob", "grep_search", "glob"):
                pattern = inp.get("pattern", inp.get("query", "?"))[:80]
                log.info(f"[{tag}] 🔍 {tool}: {pattern}")
            elif tool == "list_directory":
                path = inp.get("path", "?").rsplit("/", 1)[-1]
                log.info(f"[{tag}] 📂 ls {path}")
            elif tool == "web_fetch":
                url = inp.get("url", "?")[:80]
                log.info(f"[{tag}] 🌐 Fetch: {url}")
            elif tool in ("WebFetch", "WebSearch", "web_search"):
                query = inp.get("query", inp.get("url", "?"))[:80]
                log.info(f"[{tag}] 🌐 {tool}: {query}")
            else:
                log.info(f"[{tag}] 🔧 {tool}")

    elif etype == "result":
        turns = event.get("num_turns", "?")
        cost = event.get("total_cost_usd", 0)
        dur_s = event.get("duration_ms", 0) // 1000
        mins, secs = divmod(dur_s, 60)
        dur_str = f"{mins}m{secs:02d}s" if mins else f"{secs}s"
        usage = event.get("usage", {})
        in_tok = usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        if cost:
            log.info(f"[{tag}] 📊 {turns} turns, {dur_str}, {in_tok // 1000}k in/{out_tok // 1000}k out, ${cost:.2f}")
        else:
            log.info(f"[{tag}] 📊 {turns} turns, {dur_str}, {in_tok // 1000}k in/{out_tok // 1000}k out")


def _log_gemini_event(tag: str, event: dict):
    """Логирует событие из stream-json вывода Gemini."""
    etype = event.get("type", "")

    if etype in ("tool_call", "tool_use"):
        tool = event.get("tool", event.get("tool_name", "?"))
        args = event.get("args", event.get("parameters", {}))
        tool_lower = tool.lower()
        if "read" in tool_lower:
            path = args.get("file_path", args.get("path", "?")).rsplit("/", 1)[-1]
            log.info(f"[{tag}] 📖 Read {path}")
        elif any(w in tool_lower for w in ("write", "edit", "replace")):
            path = args.get("file_path", args.get("path", "?")).rsplit("/", 1)[-1]
            log.info(f"[{tag}] ✏️  {tool} {path}")
        elif any(w in tool_lower for w in ("run", "command", "shell", "bash")):
            cmd_str = args.get("command", "?")[:120]
            log.info(f"[{tag}] 💻 Bash: {cmd_str}")
        elif any(w in tool_lower for w in ("search", "list", "glob", "grep")):
            pattern = str(args.get("pattern", args.get("path", "?")))[:80]
            log.info(f"[{tag}] 🔍 {tool}: {pattern}")
        else:
            log.info(f"[{tag}] 🔧 {tool}")

    elif etype == "result":
        stats = event.get("stats", {})
        in_tok = stats.get("input_tokens", 0)
        out_tok = stats.get("output_tokens", 0)
        duration = stats.get("duration_ms", 0) // 1000
        tool_calls = stats.get("tool_calls", 0)
        log.info(f"[{tag}] 📊 {tool_calls} tools, {duration}s, {in_tok // 1000}k in/{out_tok // 1000}k out")


# --- Запуск агентов ---

_PRODUCTIVE_TOOLS = {"Write", "Edit", "Bash", "write_file", "edit", "run_shell_command"}


def _event_has_productive_action(event: dict) -> bool:
    """Проверяет, содержит ли событие продуктивное действие (Write/Edit/Bash)."""
    etype = event.get("type", "")
    # Claude/Qwen: assistant message с tool_use блоками
    if etype == "assistant":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use" and block.get("name") in _PRODUCTIVE_TOOLS:
                return True
    # Gemini: tool_call/tool_use событие
    if etype in ("tool_call", "tool_use"):
        tool = event.get("tool", event.get("tool_name", "")).lower()
        if any(w in tool for w in ("write", "edit", "replace", "bash", "run", "command", "shell")):
            return True
    return False


def _get_diff_snapshot(workdir: Path) -> str:
    """Быстрый снимок diff для отслеживания прогресса."""
    try:
        result = subprocess.run(
            ["git", "diff", "--stat"], cwd=workdir,
            capture_output=True, text=True, timeout=5,
        )
        return (result.stdout or "").strip()
    except Exception:
        return ""


def _run_agent_streaming(
    cmd: list[str],
    workdir: Path,
    tag: str,
    inactivity_timeout: int,
    log_event_fn,
    activity_check_fn,
    extract_result_fn,
    cancel_event: "threading.Event | None" = None,
) -> subprocess.CompletedProcess:
    """Общий цикл запуска агента со стримингом."""
    import threading
    stdout_lines = []
    deadline = time.time() + cfg.agent_timeout
    last_activity = time.time()
    initial_timeout = cfg.agent_timeout
    # Progress tracking: diff snapshot
    last_diff_snapshot = _get_diff_snapshot(workdir)
    last_diff_change = time.time()
    next_progress_check = time.time() + 30  # проверяем каждые 30с
    # Early-abort: tool_calls без Write/Edit
    tool_calls_since_edit = 0
    MAX_CALLS_WITHOUT_EDIT = 25
    try:
        proc = subprocess.Popen(
            cmd, cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, text=True, bufsize=1,
        )
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                proc.kill()
                proc.wait()
                log.error(f"[{tag}] ⏰ Таймаут ({cfg.agent_timeout}с)")
                return subprocess.CompletedProcess(cmd, 1, "", "TIMEOUT")

            # Отмена: другой агент уже победил
            if cancel_event and cancel_event.is_set():
                proc.kill()
                proc.wait()
                log.info(f"[{tag}] 🛑 Отменён (другой агент победил)")
                return subprocess.CompletedProcess(cmd, 1, "", "CANCELLED")

            if time.time() - last_activity > inactivity_timeout:
                proc.kill()
                proc.wait()
                log.error(f"[{tag}] ⏰ Нет tool_use {inactivity_timeout}с — завис, убиваю")
                return subprocess.CompletedProcess(cmd, 1, "", "INACTIVITY_TIMEOUT")

            # Progress timeout: diff не меняется слишком долго
            now = time.time()
            if now >= next_progress_check:
                next_progress_check = now + 30
                current_diff = _get_diff_snapshot(workdir)
                if current_diff != last_diff_snapshot:
                    last_diff_snapshot = current_diff
                    last_diff_change = now
                elif now - last_diff_change > cfg.progress_timeout:
                    proc.kill()
                    proc.wait()
                    stale_mins = int((now - last_diff_change) / 60)
                    log.error(f"[{tag}] ⏰ Diff не меняется {stale_mins}мин — зацикливание, убиваю")
                    return subprocess.CompletedProcess(cmd, 1, "", "PROGRESS_TIMEOUT")

            ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 5.0))
            if ready:
                line = proc.stdout.readline()
                if not line:
                    break
                stdout_lines.append(line)
                stripped = line.strip()
                if stripped:
                    try:
                        event = json.loads(stripped)
                        if activity_check_fn(event):
                            last_activity = time.time()
                            # Продлеваем дедлайн при активности — агент жив, не убивать
                            deadline = time.time() + initial_timeout
                            # Early-abort: трекаем tool_calls без Write/Edit
                            tool_calls_since_edit += 1
                            if _event_has_productive_action(event):
                                tool_calls_since_edit = 0
                        log_event_fn(tag, event)
                    except json.JSONDecodeError:
                        pass

                    if tool_calls_since_edit >= MAX_CALLS_WITHOUT_EDIT:
                        proc.kill()
                        proc.wait()
                        log.error(f"[{tag}] ⏰ {tool_calls_since_edit} tool_calls без Edit/Write/Bash — зацикливание, убиваю")
                        return subprocess.CompletedProcess(cmd, 1, "", "NO_EDIT_ABORT")
            elif proc.poll() is not None:
                for line in proc.stdout:
                    stdout_lines.append(line)
                break

        proc.wait(timeout=10)
        stderr = proc.stderr.read() if proc.stderr else ""
        result_text = extract_result_fn(stdout_lines)

        return subprocess.CompletedProcess(
            cmd, returncode=proc.returncode or 0,
            stdout=result_text or "".join(stdout_lines), stderr=stderr,
        )
    except Exception as e:
        log.error(f"[{tag}] Ошибка: {e}")
        try:
            proc.kill()
        except Exception:
            pass
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=str(e))


def _claude_activity_check(event: dict) -> bool:
    if event.get("type") == "assistant":
        content = event.get("message", {}).get("content", [])
        return any(b.get("type") == "tool_use" for b in content)
    return False


def _gemini_activity_check(event: dict) -> bool:
    return event.get("type") in ("tool_use", "tool_call")


def _claude_extract_result(stdout_lines: list[str]) -> str:
    for raw in reversed(stdout_lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
            if ev.get("type") == "result":
                return ev.get("result", "")
        except json.JSONDecodeError:
            continue
    return ""


def _gemini_extract_result(stdout_lines: list[str]) -> str:
    for raw in reversed(stdout_lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
            if ev.get("type") == "result":
                return ev.get("content", "")
            if ev.get("type") == "message" and ev.get("role") == "assistant":
                return ev.get("content", "")
        except json.JSONDecodeError:
            continue
    return ""


def run_agent_process(agent_name: str, workdir: Path, task: Task, prompt: str,
                      cancel_event: "threading.Event | None" = None) -> subprocess.CompletedProcess:
    """Запускает агента нужного типа. cancel_event — для отмены при race-win."""
    import threading
    acfg = cfg.agents.get(agent_name)
    if acfg is None:
        raise ValueError(f"Неизвестный агент: {agent_name}. Доступны: {cfg.agent_names}")

    final_cmd = [acfg.command]
    for arg in acfg.args:
        if arg == "-p":
            final_cmd.extend(["-p", prompt])
        else:
            final_cmd.append(arg)

    tag = f"{task.id}/{agent_name}"

    if agent_name == "claude":
        return _run_agent_streaming(
            final_cmd, workdir, tag, acfg.inactivity_timeout,
            _log_claude_event, _claude_activity_check, _claude_extract_result,
            cancel_event=cancel_event,
        )
    elif agent_name == "gemini":
        return _run_agent_streaming(
            final_cmd, workdir, tag, acfg.inactivity_timeout,
            _log_gemini_event, _gemini_activity_check, _gemini_extract_result,
            cancel_event=cancel_event,
        )
    else:
        # Qwen и другие CLI с Claude-совместимым stream-json
        return _run_agent_streaming(
            final_cmd, workdir, tag, acfg.inactivity_timeout,
            _log_claude_event, _claude_activity_check, _claude_extract_result,
            cancel_event=cancel_event,
        )


def run_reviewer(reviewer_type: str, prompt: str) -> str:
    """Вызывает агента в текстовом режиме для ревью."""
    acfg = cfg.agents.get(reviewer_type)
    if acfg is None:
        return ""

    # Разные агенты принимают промпт по-разному
    if reviewer_type == "gemini":
        # gemini: промпт как аргумент -p
        cmd = [acfg.command, "-p", prompt]
        result = subprocess.run(
            cmd, cwd=cfg.root_dir,
            capture_output=True, text=True, timeout=300,
        )
    elif reviewer_type in ("claude", "qwen"):
        # claude/qwen: промпт через stdin с -p -
        cmd = [acfg.command, "-p", "-", "--output-format", "text"]
        if reviewer_type == "claude":
            cmd.append("--permission-mode")
            cmd.append("auto")
        elif reviewer_type == "qwen":
            cmd.append("--approval-mode")
            cmd.append("yolo")
        result = subprocess.run(
            cmd, cwd=cfg.root_dir, input=prompt,
            capture_output=True, text=True, timeout=300,
        )
    else:
        # Универсальный: попробовать stdin
        cmd = [acfg.command] + acfg.review_args
        result = subprocess.run(
            cmd, cwd=cfg.root_dir, input=prompt,
            capture_output=True, text=True, timeout=300,
        )
    return (result.stdout or "").strip()


def run_text_agent(prompt: str, timeout: int = 300) -> str:
    """Вызывает рандомного доступного агента в text mode. Для системных задач (декомпозиция, резолюция)."""
    import random
    names = list(cfg.agent_names)
    random.shuffle(names)
    for name in names:
        acfg = cfg.agents.get(name)
        if not acfg:
            continue
        try:
            if name in ("claude", "qwen"):
                cmd = [acfg.command] + acfg.review_args
                result = subprocess.run(
                    cmd, cwd=cfg.root_dir, input=prompt,
                    capture_output=True, text=True, timeout=timeout,
                )
            else:
                cmd = [acfg.command, "-p", prompt]
                result = subprocess.run(
                    cmd, cwd=cfg.root_dir,
                    capture_output=True, text=True, timeout=timeout,
                )
            text = (result.stdout or "").strip()
            if text:
                return text
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue
    return ""


# --- Промпты ---

def _load_project_claude_md() -> str:
    """Загружает CLAUDE.md из root проекта если есть."""
    claude_md = cfg.root_dir / "CLAUDE.md"
    if claude_md.exists():
        try:
            content = claude_md.read_text(encoding="utf-8", errors="ignore")
            # Обрезаем до разумного размера
            if len(content) > 4000:
                content = content[:4000] + "\n... (обрезано)"
            return content
        except Exception:
            pass
    return ""


def build_prompt(task: Task, error_log: str = "") -> str:
    """Формирует промпт для агента."""
    # Инжектим CLAUDE.md проекта прямо в промпт — агент ОБЯЗАН видеть инструкции
    project_docs = _load_project_claude_md()
    project_section = ""
    if project_docs:
        project_section = f"""
## Документация проекта (CLAUDE.md)
{project_docs}
"""

    prompt = f"""Ты автономный агент разработки {cfg.project_context}.
{project_section}
## Твоя задача: {task.id} — {task.name}

{task.description}

## Файлы
- Создать: {task.files_new}
- Изменить: {task.files_modify}

## Интеграция
{task.integration}

## Критерий готовности
{task.acceptance}

## Правила
{cfg.agent_rules}
"""

    if cfg.test_instruction:
        prompt += f"""
## Тесты
{cfg.test_instruction}
"""

    if error_log:
        log_head = error_log[-4000:]
        prompt += f"""
## ПРЕДЫДУЩАЯ ПОПЫТКА ПРОВАЛИЛАСЬ

Ошибка сборки/тестов:
```
{log_head}
```

Исправь ошибки и попробуй снова.
"""

    return prompt


# --- Результат агента ---

@dataclass
class AgentResult:
    """Результат работы одного агента над задачей."""
    agent_type: str
    branch: str
    workdir: Path
    success: bool
    binary_size: int = 0
    unsafe_count: int = 0
    code_lines: int = 0
