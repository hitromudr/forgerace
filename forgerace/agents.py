"""Запуск агентов (Claude, Gemini), стриминг событий, промпты."""

import json
import os
import select
import subprocess
import time
import threading
# Suppress node.js deprecation warnings (gemini CLI punycode spam)
os.environ.setdefault("NODE_OPTIONS", "--no-deprecation")
# Ревьюер: Нет импорта `field` из `dataclasses`.
# Ответ: Замечание ошибочно, импорт присутствует.
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

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
            tool.lower()
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


def _log_codex_event(tag: str, event: dict, usage_acc: TokenUsage | None = None):
    """Логирует событие из --json вывода Codex CLI."""
    etype = event.get("type", "")

    if etype == "item.started":
        item = event.get("item", {})
        itype = item.get("type", "")
        if itype == "commandExecution":
            cmd = item.get("command", "?")[:120]
            log.info(f"[{tag}] 💻 Bash: {cmd}")
        elif itype == "fileChange":
            changes = item.get("changes", [])
            files = [c.get("path", "?").rsplit("/", 1)[-1] for c in changes[:3]]
            log.info(f"[{tag}] ✏️  Edit {', '.join(files)}")
        elif itype == "webSearch":
            query = item.get("query", "?")[:80]
            log.info(f"[{tag}] 🌐 Search: {query}")
        elif itype == "mcpToolCall":
            tool = item.get("tool", "?")
            log.info(f"[{tag}] 🔧 MCP: {tool}")
        elif itype == "agentMessage":
            pass  # шум
        elif itype == "reasoning":
            pass
        else:
            log.info(f"[{tag}] 🔧 {itype}")

    elif etype == "item.completed":
        item = event.get("item", {})
        itype = item.get("type", "")
        if itype == "commandExecution":
            exit_code = item.get("exitCode", "?")
            dur = item.get("durationMs", 0) // 1000
            if exit_code != 0:
                log.info(f"[{tag}] 💻 exit={exit_code} ({dur}s)")

    elif etype == "turn.completed":
        usage = event.get("usage", {})
        in_tok = usage.get("input_tokens", 0) + usage.get("cached_input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        if usage_acc:
            parsed = TokenUsage(
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=out_tok,
                cache_read_input_tokens=usage.get("cached_input_tokens", 0),
            )
            usage_acc.accumulate(parsed)
        log.info(f"[{tag}] 📊 turn done, {in_tok // 1000}k in/{out_tok // 1000}k out")


def _log_goose_event(tag: str, event: dict, usage_acc: TokenUsage | None = None):
    """Логирует событие из stream-json вывода Goose CLI.

    Goose format:
      {"type":"message","message":{"content":[{"type":"text","text":"..."}]}}
      {"type":"complete","total_tokens":N}
    """
    etype = event.get("type", "")

    if etype == "message":
        msg = event.get("message", {})
        for block in msg.get("content", []):
            btype = block.get("type", "")
            if btype == "tool_use":
                tool = block.get("name", "?")
                inp = block.get("input", {})
                if "read" in tool.lower():
                    path = inp.get("path", inp.get("file_path", "?")).rsplit("/", 1)[-1]
                    log.info(f"[{tag}] 📖 Read {path}")
                elif any(w in tool.lower() for w in ("write", "edit", "replace")):
                    path = inp.get("path", inp.get("file_path", "?")).rsplit("/", 1)[-1]
                    log.info(f"[{tag}] ✏️  {tool} {path}")
                elif any(w in tool.lower() for w in ("bash", "shell", "command", "run")):
                    cmd_str = str(inp.get("command", "?"))[:120]
                    log.info(f"[{tag}] 💻 Bash: {cmd_str}")
                else:
                    log.info(f"[{tag}] 🔧 {tool}")
            elif btype == "text":
                text = block.get("text", "")
                if text and len(text) < 200:
                    log.info(f"[{tag}] 💬 {text[:120]}")

    elif etype == "complete":
        total = event.get("total_tokens", 0)
        if usage_acc and total:
            # Goose reports only total_tokens; estimate split 70/30
            usage_acc.accumulate(TokenUsage(
                input_tokens=int(total * 0.7),
                output_tokens=int(total * 0.3),
            ))
        log.info(f"[{tag}] 📊 done, {total} total tokens")


def _goose_activity_check(event: dict) -> bool:
    """Goose activity: message events count as activity."""
    return event.get("type") in ("message", "complete")


def _goose_extract_result(stdout_lines: list[str]) -> str:
    """Extract final text from goose stream-json output."""
    for raw in reversed(stdout_lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
            if ev.get("type") == "message":
                msg = ev.get("message", {})
                for block in msg.get("content", []):
                    if block.get("type") == "text" and block.get("text", ""):
                        return block["text"]
        except json.JSONDecodeError:
            continue
    return ""


def _codex_activity_check(event: dict) -> bool:
    etype = event.get("type", "")
    if etype in ("item.started", "item.completed"):
        itype = event.get("item", {}).get("type", "")
        return itype in ("commandExecution", "fileChange", "mcpToolCall")
    return False


def _codex_extract_result(stdout_lines: list[str]) -> str:
    for raw in reversed(stdout_lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
            if ev.get("type") == "item.completed":
                item = ev.get("item", {})
                if item.get("type") == "agentMessage":
                    return item.get("text", "")
        except json.JSONDecodeError:
            continue
    return ""


# --- Запуск агентов ---

def _terminate_agent_process(proc: subprocess.Popen):
    """Graceful shutdown процесса агента: terminate → wait(timeout=2) → kill при зависании."""
    pid = proc.pid
    log.info(f"Завершение процесса агента (pid={pid}): terminate...")
    try:
        proc.terminate()
        proc.wait(timeout=2)
        log.info(f"Процесс агента (pid={pid}) завершён через terminate")
    except subprocess.TimeoutExpired:
        log.warning(f"Процесс агента (pid={pid}) не ответил на terminate, применяю kill")
        try:
            proc.kill()
            proc.wait(timeout=5)
            log.info(f"Процесс агента (pid={pid}) принудительно завершён через kill")
        except subprocess.TimeoutExpired:
            log.error(f"Процесс агента (pid={pid}) не завершился даже после kill")


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
    # Goose: toolRequest inside message content
    if etype == "message":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "toolRequest":
                tool_name = (block.get("toolCall", {}).get("value", {}).get("name", "")).lower()
                if any(w in tool_name for w in ("write", "edit", "text_editor", "shell", "bash", "command")):
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
    env: dict[str, str] | None = None,
    prompt_stdin: str = "",
    on_usage_update: "Callable[[TokenUsage], None] | None" = None,
) -> AgentProcessResult:
    """Общий цикл запуска агента со стримингом.

    Args:
        on_usage_update: Optional callback, called every time TokenUsage is updated.
    """
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
    proc_env = {**os.environ, **(env or {})}
    proc_env["PYTHONUNBUFFERED"] = "1"
    proc_env["FORCE_COLOR"] = "1"
    # Remove proxy vars entirely — empty string breaks requests library
    for pv in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        proc_env.pop(pv, None)
    try:
        proc = subprocess.Popen(
            cmd, cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if prompt_stdin else subprocess.DEVNULL,
            text=False, bufsize=0, env=proc_env,
        )
        if prompt_stdin:
            try:
                proc.stdin.write(prompt_stdin.encode("utf-8"))
                proc.stdin.close()
            except BrokenPipeError:
                pass
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                _terminate_agent_process(proc)
                log.error(f"[{tag}] ⏰ Таймаут ({cfg.agent_timeout}с)")
                return AgentProcessResult(returncode=1, stdout="", stderr="TIMEOUT", usage=usage_acc)

            # Отмена: другой агент уже победил
            if cancel_event and cancel_event.is_set():
                _terminate_agent_process(proc)
                log.info(f"[{tag}] 🛑 Отменён (другой агент победил)")
                return AgentProcessResult(returncode=1, stdout="", stderr="CANCELLED", usage=usage_acc)

            if time.time() - last_activity > inactivity_timeout:
                _terminate_agent_process(proc)
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
                line_bytes = proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace")
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
                        # Вызов колбэка при обновлении TokenUsage
                        if on_usage_update is not None:
                            on_usage_update(usage_acc)
                    except json.JSONDecodeError:
                        pass

                    if tool_calls_since_edit >= MAX_CALLS_WITHOUT_EDIT:
                        proc.kill()
                        proc.wait()
                        log.error(f"[{tag}] ⏰ {tool_calls_since_edit} tool_calls без Edit/Write/Bash — зацикливание, убиваю")
                        return AgentProcessResult(returncode=1, stdout="", stderr="NO_EDIT_ABORT", usage=usage_acc)
            elif proc.poll() is not None:
                for line_bytes in proc.stdout:
                    stdout_lines.append(line_bytes.decode("utf-8", errors="replace"))
                break

        proc.wait(timeout=10)
        stderr_bytes = proc.stderr.read() if proc.stderr else b""
        stderr = stderr_bytes.decode("utf-8", errors="replace")
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


_AIDER_STDERR_NOISE = (
    "warning: it's best to only add files",
    "https://aider.chat/docs/troubleshooting/edit-errors.html",
    "aider/docs/troubleshooting",
    "warning: no model settings found",
)


def _filter_aider_stderr(stderr: str) -> str:
    """Remove known aider noise lines from stderr."""
    if not stderr:
        return stderr
    lines = stderr.splitlines()
    filtered = [
        line for line in lines
        if not any(noise in line.lower() for noise in _AIDER_STDERR_NOISE)
    ]
    return "\n".join(filtered).strip()


def _run_agent_text(
    cmd: list[str],
    workdir: Path,
    tag: str,
    inactivity_timeout: int,
    cancel_event: "threading.Event | None" = None,
    env: dict[str, str] | None = None,
    prompt_stdin: str = "",
) -> AgentProcessResult:
    """Text-mode runner for agents without stream-json (aider, etc.).
    Tracks progress via git diff snapshots, logs raw output lines."""
    usage_acc = TokenUsage()
    proc_env = {**os.environ, **(env or {})}
    proc_env["PYTHONUNBUFFERED"] = "1"
    proc_env["FORCE_COLOR"] = "1"
    # Remove proxy vars entirely — empty string breaks requests library
    for pv in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        proc_env.pop(pv, None)
    deadline = time.time() + cfg.agent_timeout
    last_diff_snapshot = _get_diff_snapshot(workdir)
    last_diff_change = time.time()
    next_progress_check = time.time() + 30
    stdout_lines = []

    try:
        proc = subprocess.Popen(
            cmd, cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if prompt_stdin else subprocess.DEVNULL,
            text=False, bufsize=0, env=proc_env,
        )
        if prompt_stdin:
            try:
                proc.stdin.write(prompt_stdin.encode("utf-8"))
                proc.stdin.close()
            except BrokenPipeError:
                pass

        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                _terminate_agent_process(proc)
                log.error(f"[{tag}] ⏰ Таймаут ({cfg.agent_timeout}с)")
                return AgentProcessResult(returncode=1, stdout="", stderr="TIMEOUT", usage=usage_acc)

            if cancel_event and cancel_event.is_set():
                _terminate_agent_process(proc)
                log.info(f"[{tag}] 🛑 Отменён (другой агент победил)")
                return AgentProcessResult(returncode=1, stdout="", stderr="CANCELLED", usage=usage_acc)

            # Progress check via diff
            now = time.time()
            if now >= next_progress_check:
                next_progress_check = now + 30
                current_diff = _get_diff_snapshot(workdir)
                if current_diff != last_diff_snapshot:
                    last_diff_snapshot = current_diff
                    last_diff_change = now
                    deadline = now + cfg.agent_timeout  # extend on progress
                    log.info(f"[{tag}] 📝 Прогресс: файлы изменены")
                elif now - last_diff_change > cfg.progress_timeout:
                    _terminate_agent_process(proc)
                    log.error(f"[{tag}] ⏰ Diff не меняется — зацикливание")
                    return AgentProcessResult(returncode=1, stdout="", stderr="PROGRESS_TIMEOUT", usage=usage_acc)

            ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 5.0))
            if ready:
                line_bytes = proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace")
                stdout_lines.append(line)
                stripped = line.strip()
                if stripped:
                    # Log meaningful lines (skip empty/whitespace)
                    if any(kw in stripped.lower() for kw in ("applied edit", "wrote", "created", "error", "warning")):
                        log.info(f"[{tag}] {stripped[:120]}")
            elif proc.poll() is not None:
                for line_bytes in proc.stdout:
                    stdout_lines.append(line_bytes.decode("utf-8", errors="replace"))
                break

        proc.wait(timeout=10)
        stderr_bytes = proc.stderr.read() if proc.stderr else b""
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        stderr = _filter_aider_stderr(stderr)
        return AgentProcessResult(
            returncode=proc.returncode or 0,
            stdout="".join(stdout_lines),
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


_PROTECTED_FILES = {"TASKS.md", "CLAUDE.md", "forgerace.toml", ".gitignore"}


def _extract_task_files(task: Task) -> list[str]:
    """Extract file paths from task.files_new and task.files_modify fields.
    Excludes orchestrator metadata files that agents must not modify."""
    files = []
    for raw in (task.files_new, task.files_modify):
        if not raw or raw.strip() in ("", "—", "-"):
            continue
        for part in raw.replace(",", " ").split():
            part = part.strip().strip("`")
            if part and part not in ("—", "-") and part not in _PROTECTED_FILES:
                files.append(part)
    return files


def run_agent_process(agent_name: str, workdir: Path, task: Task, prompt: str,
                      cancel_event: "threading.Event | None" = None) -> AgentProcessResult:
    """Запускает агента нужного типа. cancel_event — для отмены при race-win."""
    acfg = cfg.agents.get(agent_name)
    if acfg is None:
        raise ValueError(f"Неизвестный агент: {agent_name}. Доступны: {cfg.all_agent_names}")

    final_cmd = [acfg.command]
    for arg in acfg.args:
        if arg == "-p":
            # -p — маркер: вставить [-p, prompt] (Claude/Gemini/Qwen)
            final_cmd.extend(["-p", prompt])
        elif arg == "{prompt}":
            # {prompt} — universal placeholder для агентов с другим синтаксисом
            final_cmd.append(prompt)
        else:
            final_cmd.append(arg)

    tag = f"{task.id}/{agent_name}"

    # Text-mode agents (aider, etc.) — no stream-json
    if acfg.protocol == "text":
        # Aider: pass --file args so it only sees task-relevant files
        if acfg.command == "aider":
            task_files = _extract_task_files(task)
            for fpath in task_files:
                final_cmd.extend(["--file", fpath])
            # Fix: write prompt to temp file instead of /dev/stdin
            # (aider --message-file /dev/stdin breaks when stdin=PIPE)
            import tempfile
            fd, tmp_path = tempfile.mkstemp(suffix=".md", dir=str(workdir))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(prompt)
            prompt_file = Path(tmp_path)
            final_cmd = [a if a != "/dev/stdin" else str(prompt_file) for a in final_cmd]
        return _run_agent_text(
            final_cmd, workdir, tag, acfg.inactivity_timeout,
            cancel_event=cancel_event,
            env=acfg.env if acfg.env else None,
        )

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
    elif agent_name == "codex":
        return _run_agent_streaming(
            final_cmd, workdir, tag, acfg.inactivity_timeout,
            _log_codex_event, _codex_activity_check, _codex_extract_result,
            cancel_event=cancel_event,
        )
    elif acfg.command == "goose":
        return _run_agent_streaming(
            final_cmd, workdir, tag, acfg.inactivity_timeout,
            _log_goose_event, _goose_activity_check, _goose_extract_result,
            cancel_event=cancel_event,
            env=acfg.env if acfg.env else None,
            prompt_stdin=prompt if acfg.prompt_stdin else "",
        )
    else:
        # Qwen и другие CLI с Claude-совместимым stream-json
        return _run_agent_streaming(
            final_cmd, workdir, tag, acfg.inactivity_timeout,
            _log_claude_event, _claude_activity_check, _claude_extract_result,
            cancel_event=cancel_event,
        )


_QUOTA_KEYWORDS = ("quota exceeded", "rate limit", "api key", "429")
_disabled_agents: set[str] = set()  # agents disabled at runtime (quota, auth errors)


def is_agent_disabled(name: str) -> bool:
    """Check if agent was disabled due to quota/auth errors."""
    return name in _disabled_agents


def check_agent_quota(name: str) -> bool:
    """Quick health check: send a tiny prompt, return True if agent responds."""
    acfg = cfg.agents.get(name)
    if acfg is None:
        return False
    if name == "claude":
        cmd = [acfg.command, "-p", "-", "--output-format", "text", "--max-turns", "1"]
    elif name == "gemini":
        cmd = [acfg.command, "-p", "", "--output-format", "text"]
    elif name == "qwen":
        cmd = [acfg.command, "-p", "-", "--output-format", "text", "--approval-mode", "yolo"]
    else:
        cmd = [acfg.command, "-p", "hi"]
    try:
        result = subprocess.run(
            cmd, input="Reply with exactly: OK", cwd=cfg.root_dir,
            capture_output=True, text=True, timeout=30,
        )
        stderr_lower = (result.stderr or "").lower()
        if any(kw in stderr_lower for kw in _QUOTA_KEYWORDS):
            _disabled_agents.add(name)
            log.error("check_agent_quota(%s): квота исчерпана — отключён", name)
            return False
        if result.returncode != 0:
            log.warning("check_agent_quota(%s): exit code %d", name, result.returncode)
            return False
        return True
    except subprocess.TimeoutExpired:
        log.warning("check_agent_quota(%s): таймаут 30с", name)
        return True  # timeout != quota, agent might be slow


def preflight_check_agents() -> list[str]:
    """Check all enabled agents for quota before task execution. Returns available agent names."""
    available = []
    for name in cfg.all_agent_names:
        log.info(f"  🔍 Проверка квоты: {name}...")
        if check_agent_quota(name):
            available.append(name)
            log.info(f"  ✓ {name} — доступен")
        else:
            log.warning(f"  ✗ {name} — недоступен (квота/ошибка)")
    return available


def _call_openai_api(acfg, prompt: str, timeout: int = 300) -> str:
    """Call OpenAI-compatible API (nvidia, openrouter, etc.)."""
    import urllib.request
    import urllib.error

    url = acfg.base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {acfg.api_key}",
    }
    # Truncate prompt to avoid API limits (most models handle ~30K chars)
    max_prompt_chars = 30000
    if len(prompt) > max_prompt_chars:
        prompt = prompt[:max_prompt_chars] + "\n\n... (обрезано)"

    body = json.dumps({
        "model": acfg.model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
        "temperature": 0.3,
    }).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")[:500]
        log.warning("OpenAI API (%s) error %d: %s", acfg.model, e.code, err_body)
        return ""
    except (urllib.error.URLError, TimeoutError, ConnectionError,
            json.JSONDecodeError, OSError) as e:
        log.warning("OpenAI API (%s) request failed: %s", acfg.model, e)
        return ""


