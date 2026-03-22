"""Git worktree: создание и удаление рабочих директорий агентов."""

import shutil
from pathlib import Path

from .config import cfg
from .utils import log, run_cmd


def create_worktree(agent_num: int, branch: str) -> Path:
    """Создаёт worktree для агента, возвращает путь."""
    cfg.agents_dir.mkdir(parents=True, exist_ok=True)
    agent_dir = cfg.agents_dir / f"agent-{agent_num}"

    if agent_dir.exists():
        log.warning(f"Worktree {agent_dir} уже существует, удаляю")
        run_cmd(["git", "worktree", "remove", str(agent_dir), "--force"],
                cwd=cfg.root_dir, check=False)
        # Если git worktree remove не справился — удаляем руками
        if agent_dir.exists():
            shutil.rmtree(agent_dir, ignore_errors=True)

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

    # Сбрасываем грязные файлы (TASKS.md может быть modified)
    run_cmd(["git", "checkout", "--", "TASKS.md"], cwd=agent_dir, check=False)

    log.info(f"Worktree создан: {agent_dir} → {branch}")
    return agent_dir


def remove_worktree(agent_num: int):
    """Удаляет worktree агента."""
    agent_dir = cfg.agents_dir / f"agent-{agent_num}"
    if agent_dir.exists():
        run_cmd(["git", "worktree", "remove", str(agent_dir), "--force"],
                cwd=cfg.root_dir, check=False)
        # Fallback: если git не справился
        if agent_dir.exists():
            shutil.rmtree(agent_dir, ignore_errors=True)
        log.info(f"Worktree удалён: {agent_dir}")
    # Чистим мёртвые записи
    run_cmd(["git", "worktree", "prune"], cwd=cfg.root_dir, check=False)


def cleanup_worktrees(results: list) -> None:
    """Удаляет worktree всех агентов из списка результатов."""
    for r in results:
        try:
            agent_num = int(r.workdir.name.split("-")[-1])
            remove_worktree(agent_num)
        except (ValueError, AttributeError):
            pass
