"""Утилиты: run_cmd, slugify, валидация путей, логирование, ANSI-цвета."""

import logging
import re
import subprocess
from pathlib import Path

from .config import cfg

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


def agent_color(name: str) -> str:
    """Возвращает ANSI-цвет для агента."""
    colors = {"claude": "cyan", "gemini": "blue", "qwen": "purple", "techlead": "green"}
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
        (re.compile(r"\[(TASK-\d+)/(\w+)\]"),
         lambda m: f"[{C['yellow']}{C['bold']}{m.group(1)}{R}/{_agent_c(m.group(2))}{m.group(2)}{R}]"),
        # [TASK-002] → task yellow bold
        (re.compile(r"\[(TASK-\d+)\]"),
         lambda m: f"[{C['yellow']}{C['bold']}{m.group(1)}{R}]"),
        # TASK-002 без скобок (в тексте)
        (re.compile(r"\b(TASK-\d+)\b"),
         lambda m: f"{C['yellow']}{C['bold']}{m.group(1)}{R}"),

        # --- Агенты (в любом контексте) ---
        (re.compile(r"\b(claude)\b", re.IGNORECASE),
         lambda m: f"{C['cyan']}{m.group(1)}{R}"),
        (re.compile(r"\b(gemini)\b", re.IGNORECASE),
         lambda m: f"{C['blue']}{m.group(1)}{R}"),
        (re.compile(r"\b(qwen)\b", re.IGNORECASE),
         lambda m: f"{C['purple']}{m.group(1)}{R}"),

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
        # WARNING/ERROR: оборачиваем весь msg в цвет уровня
        if level_color:
            msg = f"{level_color}{msg}{R}"
        return f"{C['dim']}{ts}{R} {msg}"


def _agent_c(name: str) -> str:
    """Быстрый доступ к цвету агента для regex-замен."""
    return agent_color(name)


def setup_logging(verbose: bool = False):
    """Настраивает логирование в консоль и файл."""
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO

    # Консоль — цветной
    console = logging.StreamHandler()
    console.setFormatter(_ColorFormatter())

    # Файл — без цветов
    fh = logging.FileHandler(cfg.log_dir / "orchestrator.log")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    logging.basicConfig(level=level, handlers=[console, fh])


# --- Запуск команд ---

def run_cmd(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Запуск команды с логированием."""
    if cwd is None:
        cwd = cfg.root_dir
    log.debug(f"$ {' '.join(cmd)} (cwd={cwd})")
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr,
            )
        return result
    except subprocess.TimeoutExpired:
        log.error(f"Таймаут ({timeout}с): {' '.join(cmd)}")
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="TIMEOUT")


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
