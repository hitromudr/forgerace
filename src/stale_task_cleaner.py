"""Очистка зависших задач."""

import time
from pathlib import Path

from forgerace.config import cfg
from forgerace.tasks import parse_tasks, update_task_status
from forgerace.utils import log

def clean_stale_tasks():
    """Очищает зависшие задачи (in_progress без активности > 30 минут).

    Задача считается зависшей если:
    - Статус in_progress
    - Нет свежих логов (моложе 30 минут)
    - Нет активных worktree

    Возвращает количество очищенных задач.
    """
    tasks = parse_tasks()
    cleaned = 0

    for task in tasks:
        if not task.status.startswith("in_progress"):
            continue

        # Проверяем свежесть логов
        task_prefix = task.id.lower()
        log_files = list(cfg.log_dir.glob(f"{task_prefix}-*-attempt*.log"))
        if log_files:
            newest = max(f.stat().st_mtime for f in log_files)
            if time.time() - newest <= 1800:  # 30 минут
                continue

        # Проверяем активные worktree
        has_active_worktree = False
        for agent_dir in cfg.agents_dir.glob("agent-*"):
            if agent_dir.is_dir():
                # Проверяем, что worktree принадлежит этой задаче
                branch = None
                try:
                    from forgerace.utils import run_cmd
                    result = run_cmd(
                        ["git", "branch", "--show-current"],
                        cwd=agent_dir, check=False
                    )
                    branch = (result.stdout or "").strip()
                except Exception:
                    pass

                if branch and branch.startswith(f"task/{task.id.lower()}"):
                    has_active_worktree = True
                    break

        if not has_active_worktree:
            log.warning(f"[{task.id}] ⚠ in_progress > 30мин без активности → open")
            update_task_status(task.id, "open")
            cleaned += 1

    return cleaned
