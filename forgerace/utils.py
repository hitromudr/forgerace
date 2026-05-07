"""Утилиты: run_cmd, slugify, валидация путей, логирование, ANSI-цвета."""

import logging
import os
import re
import select
import subprocess
import threading
import time
from pathlib import Path

from .config import cfg

# Limits for log/error truncation (used by pipeline)
MAX_LOG_PREVIEW_LENGTH = 2000
MAX_ERROR_LOG_LENGTH = 5000
MAX_SUMMARY_LENGTH = 500

__all__ = [
    "C", "R", "agent_color", "log", "setup_logging", "run_cmd",
    "slugify", "is_valid_path", "log_preflight", "parse_pytest_output",
    "find_short_test_summary", "strip_ansi", "format_duration",
    "format_bytes", "truncate_string", "clamp",
]

# --- ANSI цвета ---

C = {
    "reset":   "\033[0m",
    "bold":    "\033[1m",
    "dim":     "\033[2m",
    "cyan":    "\033[36m",
    "green":   "\033[32m",
    "yellow":  "\033[33m",
    "magenta": "\033[35m",
    "blue":    "\033[34m",
    "red":     "\033[31m",
    "white":   "\033[97m",
    "purple":  "\033[38;5;141m",
}

R = C["reset"]  # shortcut


def strip_ansi(text: str) -> str:
    """Удаляет ANSI-последовательности из текста."""
    if not text:
        return ""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def agent_color(name: str) -> str:
    """Возвращает ANSI-цвет для агента."""
    colors = {
        "claude": "cyan", "gemini": "blue", "qwen": "purple",
        "llama": "yellow", "qwen-api": "magenta", "devstral": "green",
        "gpt-oss": "cyan", "deepseek": "yellow", "techlead": "green",
        "aider-llama": "yellow", "aider-devstral": "green", "aider-qwen": "purple", "aider-gptoss": "cyan",
        "goose-llama": "red", "goose-devstral": "white",
    }
    return C.get(colors.get(name, "white"), C["white"])


# --- Логирование ---

log = logging.getLogger("forgerace")