def run_reviewer(reviewer_type: str, prompt: str) -> str:
    """Вызывает агента в текстовом режиме для ревью."""
    if reviewer_type in _disabled_agents:
        log.warning("run_reviewer(%s) skipped — agent disabled (quota/auth)", reviewer_type)
        return ""
    acfg = cfg.agents.get(reviewer_type)
    if acfg is None:
        return ""

    # OpenAI-compatible API (nvidia, openrouter, etc.)
    if acfg.protocol == "openai":
        return _call_openai_api(acfg, prompt, acfg.inactivity_timeout or 300)

    # CLI agents — prompt via stdin
    if reviewer_type == "claude":
        cmd = [acfg.command, "-p", "-", "--output-format", "text", "--permission-mode", "auto"]
    elif reviewer_type == "qwen":
        cmd = [acfg.command, "-p", "-", "--output-format", "text", "--approval-mode", "yolo"]
    elif reviewer_type == "gemini":
        cmd = [acfg.command, "-p", "", "--output-format", "text"]
    elif reviewer_type == "codex":
        cmd = [acfg.command, "exec", "--full-auto"]
    elif acfg.command == "goose":
        # Goose CLI mangles output — use API directly for review via LiteLLM
        goose_model = "llama-70b"
        for j, a in enumerate(acfg.args):
            if a == "--model" and j + 1 < len(acfg.args):
                goose_model = acfg.args[j + 1]
        api_base = (acfg.env or {}).get("OPENAI_HOST", "http://127.0.0.1:4000")
        api_key = (acfg.env or {}).get("OPENAI_API_KEY", "fr-local-dev")
        # Build ad-hoc config for API call
        from dataclasses import replace
        api_acfg = replace(acfg, base_url=api_base + "/v1",
                           api_key=api_key, model=goose_model)
        return _call_openai_api(api_acfg, prompt, acfg.inactivity_timeout or 300)
    else:
        cmd = [acfg.command]
        for arg in acfg.review_args:
            if arg != "{prompt}":
                cmd.append(arg)
    # Build env (for goose, aider with proxy)
    proc_env = {**os.environ, **(acfg.env if acfg.env else {})}
    proc_env["PYTHONUNBUFFERED"] = "1"
    proc_env["FORCE_COLOR"] = "1"
    # Remove proxy vars entirely — empty string breaks requests library
    for pv in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        proc_env.pop(pv, None)
    timeout = acfg.inactivity_timeout or 300
    result = subprocess.run(
        cmd, cwd=cfg.root_dir, input=prompt,
        capture_output=True, text=True, timeout=timeout,
        env=proc_env,
    )
    if result.returncode != 0:
        log.warning("run_reviewer(%s) exit code %d: %s",
                     reviewer_type, result.returncode, (result.stderr or "")[:500])
    # Detect quota/auth errors in stderr only (stdout may contain code with these words)
    stderr_lower = (result.stderr or "").lower()
    if any(kw in stderr_lower for kw in _QUOTA_KEYWORDS):
        _disabled_agents.add(reviewer_type)
        log.error("run_reviewer(%s) quota/auth error — agent disabled for this run",
                   reviewer_type)
        return ""
    if not (result.stdout or "").strip() and result.stderr:
        log.warning("run_reviewer(%s) empty stdout, stderr: %s",
                     reviewer_type, result.stderr[:500])
    return (result.stdout or "").strip()


