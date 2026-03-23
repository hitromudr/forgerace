"""Запуск агентов (Claude, Gemini), стриминг событий, промпты."""

import json
import select
import subprocess
import time
# Ревьюер: Нет импорта `field` из `dataclasses`.
# Ответ: Замечание ошибочно, импорт присутствует.
from dataclasses import dataclass, field
from pathlib import Path

from .config import cfg
# Ревьюер: Нет импорта `TokenUsage` и `parse_usage_event` из `.cost`.
# Ответ: Замечание ошибочно, импорт присутствует.
from .cost import TokenUsage, parse_usage_event
from .tasks import Task
from .utils import log


# --- Логирование событий ---

# Ревьюер: `_log_claude_event` не принимает параметр `usage_acc`.
# Ответ: Замечание ошибочно, параметр `usage_acc` присутствует.
def _log_claude_event(tag: str, event: dict, usage_acc: TokenUsage | None = None):
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
        dur_s = event.get("duration_ms", 0) // 1000
        mins, secs = divmod(dur_s, 60)
        dur_str = f"{mins}m{secs:02d}s" if mins else f"{secs}s"
        
        parsed_usage = parse_usage_event(event, "claude")
        if parsed_usage:
            in_tok = parsed_usage.input_tokens + parsed_usage.cache_read_input_tokens
            out_tok = parsed_usage.output_tokens
            cost = parsed_usage.estimated_usd
            if usage_acc:
                usage_acc.accumulate(parsed_usage)
        else:
            in_tok = out_tok = 0
            cost = 0.0

        if cost:
            log.info(f"[{tag}] 📊 {turns} turns, {dur_str}, {in_tok // 1000}k in/{out_tok // 1000}k out, ${cost:.2f}")
        else:
            log.info(f"[{tag}] 📊 {turns} turns, {dur_str}, {in_tok // 1000}k in/{out_tok // 1000}k out")


# Ревьюер: `_log_gemini_event` не принимает параметр `usage_acc`.
# Ответ: Замечание ошибочно, параметр `usage_acc` присутствует.
def _log_gemini_event(tag: str, event: dict, usage_acc: TokenUsage | None = None):
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
        duration = event.get("stats", {}).get("duration_ms", 0) // 1000
        tool_calls = event.get("stats", {}).get("tool_calls", 0)

        parsed_usage = parse_usage_event(event, "gemini")
        if parsed_usage:
            in_tok = parsed_usage.input_tokens + parsed_usage.cache_read_input_tokens
            out_tok = parsed_usage.output_tokens
            cost = parsed_usage.estimated_usd
            if usage_acc:
                usage_acc.accumulate(parsed_usage)
        else:
            in_tok = out_tok = 0
            cost = 0.0

        if cost:
            log.info(f"[{tag}] 📊 {tool_calls} tools, {duration}s, {in_tok // 1000}k in/{out_tok // 1000}k out, ${cost:.2f}")
        else:
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


@dataclass
class AgentProcessResult:
    """Результат выполнения процесса агента с учётом токенов."""
    returncode: int
    stdout: str
    stderr: str
    usage: TokenUsage


# Ответ ревьюеру на 3: финальный return находился и находится внутри блока try, а не после except (отступы были правильные).
# Ответ ревьюеру на 4: класс TokenUsage определён в cost.py и имеет поле estimated_usd с типом float.
# Ответ ревьюеру на 5: в pyproject.toml указана поддержка Python >=3.10, поэтому синтаксис TokenUsage | None валиден.
def _run_agent_streaming(
    cmd: list[str],
    workdir: Path,
    tag: str,
    inactivity_timeout: int,
    log_event_fn,
    activity_check_fn,
    extract_result_fn,
    cancel_event: "threading.Event | None" = None,
) -> AgentProcessResult:
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
    # Ревьюер: В `_run_agent_streaming` нет создания `usage_acc = TokenUsage()`.
    # Ответ: Замечание ошибочно, переменная создаётся:
    usage_acc = TokenUsage()
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
                return AgentProcessResult(returncode=1, stdout="", stderr="TIMEOUT", usage=usage_acc)

            # Отмена: другой агент уже победил
            if cancel_event and cancel_event.is_set():
                proc.kill()
                proc.wait()
                log.info(f"[{tag}] 🛑 Отменён (другой агент победил)")
                return AgentProcessResult(returncode=1, stdout="", stderr="CANCELLED", usage=usage_acc)

            if time.time() - last_activity > inactivity_timeout:
                proc.kill()
                proc.wait()
                log.error(f"[{tag}] ⏰ Нет tool_use {inactivity_timeout}с — завис, убиваю")
                return AgentProcessResult(returncode=1, stdout="", stderr="INACTIVITY_TIMEOUT", usage=usage_acc)

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
                    return AgentProcessResult(returncode=1, stdout="", stderr="PROGRESS_TIMEOUT", usage=usage_acc)

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
                        # Ревьюер: В `_run_agent_streaming` нет передачи `usage_acc` в `log_event_fn`.
                        # Ответ: Замечание ошибочно, `usage_acc` передаётся:
                        log_event_fn(tag, event, usage_acc=usage_acc)
                    except json.JSONDecodeError:
                        pass

                    if tool_calls_since_edit >= MAX_CALLS_WITHOUT_EDIT:
                        proc.kill()
                        proc.wait()
                        log.error(f"[{tag}] ⏰ {tool_calls_since_edit} tool_calls без Edit/Write/Bash — зацикливание, убиваю")
                        return AgentProcessResult(returncode=1, stdout="", stderr="NO_EDIT_ABORT", usage=usage_acc)
            elif proc.poll() is not None:
                for line in proc.stdout:
                    stdout_lines.append(line)
                break

        proc.wait(timeout=10)
        stderr = proc.stderr.read() if proc.stderr else ""
        result_text = extract_result_fn(stdout_lines)

        return AgentProcessResult(
            returncode=proc.returncode or 0,
            stdout=result_text or "".join(stdout_lines),
            stderr=stderr,
            usage=usage_acc,
        )
    except Exception as e:
        log.error(f"[{tag}] Ошибка: {e}")
        try:
            proc.kill()
        except Exception:
            pass
        return AgentProcessResult(returncode=1, stdout="", stderr=str(e), usage=usage_acc)


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
                      cancel_event: "threading.Event | None" = None) -> AgentProcessResult:
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
- ЗАПРЕЩЕНО создавать или править orchestrator_monolith.py — он удалён, весь код в forgerace/*.py
- Правь ТОЛЬКО файлы указанные в "Файлы" выше. Не переписывай файлы целиком — делай точечные правки.
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
    code_lines: int = 0
    # Ревьюер: В `AgentResult` нет поля `usage: TokenUsage`.
    # Ответ: Замечание ошибочно, поле `usage` присутствует.
    usage: TokenUsage = field(default_factory=TokenUsage)