class _ColorFormatter(logging.Formatter):
    """Цветной MUD-style форматтер для консоли."""
    LEVEL_COLORS = {
        logging.DEBUG:    C["dim"],
        logging.INFO:     "",
        logging.WARNING:  C["yellow"],
        logging.ERROR:    C["red"],
        logging.CRITICAL: C["red"] + C["bold"],
    }

    # Паттерны для автоматической MUD-раскраски
    _HIGHLIGHTS = [
        # --- Идентификаторы ---
        # [TASK-002/claude] → task yellow bold, agent colored
        (re.compile(r"\[(TASK-\d+)/([\w-]+)\]"),
         lambda m: f"[{C['yellow']}{C['bold']}{m.group(1)}{R}/{_agent_c(m.group(2))}{m.group(2)}{R}]"),
        # [TASK-002] → task yellow bold
        (re.compile(r"\[(TASK-\d+)\]"),
         lambda m: f"[{C['yellow']}{C['bold']}{m.group(1)}{R}]"),
        # TASK-002 без скобок (в тексте)
        (re.compile(r"\b(TASK-\d+)\b"),
         lambda m: f"{C['yellow']}{C['bold']}{m.group(1)}{R}"),

        # --- Агенты (в любом контексте) ---
        (re.compile(r"\b(aider-llama)\b", re.IGNORECASE),
         lambda m: f"{C['yellow']}{m.group(1)}{R}"),
        (re.compile(r"\b(aider-devstral)\b", re.IGNORECASE),
         lambda m: f"{C['green']}{m.group(1)}{R}"),
        (re.compile(r"\b(goose-llama)\b", re.IGNORECASE),
         lambda m: f"{C['red']}{m.group(1)}{R}"),
        (re.compile(r"\b(goose-devstral)\b", re.IGNORECASE),
         lambda m: f"{C['white']}{m.group(1)}{R}"),
        (re.compile(r"\b(claude)\b", re.IGNORECASE),
         lambda m: f"{C['cyan']}{m.group(1)}{R}"),
        (re.compile(r"\b(gemini)\b", re.IGNORECASE),
         lambda m: f"{C['blue']}{m.group(1)}{R}"),
        (re.compile(r"\b(qwen[-\w]*)\b", re.IGNORECASE),
         lambda m: f"{C['purple']}{m.group(1)}{R}"),
        (re.compile(r"\b(devstral)\b", re.IGNORECASE),
         lambda m: f"{C['green']}{m.group(1)}{R}"),
        (re.compile(r"\b(gpt-oss)\b", re.IGNORECASE),
         lambda m: f"{C['cyan']}{m.group(1)}{R}"),
        (re.compile(r"\b(llama)\b", re.IGNORECASE),
         lambda m: f"{C['yellow']}{m.group(1)}{R}"),

        # --- Обрамление ---
        # ═══ заголовки ═══ → yellow bold
        (re.compile(r"(═+.+═+)"),
         lambda m: f"{C['yellow']}{C['bold']}{m.group(1)}{R}"),

        # --- Вердикты и статусы ---
        (re.compile(r"\b(APPROVED)\b"),
         lambda m: f"{C['green']}{C['bold']}{m.group(1)}{R}"),
        (re.compile(r"\b(NEEDS_WORK)\b"),
         lambda m: f"{C['yellow']}{C['bold']}{m.group(1)}{R}"),
        (re.compile(r"\b(BLOCKED|FAILED|NO_EDIT_ABORT|CANCELLED|TIMEOUT|INACTIVITY_TIMEOUT|PROGRESS_TIMEOUT)\b"),
         lambda m: f"{C['red']}{C['bold']}{m.group(1)}{R}"),

        # --- Секции ревью (с опциональными ** markdown bold) ---
        (re.compile(r"^(\*{0,2}VERDICT:?\*{0,2})(.*)$", re.MULTILINE),
         lambda m: f"{C['yellow']}{C['bold']}{m.group(1)}{R}{m.group(2)}"),
        (re.compile(r"^(\*{0,2}COMMENTS:?\*{0,2})(.*)$", re.MULTILINE),
         lambda m: f"{C['blue']}{C['bold']}{m.group(1)}{R}{m.group(2)}"),
        (re.compile(r"^(\*{0,2}SUMMARY:?\*{0,2})(.*)$", re.MULTILINE),
         lambda m: f"{C['cyan']}{C['bold']}{m.group(1)}{R}{C['dim']}{m.group(2)}{R}"),

        # --- Markdown bold заголовки в тексте ревью (**Текст:**) ---
        (re.compile(r"\*\*([^*]*(?:блокирующ|критич|проблем|ошибк|баг)[^*]*?):\*\*", re.IGNORECASE),
         lambda m: f"{C['red']}{C['bold']}{m.group(1)}:{R}"),
        (re.compile(r"\*\*([^*]*(?:замечани|некритич|minor|нюанс)[^*]*?):\*\*", re.IGNORECASE),
         lambda m: f"{C['yellow']}{C['bold']}{m.group(1)}:{R}"),
        (re.compile(r"\*\*([^*]*(?:проверен|корректн|качеств|соответств|ОК|OK)[^*]*?):\*\*", re.IGNORECASE),
         lambda m: f"{C['green']}{C['bold']}{m.group(1)}:{R}"),
        (re.compile(r"\*\*([^*]+?):\*\*"),
         lambda m: f"{C['white']}{C['bold']}{m.group(1)}:{R}"),

        # --- Действия агентов (эмодзи + инструменты) ---
        (re.compile(r"(📖 Read) (.+)"),
         lambda m: f"{C['dim']}{m.group(1)} {m.group(2)}{R}"),
        (re.compile(r"(✏️  (?:Write|Edit)) (.+)"),
         lambda m: f"{C['green']}{m.group(1)} {m.group(2)}{R}"),
        (re.compile(r"(💻 Bash:) (.+)"),
         lambda m: f"{C['blue']}{m.group(1)}{R} {m.group(2)}"),
        (re.compile(r"(🔍 (?:Grep|Glob):) (.+)"),
         lambda m: f"{C['magenta']}{m.group(1)}{R} {C['dim']}{m.group(2)}{R}"),

        # --- Прогресс ---
        (re.compile(r"(⏳ \d+m\d+s) — (.+)"),
         lambda m: f"{C['dim']}{m.group(1)}{R} — {m.group(2)}"),
        (re.compile(r"(📊 .+turns.+)"),
         lambda m: f"{C['dim']}{m.group(1)}{R}"),

        # --- Успех ---
        (re.compile(r"(✅|✓)(.*)"),
         lambda m: f"{C['green']}{m.group(1)}{m.group(2)}{R}"),
        (re.compile(r"(🏆.*)"),
         lambda m: f"{C['green']}{C['bold']}{m.group(1)}{R}"),
        # → done → green
        (re.compile(r"→ (done)"),
         lambda m: f"→ {C['green']}{C['bold']}{m.group(1)}{R}"),

        # --- Ошибки ---
        (re.compile(r"(❌|✗)(.*)"),
         lambda m: f"{C['red']}{m.group(1)}{m.group(2)}{R}"),
        (re.compile(r"(⚠️)(.*)"),
         lambda m: f"{C['yellow']}{m.group(1)}{m.group(2)}{R}"),
        (re.compile(r"(⏰.*)"),
         lambda m: f"{C['red']}{m.group(1)}{R}"),

        # --- Ревью и доработка ---
        (re.compile(r"(📝 Code review .+)"),
         lambda m: f"{C['blue']}{m.group(1)}{R}"),
        (re.compile(r"(📝 Ревью|📝 Результат|📝 Финальное)(.*)"),
         lambda m: f"{C['blue']}{m.group(1)}{R}{m.group(2)}"),
        (re.compile(r"(📋 Ревью прогона.*)"),
         lambda m: f"{C['yellow']}{C['bold']}{m.group(1)}{R}"),
        (re.compile(r"(🔧 отправлен на доработку)"),
         lambda m: f"{C['yellow']}{m.group(1)}{R}"),
        (re.compile(r"(Доработка по замечаниям.*)"),
         lambda m: f"{C['yellow']}{m.group(1)}{R}"),

        # --- Декомпозиция ---
        (re.compile(r"(декомпозирована на \d+ подзадач.*)"),
         lambda m: f"{C['magenta']}{C['bold']}{m.group(1)}{R}"),
        (re.compile(r"(сложность: )(\d)/(\d)(.*)"),
         lambda m: f"{m.group(1)}{C['bold']}{m.group(2)}{R}/{m.group(3)}{m.group(4)}"),
        (re.compile(r"(Оценка сложности .+)"),
         lambda m: f"{C['dim']}{m.group(1)}{R}"),
        (re.compile(r"(Обновлена зависимость:.+)"),
         lambda m: f"{C['magenta']}{m.group(1)}{R}"),
        (re.compile(r"(→ .+)"),
         lambda m: f"{C['dim']}{m.group(1)}{R}"),
        (re.compile(r"(▶ готова|► готова)"),
         lambda m: f"{C['green']}{m.group(1)}{R}"),
        (re.compile(r"(⏸ ждёт .+)"),
         lambda m: f"{C['dim']}{m.group(1)}{R}"),

        # --- Инфраструктура ---
        (re.compile(r"(Worktree создан:.+)"),
         lambda m: f"{C['dim']}{m.group(1)}{R}"),
        (re.compile(r"(Worktree удалён:.+)"),
         lambda m: f"{C['dim']}{m.group(1)}{R}"),
        (re.compile(r"(Попытка \d+/\d+)"),
         lambda m: f"{C['dim']}{m.group(1)}{R}"),

        # --- Запуск ---
        (re.compile(r"(Запускаю:.+процессов)"),
         lambda m: f"{C['bold']}{m.group(1)}{R}"),
        (re.compile(r"(Ревьюер:)(.+)"),
         lambda m: f"{C['blue']}{m.group(1)}{R}{m.group(2)}"),
        (re.compile(r"(конкурентный|распределённ)"),
         lambda m: f"{C['cyan']}{m.group(1)}{R}"),
        (re.compile(r"(→ конкурентный .+)"),
         lambda m: f"{C['cyan']}{m.group(1)}{R}"),

        # --- Назначения ---
        (re.compile(r"\[причина: (.*?)\]"),
         lambda m: f"[{C['dim']}причина: {C['white']}{m.group(1)}{R}]"),

        # --- Стоимость ($) ---
        (re.compile(r"(\$[\d.]+)"),
         lambda m: f"{C['green']}{m.group(1)}{R}"),
    ]

    def format(self, record):
        level_color = self.LEVEL_COLORS.get(record.levelno, "")
        ts = self.formatTime(record, "%H:%M:%S")
        msg = record.getMessage()
        # Хайлайты применяем всегда
        for pattern, repl in self._HIGHLIGHTS:
            msg = pattern.sub(repl, msg)
        # WARNING/ERROR: оборачиваем весь msg в цвет уровня.
        # Чтобы хайлайты со своим сбросом (R) не обрывали цвет уровня,
        # заменяем R на R + level_color.
        if level_color:
            msg = msg.replace(R, R + level_color)
            msg = f"{level_color}{msg}{R}"
        return f"{C['dim']}{ts}{R} {msg}"


