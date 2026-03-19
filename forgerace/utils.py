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
}

R = C["reset"]  # shortcut


def agent_color(name: str) -> str:
    """Возвращает ANSI-цвет для агента."""
    colors = {"claude": "cyan", "gemini": "magenta", "techlead": "green"}
    return C.get(colors.get(name, "white"), C["white"])


# --- Логирование ---

log = logging.getLogger("forgerace")


class _ColorFormatter(logging.Formatter):
    """Цветной форматтер для консоли."""
    LEVEL_COLORS = {
        logging.DEBUG:    C["dim"],
        logging.INFO:     "",
        logging.WARNING:  C["yellow"],
        logging.ERROR:    C["red"],
        logging.CRITICAL: C["red"] + C["bold"],
    }

    def format(self, record):
        color = self.LEVEL_COLORS.get(record.levelno, "")
        ts = self.formatTime(record, "%H:%M:%S")
        msg = record.getMessage()
        return f"{C['dim']}{ts}{R} {color}{msg}{R}"


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