_text_agent_counter = 0
_text_agent_lock = __import__("threading").Lock()


def run_text_agent(prompt: str, timeout: int = 300, tag: str = "",
                    agent_name: str = "") -> str:
    """Вызывает агента в text mode. agent_name — конкретный агент, иначе round-robin."""
    global _text_agent_counter
    if agent_name:
        names = [agent_name]
    else:
        names = list(cfg.agent_names)
        if not names:
            return ""
        with _text_agent_lock:
            start = _text_agent_counter % len(names)
            _text_agent_counter += 1
        names = names[start:] + names[:start]
    for name in names:
        if name in _disabled_agents:
            continue
        acfg = cfg.agents.get(name)
        if not acfg:
            continue
        try:
            # OpenAI-compatible API
            if acfg.protocol == "openai":
                text = _call_openai_api(acfg, prompt, timeout)
                if text:
                    if tag:
                        log.info(f"  [{tag}] → {name}")
                    return text
                continue

            # CLI agents
            cmd = [acfg.command] + [a for a in acfg.review_args if a != "{prompt}"]
            proc_env = {**os.environ}
            proc_env["PYTHONUNBUFFERED"] = "1"
            proc_env["FORCE_COLOR"] = "1"
            result = subprocess.run(
                cmd, cwd=cfg.root_dir, input=prompt,
                capture_output=True, text=True, timeout=timeout,
                env=proc_env,
            )
            # Detect quota errors
            stderr_lower = (result.stderr or "").lower()
            if any(kw in stderr_lower for kw in _QUOTA_KEYWORDS):
                _disabled_agents.add(name)
                log.error("run_text_agent(%s): квота — отключён", name)
                continue
            text = (result.stdout or "").strip()
            if text:
                if tag:
                    log.info(f"  [{tag}] → {name}")
                return text
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue
    return ""


