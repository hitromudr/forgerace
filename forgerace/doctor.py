"""Диагностика и автолечение среды ForgeRace."""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .config import cfg
from .utils import log, run_cmd, C, R

def validate_toml() -> tuple[bool, str]:
    """Проверяет валидность TOML-файла."""
    toml_path = cfg.root_dir / "forgerace.toml"
    if not toml_path.exists():
        return False, "forgerace.toml не найден"

    try:
        # Пробуем загрузить конфиг
        from .config import load_config
        load_config(config_path=toml_path)
        return True, "TOML валиден"
    except Exception as e:
        return False, f"Ошибка TOML: {str(e)[:200]}"

def check_project_brief() -> tuple[bool, str]:
    """Проверяет наличие и корректность PROJECT_BRIEF.md."""
    brief_path = cfg.root_dir / "PROJECT_BRIEF.md"
    if not brief_path.exists():
        return False, "PROJECT_BRIEF.md не найден"

    try:
        content = brief_path.read_text(encoding="utf-8")
        if len(content) < 100:
            return False, "PROJECT_BRIEF.md слишком короткий"
        return True, "PROJECT_BRIEF.md корректен"
    except Exception as e:
        return False, f"Ошибка чтения PROJECT_BRIEF.md: {str(e)[:200]}"

def check_directories() -> tuple[bool, str]:
    """Проверяет наличие необходимых директорий, создаёт отсутствующие."""
    created = []
    for dir_name in ["logs", "docs/discuss"]:
        path = cfg.root_dir / dir_name
        if not path.exists():
            try:
                path.mkdir(parents=True, exist_ok=True)
                created.append(dir_name)
            except OSError as e:
                return False, f"Не удалось создать {dir_name}: {e}"

    if created:
        return True, f"Созданы директории: {', '.join(created)}"
    return True, "Все директории существуют"

def check_discuss_context() -> tuple[bool, str]:
    """Проверяет наличие файла, указанного в discuss_context."""
    if not cfg.discuss_context:
        return True, "discuss_context не задан"

    context_path = cfg.root_dir / cfg.discuss_context
    if not context_path.exists():
        return False, f"Файл discuss_context не найден: {cfg.discuss_context}"
    return True, "discuss_context корректен"

def check_agents() -> tuple[bool, str]:
    """Проверяет доступность агентов (CLI — в PATH, API — пропускаем)."""
    missing = []
    for name, acfg in cfg.agents.items():
        if not acfg.enabled:
            continue
        # API agents (openai protocol) don't need a binary in PATH
        if acfg.protocol == "openai":
            continue
        if acfg.command and not shutil.which(acfg.command):
            missing.append(name)

    if missing:
        return False, f"CLI-агенты не найдены в PATH: {', '.join(missing)}"
    return True, "Все CLI-агенты доступны"

def check_litellm() -> tuple[bool, str]:
    """Проверяет доступность LiteLLM proxy."""
    # Проверяем, используют ли агенты localhost proxy
    proxy_url = ""
    for name, acfg in cfg.agents.items():
        if not acfg.enabled:
            continue
        for arg in acfg.args:
            if "127.0.0.1:4000" in arg or "localhost:4000" in arg:
                proxy_url = "http://127.0.0.1:4000"
                break
        if not proxy_url and acfg.env:
            for v in acfg.env.values():
                if "127.0.0.1:4000" in str(v) or "localhost:4000" in str(v):
                    proxy_url = "http://127.0.0.1:4000"
                    break
        if proxy_url:
            break

    if not proxy_url:
        return True, "LiteLLM proxy не требуется"

    # Проверяем, запущен ли proxy
    try:
        import subprocess as _sp
        _clean = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
        hc = _sp.run(["curl", "-s", "--connect-timeout", "2", "-o", "/dev/null", "-w", "%{http_code}",
                       f"{proxy_url}/health"],
                      capture_output=True, text=True, timeout=5, env=_clean)
        if hc.stdout.strip() in ("200", "401"):
            return True, "LiteLLM proxy доступен"
        return False, f"LiteLLM proxy не отвечает (код {hc.stdout.strip()})"
    except Exception as e:
        return False, f"Ошибка проверки LiteLLM: {str(e)[:200]}"

def cleanup_worktrees() -> tuple[bool, str]:
    """Очищает зависшие worktree."""
    try:
        # Удаляем все worktree в .agents
        agents_dir = cfg.root_dir / ".agents"
        if agents_dir.exists():
            for d in agents_dir.glob("agent-*"):
                if d.is_dir():
                    run_cmd(["git", "worktree", "remove", str(d), "--force"], cwd=cfg.root_dir, check=False)
                    shutil.rmtree(d, ignore_errors=True)
        return True, "Worktree очищены"
    except Exception as e:
        return False, f"Ошибка очистки worktree: {str(e)[:200]}"

def doctor() -> bool:
    """Запускает диагностику и автолечение среды."""
    log.info("═══ Запуск диагностики ForgeRace ═══")

    checks = [
        ("TOML", validate_toml),
        ("PROJECT_BRIEF.md", check_project_brief),
        ("Директории", check_directories),
        ("Discuss context", check_discuss_context),
        ("Агенты", check_agents),
        ("LiteLLM", check_litellm),
        ("Worktree", cleanup_worktrees),
    ]

    all_ok = True
    for name, check_fn in checks:
        ok, msg = check_fn()
        status = f"{C['green']}✓{R}" if ok else f"{C['red']}✗{R}"
        log.info(f"  {status} {name}: {msg}")
        if not ok:
            all_ok = False

    if all_ok:
        log.info(f"\n{C['green']}✅ Все проверки пройдены{R}")
    else:
        log.info(f"\n{C['red']}⚠️  Обнаружены проблемы{R}")

    return all_ok
