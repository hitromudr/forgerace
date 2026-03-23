"""Оценка сложности задач и авто-декомпозиция."""

import re
import subprocess

from .config import cfg, run_hint
from .tasks import Task, parse_tasks, task_paths
from .utils import log, is_valid_path

# Кэш: задачи, которые уже оценивались и не требуют декомпозиции
_assessed_tasks: set[str] = set()


def assess_and_maybe_decompose(task: Task) -> bool:
    """Оценивает сложность задачи через LLM. Если > max — разбивает.
    Возвращает True если задача была декомпозирована."""
    if task.id in _assessed_tasks:
        return False

    tasks_list = parse_tasks()
    max_num = max((int(re.match(r"TASK-(\d+)", t.id).group(1))
                   for t in tasks_list if re.match(r"TASK-(\d+)", t.id)), default=0)
    next_num = max_num + 1

    # Контекст: текущий код файлов задачи
    code_context = ""
    for files_str in (task.files_new, task.files_modify):
        if not files_str or files_str.strip() == "—":
            continue
        for f in files_str.split(","):
            f = re.sub(r"\s*\(.*?\)", "", f).strip()
            if f and is_valid_path(f):
                filepath = cfg.root_dir / f
                if filepath.exists() and filepath.is_file():
                    content = filepath.read_text(encoding="utf-8", errors="ignore")
                    code_context += f"\n--- {f} ---\n{content[:3000]}\n"

    # Информация о прошлых неудачах
    task_prefix = task.id.lower()
    past_failures = list(cfg.log_dir.glob(f"{task_prefix}-*-attempt*.log"))
    failure_context = ""
    if past_failures:
        failure_context = f"\n⚠ Задача ранее провалилась ({len(past_failures)} попыток). Учти это при оценке.\n"

    prompt = f"""Оцени сложность задачи для автономного агента (Claude/Gemini) в {cfg.project_context or "проекте"}.

## Задача
### {task.id}: {task.name}
- **Описание**: {task.description}
- **Файлы (новые)**: {task.files_new}
- **Файлы (modify)**: {task.files_modify}
- **Интеграция**: {task.integration}
- **Критерий готовности**: {task.acceptance}
{failure_context}
## Контекст (существующий код)
{code_context}

## Инструкция
Оцени сложность от 1 до 5:
- 1: тривиальная (добавить поле, переименовать)
- 2: простая (один файл, одна функция, понятная логика)
- 3: средняя (новый модуль с 2-3 функциями, нужно понять контекст)
- 4: сложная (затрагивает несколько модулей, синхронизация, переключение контекста)
- 5: очень сложная (архитектурные изменения, много взаимосвязей)

Если сложность > {cfg.max_task_complexity}, СРАЗУ разбей на 2-4 атомарные подзадачи.

## Формат ответа
Первая строка — СТРОГО:
COMPLEXITY: N

Если N <= {cfg.max_task_complexity} — больше ничего не пиши.

Если N > {cfg.max_task_complexity} — ниже выведи подзадачи в формате:

### TASK-XXX: Название
- **Статус**: open
- **Приоритет**: {task.priority}
- **Этап**: {task.stage}
- **Зависимости**: TASK-YYY или —
- **Файлы (новые)**: src/path/file.rs
- **Файлы (modify)**: — или путь
- **Интеграция**: что добавить при мерже
- **Описание**: что именно реализовать (конкретно)
- **Критерий готовности**: что должно работать
- **Дискуссия**: {task.discussion}
- **Агент**: —
- **Ветка**: —

Правила подзадач:
- Нумерация с TASK-{next_num:03d}
- Каждая подзадача — один файл или одна функциональность
- Зависимости между подзадачами указать
- Первая подзадача не должна зависеть от других новых
- Пиши на русском
"""

    log.info(f"  🔍 Оценка сложности {task.id}...")
    from .agents import run_text_agent

    # Пробуем всех агентов по очереди (round-robin) до успешной декомпозиции
    agent_names = cfg.agent_names
    complexity = None
    tasks_block = ""
    new_task_ids = []

    for attempt_num in range(len(agent_names)):
        output = run_text_agent(prompt, timeout=120, tag=task.id)
        if not output:
            continue

        complexity_match = re.search(r"COMPLEXITY:\s*(\d)", output)
        if not complexity_match:
            log.warning(f"  ⚠ [{task.id}] Агент не вернул COMPLEXITY, пробую следующего...")
            continue

        complexity = int(complexity_match.group(1))
        log.info(f"  📊 {task.id} сложность: {complexity}/5 (порог: {cfg.max_task_complexity})")

        if complexity <= cfg.max_task_complexity:
            _assessed_tasks.add(task.id)
            return False

        # Сложность высокая — проверяем наличие подзадач
        tasks_block = output[complexity_match.end():].strip()
        # Логируем обоснование (текст до первого ### TASK-)
        reasoning = tasks_block.split("### TASK-")[0].strip()
        if reasoning:
            log.info(f"  [{task.id}] Обоснование: {reasoning[:300]}")

        new_task_ids = re.findall(r"### (TASK-\d+):", tasks_block)
        if new_task_ids:
            break  # Успешная декомпозиция
        else:
            log.warning(f"  ⚠ [{task.id}] Сложность {complexity} но подзадачи не сгенерированы, пробую другого агента...")

    if not new_task_ids:
        if complexity and complexity > cfg.max_task_complexity:
            log.error(f"  ✗ [{task.id}] Ни один агент не смог декомпозировать (сложность {complexity}) — запускаю как есть")
        _assessed_tasks.add(task.id)
        return False

    # Сохраняем копию
    decompose_file = cfg.log_dir / f"{task.id.lower()}-decomposed.md"
    decompose_file.write_text(tasks_block + "\n", encoding="utf-8")

    # Принудительно подставляем дискуссию родителя в подзадачи
    if task.discussion and task.discussion != "—":
        tasks_block = re.sub(
            r"(\*\*Дискуссия\*\*:\s*).*",
            rf"\g<1>{task.discussion}",
            tasks_block,
        )

    # Вставляем в TASKS.md
    insert_tasks_into_tasksmd(tasks_block, task.id)
    if new_task_ids:
        last_subtask = new_task_ids[-1]
        content = cfg.tasks_file.read_text(encoding="utf-8")
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if "**Зависимости**" in line and task.id in line:
                if i > 0 and any(tid in lines[i - 1] for tid in new_task_ids):
                    continue
                lines[i] = line.replace(task.id, last_subtask)
                log.info(f"  🔗 Обновлена зависимость: {task.id} → {last_subtask} в строке {i + 1}")
        cfg.tasks_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Логируем
    log.info(f"  ✓ {task.id} декомпозирована на {len(new_task_ids)} подзадач:")
    new_tasks = parse_tasks()
    done_ids = {t.id for t in new_tasks if t.status == "done"}
    for tid in new_task_ids:
        t = next((t for t in new_tasks if t.id == tid), None)
        if t:
            deps_ready = all(d in done_ids for d in t.deps)
            status = "▶ готова" if deps_ready else f"⏸ ждёт {', '.join(t.deps)}"
            desc_short = (t.description or "")[:100].rstrip()
            log.info(f"    {t.id}: {t.name} [{status}]\n      → {desc_short}")

    # Коммитим
    from .utils import run_cmd
    run_cmd(["git", "add", "TASKS.md"], cwd=cfg.root_dir, check=False)
    run_cmd(
        ["git", "commit", "-m", f"decompose: {task.id} → {', '.join(new_task_ids)}"],
        cwd=cfg.root_dir, check=False,
    )

    return True


