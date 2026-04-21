"""Git worktree: создание и удаление рабочих директорий агентов."""
import os
import shutil
import sys as _sys
import threading
from pathlib import Path

from .config import cfg
from .utils import log, run_cmd
def _rmtree_onerror(func, path, exc_info):
    """Log rmtree failures instead of silently ignoring them (Python <3.12)."""
    log.warning("rmtree failed: %s(%s): %s", func.__name__, path, exc_info[1])


def _rmtree_onexc(func, path, exc):
    """Log rmtree failures instead of silently ignoring them (Python 3.12+)."""
    log.warning("rmtree failed: %s(%s): %s", func.__name__, path, exc)


_rmtree_kwargs = (
    {"onexc": _rmtree_onexc} if _sys.version_info >= (3, 12)
    else {"onerror": _rmtree_onerror}
)

_worktree_lock = threading.Lock()


def create_worktree(agent_num: int, branch: str) -> Path:
    """Создаёт worktree для агента, возвращает путь. Thread-safe."""
    with _worktree_lock:
        return _create_worktree_impl(agent_num, branch)


def _create_worktree_impl(agent_num: int, branch: str) -> Path:
    """Внутренняя реализация — вызывается под lock."""
    cfg.agents_dir.mkdir(parents=True, exist_ok=True)
    # PID in dir name prevents collisions between parallel forgerace processes
    agent_dir = cfg.agents_dir / f"agent-{os.getpid()}-{agent_num}"

    if agent_dir.exists():
        log.warning(f"Worktree {agent_dir} уже существует, удаляю")
        run_cmd(["git", "worktree", "remove", str(agent_dir), "--force"],
                cwd=cfg.root_dir, check=False)
        # Если git worktree remove не справился — удаляем руками
        if agent_dir.exists():
            shutil.rmtree(agent_dir, **_rmtree_kwargs)

    # Чистим worktree list от мёртвых записей
    run_cmd(["git", "worktree", "prune"], cwd=cfg.root_dir, check=False)

    # Удаляем старую ветку — может быть залочена мёртвым worktree
    run_cmd(["git", "branch", "-D", branch], cwd=cfg.root_dir, check=False)
    # Если не удалилась — kill all worktrees referencing it, prune, retry
    branch_check = run_cmd(["git", "branch", "--list", branch], cwd=cfg.root_dir, check=False)
    if branch_check.stdout.strip():
        # Find and remove any worktree using this branch
        wt_list = run_cmd(["git", "worktree", "list", "--porcelain"], cwd=cfg.root_dir, check=False)
        for line in (wt_list.stdout or "").split("\n"):
            if line.startswith("worktree ") and branch in (wt_list.stdout or ""):
                wt_path = line.split(" ", 1)[1]
                run_cmd(["git", "worktree", "remove", wt_path, "--force"],
                        cwd=cfg.root_dir, check=False)
        run_cmd(["git", "worktree", "prune"], cwd=cfg.root_dir, check=False)
        run_cmd(["git", "branch", "-D", branch], cwd=cfg.root_dir, check=False)

    # Создаём новую ветку от develop
    result = run_cmd(
        ["git", "worktree", "add", str(agent_dir), "-b", branch, cfg.dev_branch],
        cwd=cfg.root_dir, check=False,
    )
    if result.returncode != 0:
        log.error(f"Не удалось создать worktree: {result.stderr}")
        raise RuntimeError(f"git worktree add failed: {result.stderr}")

    # Protect orchestrator files: mark as assume-unchanged so git ignores
    # agent modifications, then replace with empty/minimal content
    _PROTECTED = ("TASKS.md", "forgerace.toml", "litellm_config.yaml",
                  "CLAUDE.md", ".gitignore")
    for protected in _PROTECTED:
        pf = agent_dir / protected
        # Mark assume-unchanged — git won't track changes even if agent modifies
        run_cmd(["git", "update-index", "--assume-unchanged", protected],
                cwd=agent_dir, check=False)
        # Replace content with stub so agent can't extract useful data
        if pf.exists():
            pf.write_text(f"# {protected} — protected by orchestrator, do not edit\n")

    log.info(f"Worktree создан: {agent_dir} → {branch}")
    return agent_dir


def remove_worktree(agent_num: int):
    """Удаляет worktree агента."""
    # PID in dir name prevents collisions between parallel forgerace processes
    agent_dir = cfg.agents_dir / f"agent-{os.getpid()}-{agent_num}"
    if agent_dir.exists():
        run_cmd(["git", "worktree", "remove", str(agent_dir), "--force"],
                cwd=cfg.root_dir, check=False)
        # Fallback: если git не справился
        if agent_dir.exists():
            shutil.rmtree(agent_dir, **_rmtree_kwargs)
        log.info(f"Worktree удалён: {agent_dir}")
    # Чистим мёртвые записи
    run_cmd(["git", "worktree", "prune"], cwd=cfg.root_dir, check=False)


def cleanup_worktrees(results: list, keep_failed: bool = True) -> None:
    """Удаляет worktree всех агентов из списка результатов.

    If keep_failed=True (default), worktrees of agents that failed
    (success=False or blocked) are preserved for debugging.
    """
    for r in results:
        try:
            if keep_failed and hasattr(r, "success") and r.success is False:
                log.info(f"  Worktree сохранён для дебага: {r.workdir}")
                continue
            agent_num = int(r.workdir.name.split("-")[-1])
            remove_worktree(agent_num)
        except (ValueError, AttributeError) as exc:
            log.warning("cleanup_worktrees: skipping result %r: %s", r, exc)