def _agent_c(name: str) -> str:
    """Быстрый доступ к цвету агента для regex-замен."""
    return agent_color(name)


def setup_logging(verbose: bool = False, rotate: bool = False):
    """Настраивает логирование в консоль и файл.

    `rotate=True` (для команды `run`) переносит прошлый orchestrator.log
    в orchestrator.log.prev и стартует новый — чтобы файл не разрастался
    до 60+MB между прогонами и monitor не упирался в его размер.
    """
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO

    # Консоль — цветной
    console = logging.StreamHandler()
    console.setFormatter(_ColorFormatter())

    log_path = cfg.log_dir / "orchestrator.log"
    if rotate and log_path.exists():
        try:
            prev = log_path.with_suffix(".log.prev")
            if prev.exists():
                prev.unlink()
            log_path.rename(prev)
        except OSError as e:
            # Don't crash startup over a rotation failure.
            print(f"  [warn] couldn't rotate orchestrator.log: {e}")

    # Файл — без цветов. mode='w' = свежий файл при rotate (старый уже
    # переименован) либо обычный append если rotate=False.
    fh = logging.FileHandler(log_path, mode="w" if rotate else "a")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    logging.basicConfig(level=level, handlers=[console, fh])


# --- Запуск команд ---

def _read_streams_threaded(proc: subprocess.Popen, timeout: float | None = None) -> tuple[bytes, bytes]:
    """Читает stdout и stderr процесса в отдельных потоках.

    Args:
        proc: Процесс, чьи потоки нужно прочитать.
        timeout: Максимальное время ожидания в секундах.

    Returns:
        Кортеж (stdout_data, stderr_data) с данными из потоков.
    """
    stdout_data = bytearray()
    stderr_data = bytearray()
    stdout_lock = threading.Lock()
    stderr_lock = threading.Lock()
    errors = []
    start_time = time.time()

    def _read_stdout():
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                with stdout_lock:
                    stdout_data.extend(chunk)
        except Exception as e:
            errors.append(("stdout", e))

    def _read_stderr():
        try:
            while True:
                chunk = proc.stderr.read(4096)
                if not chunk:
                    break
                with stderr_lock:
                    stderr_data.extend(chunk)
        except Exception as e:
            errors.append(("stderr", e))

    stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    if timeout is not None:
        remaining = timeout
        while remaining > 0:
            stdout_thread.join(0.1)
            stderr_thread.join(0.1)
            if not stdout_thread.is_alive() and not stderr_thread.is_alive():
                break
            remaining = timeout - (time.time() - start_time)
        else:
            if stdout_thread.is_alive() or stderr_thread.is_alive():
                raise subprocess.TimeoutExpired(proc.args, timeout)
    else:
        stdout_thread.join()
        stderr_thread.join()

    if errors:
        for stream, e in errors:
            log.warning(f"Ошибка чтения {stream}: {e}")

    return (bytes(stdout_data), bytes(stderr_data))


