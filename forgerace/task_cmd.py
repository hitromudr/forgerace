import re
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

def _format_task_md(task_id: str, name: str, priority: str = "P1",
                    depends: str = "", files_new: str = "", files_modify: str = "",
                    description: str = "", discussion: str = "", **extra) -> str:
    """Create a markdown block for a new task. Only non-empty fields are written."""
    lines = [f"### {task_id}: {name}", "- **Статус**: open"]
    # Optional fields — only write if not empty/dash
    _optional = [
        ("Приоритет", priority),
        ("Зависимости", depends),
        ("Файлы (новые)", files_new),
        ("Файлы (modify)", files_modify),
        ("Описание", description),
        ("Дискуссия", discussion),
    ]
    for field_name, value in _optional:
        if value and value != "—":
            lines.append(f"- **{field_name}**: {value}")
    # Extra fields (integration, forbidden, verification, etc.)
    for field_name, value in extra.items():
        if value and value != "—":
            lines.append(f"- **{field_name}**: {value}")
    return "\n".join(lines) + "\n"

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

def edit_task(task_id: str, **fields) -> None:
    """Edit an existing task in TASKS.md.

    Args:
        task_id: ID of the task to edit (e.g., "TASK-036")
        **fields: field names and new values (e.g., status="done", priority="P1")

    Validates priority and status values. Updates the task block under a lock.
    """
    # Validate inputs
    if "priority" in fields and fields["priority"] not in ("P0", "P1", "P2", "P3"):
        print(f"{C['red']}Ошибка: приоритет должен быть P0, P1, P2 или P3{R}")
        return
    if "status" in fields and fields["status"] not in ("open", "done", "blocked", "skip"):
        print(f"{C['red']}Ошибка: статус должен быть open, done, blocked или skip{R}")
        return

    # Read the file
    with tasks_file_lock():
        content = cfg.tasks_file.read_text(encoding="utf-8")

        # Find the task block
        pattern = rf"(### {re.escape(task_id)}: .+?)(?=\n### TASK-|\n---|\Z)"
        match = re.search(pattern, content, re.DOTALL)
        if not match:
            print(f"{C['red']}Задача {task_id} не найдена{R}")
            return

        task_block = match.group(1)

        # Update fields
        for field_name, new_value in fields.items():
            # Build regex pattern for the field
            field_pattern = rf"- \*\*{re.escape(field_name)}\*\*: .*"
            # Replace the field value
            task_block = re.sub(
                field_pattern,
                f"- **{field_name}**: {new_value}",
                task_block,
                count=1
            )

        # Replace the task block in the content
        new_content = content[:match.start()] + task_block + content[match.end():]

        # Write back
        _atomic_write(cfg.tasks_file, new_content)

    print(f"{C['green']}Задача {task_id} обновлена{R}")