def insert_tasks_into_tasksmd(tasks_block: str, linked_task_id: str):
    """Вставляет сгенерированные задачи в TASKS.md."""
    content = cfg.tasks_file.read_text(encoding="utf-8")

    if linked_task_id:
        pattern = rf"(### {re.escape(linked_task_id)}: .+?)(?=\n### TASK-|\n---|\Z)"
        m = re.search(pattern, content, re.DOTALL)
        if m:
            content = content[:m.start()] + tasks_block.rstrip() + "\n" + content[m.end():]
        else:
            content = _append_before_footer(content, tasks_block)
    else:
        content = _append_before_footer(content, tasks_block)

    cfg.tasks_file.write_text(content, encoding="utf-8")


def _append_before_footer(content: str, tasks_block: str) -> str:
    """Вставляет блок задач перед секцией 'Карта параллелизма' или в конец."""
    marker = "## Карта параллелизма"
    idx = content.find(marker)
    if idx != -1:
        return content[:idx] + tasks_block.rstrip() + "\n\n---\n\n" + content[idx:]
    return content.rstrip() + "\n\n" + tasks_block.rstrip() + "\n"


def create_checkpoint_task(error_log: str):
    """Создаёт задачу-чекпоинт для починки make check."""
    tasks = parse_tasks()
    for t in tasks:
        if "make check" in (t.acceptance or "") and t.status != "done":
            print(f"  ℹ Задача {t.id} уже существует")
            print(f"\n    → {run_hint()}")
            return

    max_num = max((int(re.match(r"TASK-(\d+)", t.id).group(1))
                   for t in tasks if re.match(r"TASK-(\d+)", t.id)), default=0)
    next_id = f"TASK-{max_num + 1:03d}"

    check_cmd = cfg.check_command or "make check"
    error_short = error_log[:200].replace("\n", " ").strip()
    task_block = f"""### {next_id}: Чекпоинт — починить check
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 4
- **Зависимости**: —
- **Файлы (новые)**: Makefile
- **Файлы (modify)**: —
- **Интеграция**: —
- **Описание**: `{check_cmd}` не проходит. Ошибка: {error_short}. Создать Makefile с таргетом check если его нет, исправить все проблемы.
- **Критерий готовности**: `make check` проходит с exit code 0
- **Дискуссия**: —
- **Агент**: —
- **Ветка**: —"""

    insert_tasks_into_tasksmd(task_block, "")
    from .utils import run_cmd
    run_cmd(["git", "add", "TASKS.md"], cwd=cfg.root_dir, check=False)
    run_cmd(["git", "commit", "-m", f"auto: {next_id} — чекпоинт make check"],
            cwd=cfg.root_dir, check=False)
    print(f"  ✅ Создана {next_id}: Чекпоинт — починить make check")
    print(f"\n    → {run_hint()}")