def _read_streams_nonblocking(proc: subprocess.Popen, timeout: float | None = None) -> tuple[bytes, bytes]:
    """Читает stdout и stderr процесса через select.select (Unix-only).

    Обеспечивает неблокирующее чтение, предотвращая deadlock при больших
    объемах вывода в обоих потоках одновременно.

    Args:
        proc: Процесс, чьи потоки нужно прочитать.
        timeout: Максимальное время ожидания в секундах.

    Returns:
        Кортеж (stdout_data, stderr_data) с данными из потоков.
    """
    import fcntl

    # Устанавливаем O_NONBLOCK для stdout и stderr
    for pipe in (proc.stdout, proc.stderr):
        if pipe:
            fd = pipe.fileno()
            fl = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

    stdout_data = bytearray()
    stderr_data = bytearray()

    # Соответствие FD -> (pipe, buffer)
    streams = {}
    if proc.stdout:
        streams[proc.stdout.fileno()] = (proc.stdout, stdout_data)
    if proc.stderr:
        streams[proc.stderr.fileno()] = (proc.stderr, stderr_data)

    fds = list(streams.keys())
    start_time = time.time()

    while fds:
        if timeout is not None and (time.time() - start_time) > timeout:
            raise subprocess.TimeoutExpired(proc.args, timeout)

        # Ждем готовности хотя бы одного дескриптора
        ready, _, _ = select.select(fds, [], [], 0.1)

        for fd in ready:
            try:
                # Читаем чанками по 64KB
                chunk = os.read(fd, 65536)
                if not chunk:
                    fds.remove(fd)
                else:
                    streams[fd][1].extend(chunk)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                fds.remove(fd)

        # Если select вышел по таймауту, проверяем, не завершился ли процесс
        if not ready and proc.poll() is not None:
            # Дочитываем всё, что осталось в буферах ОС
            for fd in list(fds):
                try:
                    while True:
                        chunk = os.read(fd, 65536)
                        if not chunk:
                            break
                        streams[fd][1].extend(chunk)
                except (BlockingIOError, OSError):
                    pass
                fds.remove(fd)
            break

    return bytes(stdout_data), bytes(stderr_data)

