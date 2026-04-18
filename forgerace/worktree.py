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
    # Если не удалилась (залочена) — ещё раз prune и retry
    branch_check = run_cmd(["git", "branch", "--list", branch], cwd=cfg.root_dir, check=False)
    if branch_check.stdout.strip():
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

    # Remove orchestrator files from worktree — agents must not edit them
    for protected in ("TASKS.md", "forgerace.toml", "litellm_config.yaml",
                       "CLAUDE.md", ".gitignore"):
        pf = agent_dir / protected
        if pf.exists():
            pf.unlink()

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


def cleanup_worktrees(results: list) -> None:
    """Удаляет worktree всех агентов из списка результатов."""
    for r in results:
        try:
            agent_num = int(r.workdir.name.split("-")[-1])
            remove_worktree(agent_num)
        except (ValueError, AttributeError) as exc:
            log.warning("cleanup_worktrees: skipping result %r: %s", r, exc)