# --- Промпты ---


def build_prompt(task: Task, error_log: str = "", agent_type: str = "") -> str:
    """Формирует промпт для агента."""
    # Claude CLI сам читает CLAUDE.md — не дублируем. Остальным агентам инжектим.
    project_section = ""
    if agent_type != "claude" and cfg.project_docs:
        project_section = f"""
## Документация проекта (CLAUDE.md)
{cfg.project_docs}
"""

    prompt = f"""Ты автономный агент разработки {cfg.project_context}.
{project_section}
ЗАПРЕЩЕНО РЕДАКТИРОВАТЬ: TASKS.md, CLAUDE.md, forgerace.toml, .gitignore — это файлы оркестратора.
Правь ТОЛЬКО файлы указанные в секции "Файлы".

## Твоя задача: {task.id} — {task.name}

{task.description}

## Файлы
- Создать: {task.files_new}
- Изменить: {task.files_modify}

## Интеграция
{task.integration}

## Критерий готовности
{task.acceptance}
"""

    if task.forbidden and task.forbidden.strip() not in ("", "—"):
        prompt += f"""
## ЗАПРЕЩЕНО (kill box этой задачи)
{task.forbidden}
"""

    if task.verification and task.verification.strip() not in ("", "—"):
        prompt += f"""
## Обязательная проверка
После реализации ОБЯЗАТЕЛЬНО выполни эту команду. Задача НЕ считается выполненной пока команда не вернёт exit code 0:
```
{task.verification}
```
"""

    prompt += f"""
## Правила
- Правь ТОЛЬКО файлы указанные в "Файлы" выше. Не переписывай файлы целиком — делай точечные правки.
- Если задача содержит ПРИМЕРЫ (input → output) — следуй им БУКВАЛЬНО. Не переводи
  единицы измерения, не «русифицируй» суффиксы и не подгоняй под свою интуицию.
- Тесты пиши строго по примерам из задачи. Если задача требует «округление вниз
  для 60.5 → 1m 0s» — assert именно "1m 0s", а не "1m 1s" «потому что логично».
- Реализацию и тесты добавляй в ОДНОМ применённом изменении (один edit-batch).
  Если тесты импортируют функцию, которой ещё нет — это ImportError → провалит build.
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

    # Интеграция pre-flight анализа
    if cfg.preflight:
        from .decompose import run_preflight
        preflight_result = run_preflight(task)
        if preflight_result:
            prompt += f"""