def run_cmd(
    cmd: list[str] | str,
    cwd: Path | None = None,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Запуск команды с потоковым чтением вывода для предотвращения deadlocks."""
    if cwd is None:
        cwd = cfg.root_dir
    
    use_shell = isinstance(cmd, str)
    cmd_str = cmd if use_shell else " ".join(cmd)
    log.debug(f"$ {cmd_str} (cwd={cwd})")

    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            shell=use_shell,
        )

        # Читаем потоки неблокирующим способом
        if os.name == "posix":
            stdout_bytes, stderr_bytes = _read_streams_nonblocking(proc, timeout=timeout)
        else:
            stdout_bytes, stderr_bytes = _read_streams_threaded(proc, timeout=timeout)

        # Дожидаемся завершения процесса (он уже должен быть завершен или закрыть потоки)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        # Логируем результат
        for line in stdout.splitlines():
            log.debug(line)
        for line in stderr.splitlines():
            log.debug(f"[stderr] {line}")

        result = subprocess.CompletedProcess(
            args=cmd,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
        )

        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr,
            )
        return result

    except subprocess.TimeoutExpired:
        if proc:
            proc.kill()
            proc.wait()
        log.error(f"Таймаут ({timeout}с): {cmd_str}")
        return subprocess.CompletedProcess(
            cmd, returncode=1, stdout="", stderr="TIMEOUT"
        )
    except Exception as e:
        if proc:
            proc.kill()
            proc.wait()
        log.error(f"Ошибка при выполнении команды: {e}")
        raise


# --- Slugify ---

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "j", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(text: str) -> str:
    """Транслитерация и slugify для имён веток."""
    result = ""
    for c in text.lower():
        if c in _TRANSLIT:
            result += _TRANSLIT[c]
        elif c.isascii() and (c.isalnum() or c == "-"):
            result += c
        elif c in " _":
            result += "-"
    result = re.sub(r"-+", "-", result).strip("-")
    return result[:40]


def is_valid_path(p: str) -> bool:
    """Проверяет, что строка похожа на путь к файлу, а не на описание."""
    if " " in p:
        return False
    if re.search(r"[а-яА-ЯёЁ]", p):
        return False
    return True

def log_preflight(summary: str):
    """Логирует результаты pre-flight анализа."""
    log.info(f"📋 Pre-flight analysis: {summary}")

def find_short_test_summary(output: str) -> str:
    """Находит и возвращает секцию 'short test summary info' из вывода pytest."""
    output = strip_ansi(output)
    if not output:
        return ""

    if "short test summary info" in output:
        parts = re.split(r"=+\s*short test summary info\s*=+", output)
        if len(parts) > 1:
            # Берем контент после заголовка до следующего разделителя ====
            summary_content = re.split(r"\n={3,}", parts[1])[0]
            return summary_content.strip()
    return ""


def format_duration(seconds: float) -> str:
    """
    Форматирует продолжительность в секундах в строку вида "Xh Ym Zs".

    Args:
        seconds: Продолжительность в секундах.

    Returns:
        Строка в формате "Xh Ym Zs", где X, Y, Z — целые числа.
        Нулевые компоненты опускаются, кроме случая, когда все компоненты равны нулю.
    """
    if seconds < 0:
        raise ValueError("Продолжительность не может быть отрицательной")

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int(seconds % 60)

    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    # Always include seconds part, even if it's 0
    parts.append(f"{seconds}s")

    return " ".join(parts)


def format_bytes(n: int) -> str:
    """
    Преобразует количество байт в человекочитаемую строку с суффиксами B, KB, MB, GB.
    Делитель — 1024, округление до одной десятой для KB/MB/GB.
    """
    if n < 0:
        raise ValueError("Размер не может быть отрицательным")
    units = ["B", "KB", "MB", "GB"]
    size = float(n)
    idx = 0
    while idx < len(units) - 1 and size >= 1024:
        size /= 1024
        idx += 1
    if idx == 0:
        return f"{int(size)} B"
    return f"{size:.1f} {units[idx]}"


def clamp(value, low, high):
    """
    Ограничивает значение отрезком [low, high].

    Args:
        value: Значение для ограничения (int или float)
        low: Нижняя граница
        high: Верхняя граница

    Returns:
        Значение, ограниченное отрезком [low, high]. Тип результата соответствует типу value.

    Raises:
        ValueError: Если low > high
    """
    if low > high:
        raise ValueError("low must be less than or equal to high")
    if value < low:
        return low
    if value > high:
        return high
    return value

def truncate_string(s: str, max_len: int) -> str:
    """
    Обрезает строку `s` до `max_len` символов, добавляя суффикс "..."
    если строка была урезана. Длина результата никогда не превышает `max_len`.

    Правила:
    - если max_len < 0 → ValueError
    - если max_len == 0 → ""
    - если max_len < 3 → возвращает первые max_len символов строки "..."
    - если len(s) <= max_len → возвращает s
    - иначе → s[:max_len-3] + "..."
    """
    if max_len < 0:
        raise ValueError("max_len cannot be negative")
    if max_len == 0:
        return ""
    if max_len < 3:
        return "..."[:max_len]
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."

def parse_pytest_output(output: str) -> list[str]:
    """
    Парсит вывод pytest и возвращает список полных имён упавших тестов.

    Ищет строки вида 'FAILED path/to/test.py::test_name' в кратком отчете
    или в подробном выводе. Учитывает ошибки при инициализации и сборе,
    а также префиксы pytest-xdist [gw0].
    """
    output = strip_ansi(output)
    if not output:
        return []

    failed_tests = []
    seen = set()

    # 2. Ищем секцию "short test summary info"
    summary_content = find_short_test_summary(output)
    summary_lines = summary_content.splitlines() if summary_content else []


    # Если нашли summary, парсим в первую очередь его
    # Если нет — парсим весь вывод line-by-line
    lines_to_parse = summary_lines if summary_lines else output.splitlines()

    for line in lines_to_parse:
        line = line.strip()
        if not line:
            continue

        parts = line.split()
        if not parts:
            continue

        # 1. Формат "FAILED/ERROR [tag] path::name"
        # Убираем возможные двоеточия (FAILED:, ERROR:)
        first_word = parts[0].rstrip(":")
        if first_word in ("FAILED", "ERROR") and len(parts) > 1:
            for p in parts[1:]:
                candidate = p.strip("() ")
                if "::" in candidate or candidate.endswith(".py"):
                    if candidate not in seen:
                        failed_tests.append(candidate)
                        seen.add(candidate)
                    break

        # 2. Формат "[tag] path::name FAILED/ERROR"
        else:
            # Ищем FAILED или ERROR в любом месте строки как отдельное слово (может быть с :)
            found_idx = -1
            for i, p in enumerate(parts):
                if p.rstrip(":") in ("FAILED", "ERROR"):
                    found_idx = i
                    break

            if found_idx >= 0:
                # Идентификатор должен быть ДО FAILED/ERROR
                for p in reversed(parts[:found_idx]):
                    candidate = p.strip("() ")
                    if "::" in candidate or candidate.endswith(".py"):
                        if candidate not in seen:
                            failed_tests.append(candidate)
                            seen.add(candidate)
                        break

    return failed_tests
