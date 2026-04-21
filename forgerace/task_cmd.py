from .config import cfg
from .utils import C, R
from .tasks import tasks_file_lock, _atomic_write, parse_tasks

def list_tasks():
    """Print a compact table of tasks: ID, status, agent, name."""
    tasks = parse_tasks()
    if not tasks:
        print("No tasks found.")
        return

    # Header
    print(f"{'ID':<10} {'STATUS':<20} {'AGENT':<15} NAME")
    for t in tasks:
        agent = t.agent if t.agent and t.agent != "—" else "-"
        print(f"{t.id:<10} {t.status:<20} {agent:<15} {t.name}")

def _next_task_id() -> str:
    """Return the next free TASK-XXX identifier."""
    with tasks_file_lock():
        tasks = parse_tasks()
        max_num = 0
        for t in tasks:
            try:
                num = int(t.id.split("-")[1])
                if num > max_num:
                    max_num = num
            except Exception:
                continue
        return f"TASK-{max_num + 1:03d}"

def _format_task_md(task_id: str, name: str, priority: str, depends: str,
                    files_new: str, files_modify: str, description: str,
                    discussion: str) -> str:
    """Create a markdown block for a new task."""
    return f"""### {task_id}: {name}
- **Статус**: open
- **Приоритет**: {priority}
- **Этап**: 1
- **Зависимости**: {depends}
- **Файлы (новые)**: {files_new}
- **Файлы (modify)**: {files_modify}
- **Интеграция**: —
- **Описание**: {description}
- **Запрещено**: —
- **Проверка**: —
- **Критерий готовности**: —
- **Дискуссия**: {discussion}
- **Агент**: —
- **Ветка**: —
"""

def add_task(name: str, priority: str = "P1", depends: str = "—",
             files_new: str = "—", files_modify: str = "—",
             description: str = "—", discussion: str = "—") -> None:
    """Add a new task to TASKS.md.

    Generates a new TASK-XXX id, formats the markdown block and
    appends it to the tasks file under a lock.
    """
    task_id = _next_task_id()
    block = _format_task_md(task_id, name, priority, depends,
                            files_new, files_modify, description, discussion)
    with tasks_file_lock():
        content = cfg.tasks_file.read_text(encoding="utf-8")
        new_content = content.rstrip() + "\n\n" + block.rstrip() + "\n"
        _atomic_write(cfg.tasks_file, new_content)
    print(f"{C['green']}Создана задача {task_id}: {name}{R}")
