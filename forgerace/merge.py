"""Мерж task-веток в develop (через detached worktree + update-ref)."""

import threading

from .config import cfg
from .utils import log, run_cmd

_merge_lock = threading.Lock()


def ensure_develop_branch():
    """Создаёт ветку develop от master если её нет."""
    result = run_cmd(
        ["git", "branch", "--list", cfg.dev_branch], cwd=cfg.root_dir, check=False,
    )
    if not result.stdout.strip():
        run_cmd(["git", "branch", cfg.dev_branch, "master"], cwd=cfg.root_dir)
        log.info(f"Создана ветка {cfg.dev_branch} от master")


def merge_to_develop(branch: str, task_id: str) -> bool:
    """Мержит task-ветку в develop без переключения веток.
    Использует detached worktree + update-ref."""
    with _merge_lock:
        merge_dir = cfg.agents_dir / "_merge_tmp"
        if merge_dir.exists():
            run_cmd(["git", "worktree", "remove", str(merge_dir), "--force"],
                    cwd=cfg.root_dir, check=False)

        # Detached HEAD на текущем коммите develop
        dev_sha = run_cmd(
            ["git", "rev-parse", cfg.dev_branch], cwd=cfg.root_dir, check=False,
        ).stdout.strip()
        run_cmd(
            ["git", "worktree", "add", "--detach", str(merge_dir), dev_sha],
            cwd=cfg.root_dir, check=False,
        )

        try:
            result = run_cmd(
                ["git", "merge", branch, "--no-ff", "-X", "theirs",
                 "-m", f"Merge {task_id}: {branch} → {cfg.dev_branch}"],
                cwd=merge_dir, check=False,
            )

            if result.returncode != 0:
                err_msg = (result.stderr or result.stdout or "неизвестная ошибка")[:500]
                log.error(f"  ✗ Merge {branch} → {cfg.dev_branch} провалился:\n{err_msg}")
                run_cmd(["git", "merge", "--abort"], cwd=merge_dir, check=False)
                return False

            # Обновляем ветку develop на новый merge-коммит
            merge_sha = run_cmd(
                ["git", "rev-parse", "HEAD"], cwd=merge_dir, check=False,
            ).stdout.strip()
            run_cmd(
                ["git", "update-ref", f"refs/heads/{cfg.dev_branch}", merge_sha],
                cwd=cfg.root_dir, check=False,
            )

            # Синхронизируем файлы из мержа (кроме TASKS.md и orchestrator.py)
            changed = run_cmd(
                ["git", "diff", "--name-only", f"{dev_sha}..{merge_sha}"],
                cwd=merge_dir, check=False,
            )
            for fname in (changed.stdout or "").strip().splitlines():
                fname = fname.strip()
                if fname and fname not in ("TASKS.md", "orchestrator.py"):
                    run_cmd(["git", "checkout", merge_sha, "--", fname],
                            cwd=cfg.root_dir, check=False)

            log.info(f"  ✓ {branch} вмержен в {cfg.dev_branch}")
            return True
        finally:
            run_cmd(["git", "worktree", "remove", str(merge_dir), "--force"],
                    cwd=cfg.root_dir, check=False)
