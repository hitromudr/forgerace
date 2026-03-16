"""Git worktree: создание и удаление рабочих директорий агентов."""

from pathlib import Path

from .config import cfg
from .utils import log, run_cmd


def create_worktree(agent_num: int, branch: str) -> Path:
    """Создаёт worktree для агента, возвращает путь."""
    cfg.agents_dir.mkdir(parents=True, exist_ok=True)
    agent_dir = cfg.agents_dir / f"agent-{agent_num}"

    if agent_dir.exists():
        log.warning(f"Worktree {agent_dir} уже существует, удаляю")
        run_cmd(["git", "worktree", "remove", str(agent_dir), "--force"], cwd=cfg.root_dir)

    # Удаляем старую ветку если есть — иначе worktree возьмёт устаревший код
    run_cmd(["git", "branch", "-D", branch], cwd=cfg.root_dir, check=False)

    # Создаём новую ветку от develop
    run_cmd(
        ["git", "worktree", "add", str(agent_dir), "-b", branch, cfg.dev_branch],
        cwd=cfg.root_dir,
    )

    # Сбрасываем грязные файлы
    run_cmd(["git", "checkout", "--", "orchestrator.py", "TASKS.md"], cwd=agent_dir, check=False)

    log.info(f"Worktree создан: {agent_dir} → {branch}")
    return agent_dir


def remove_worktree(agent_num: int):
    """Удаляет worktree агента."""
    agent_dir = cfg.agents_dir / f"agent-{agent_num}"
    if agent_dir.exists():
        run_cmd(["git", "worktree", "remove", str(agent_dir), "--force"], cwd=cfg.root_dir)
        log.info(f"Worktree удалён: {agent_dir}")


def cleanup_worktrees(results: list) -> None:
    """Удаляет worktree всех агентов из списка результатов."""
    for r in results:
        try:
            agent_num = int(r.workdir.name.split("-")[-1])
            remove_worktree(agent_num)
        except (ValueError, AttributeError):
            pass