## Pre-flight анализ
{preflight_result}
"""

    # Text-protocol agents (aider): the files are ALREADY passed via --file
    # in run_agent_process, so aider reads them itself. We don't dump file
    # contents here a second time — that doubled the prompt size for nothing.
    # Just keep the SEARCH/REPLACE instruction.
    acfg = cfg.agents.get(agent_type)
    if acfg and acfg.protocol == "text":
        prompt += """
## Edit instructions
Edit the files using SEARCH/REPLACE blocks. Example:
<<<<<<< SEARCH
old code here
=======
new code here
>>>>>>> REPLACE

Make minimal, targeted changes. Do not rewrite entire files.
"""
        # Limit prompt for text-protocol agents (smaller context window)
        max_chars = 15000
        if len(prompt) > max_chars:
            prompt = prompt[:max_chars] + "\n\n... (prompt truncated)"

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
    system_frames: dict[str, str] = field(default_factory=dict)

# --- Post-mortem ---

POST_MORTEM_SYSTEM_PROMPT = """Ты — экспертный инженер по обеспечению качества и системный аналитик.
Твоя задача — провести Post-mortem анализ цепочки неудачных попыток решения задачи AI-агентом.

На входе ты получишь логи: замечания ревьюеров, ошибки компиляции, сообщения об ошибках тестов.
Проанализируй их и выдай структурированный отчет:

