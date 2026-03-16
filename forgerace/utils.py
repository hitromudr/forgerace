"""Утилиты: run_cmd, slugify, валидация путей, логирование."""

import logging
import re
import subprocess
from pathlib import Path

from .config import cfg

# --- Логирование ---

log = logging.getLogger("forgerace")


def setup_logging(verbose: bool = False):
    """Настраивает логирование в консоль и файл."""
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(cfg.log_dir / "orchestrator.log"),
        ],
    )


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