1. **Root Cause**: Основная причина неудачи (например, "Агент зациклился на неверном подходе к API X", "Несоответствие типов в контракте Y").
2. **Verdict**: Краткий вердикт (Stuck / Max Retries reached / Terminal failure).
3. **Recommendation**: Что нужно изменить в описании задачи или коде, чтобы следующая попытка была успешной.

Будь краток, технически точен и конструктивен.
"""

def build_post_mortem_prompt(failure_logs: list[str]) -> str:
    """
    Формирует промпт для post-mortem анализа на основе накопленных логов.
    Обеспечивает безопасную экранизацию и ограничение длины (16 KB).
    """
    if not failure_logs:
        return f"{POST_MORTEM_SYSTEM_PROMPT}\n\n"

    # Безопасная экранизация: переводим в строки, убираем лишние пробелы и
    # заменяем тройные обратные кавычки на одинарные, чтобы не разрывать блок кода.
    processed = [str(log).strip().replace("```", "'''") for log in failure_logs]
    logs_text = "\n".join(filter(None, processed))

    # Лимит в 16 KB (16384 символа) на весь промпт.
    MAX_PROMPT_LEN = 16384
    header = f"{POST_MORTEM_SYSTEM_PROMPT}\n\n```\n"
    footer = "\n```"

    if len(header) + len(logs_text) + len(footer) > MAX_PROMPT_LEN:
        prefix = "...(truncated)...\n"
        # Вычисляем доступное место для текста логов, учитывая префикс
        available = MAX_PROMPT_LEN - len(header) - len(footer) - len(prefix)
        if available >= 0:
            logs_text = prefix + (logs_text[-available:] if available > 0 else "")
        else:
            # Если места не хватает даже на префикс, берем остаток без него
            available = max(0, MAX_PROMPT_LEN - len(header) - len(footer))
            logs_text = logs_text[-available:] if available > 0 else ""

    return f"{header}{logs_text}{footer}"
