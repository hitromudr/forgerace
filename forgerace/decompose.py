"""Оценка сложности задач и авто-декомпозиция."""

import re
from pathlib import Path

from .config import cfg, run_hint
from .tasks import Task, parse_tasks, _tasks_file_lock, _atomic_write, tasks_file_lock
from .utils import log, is_valid_path

# Кэш: задачи, которые уже оценивались (id -> complexity)
_task_complexity: dict[str, int] = {}


def validate_generated_tasks(tasks_block: str, existing_tasks: list[Task]) -> str:
    """Validate and fix generated tasks block before inserting into TASKS.md.

    Fixes:
    - Phantom dependencies (refs to non-existent TASK IDs)
    - Duplicate TASK IDs within the block
    - Suspicious file paths (common agent hallucinations)

    Returns cleaned tasks_block.
    """
    existing_ids = {t.id for t in existing_tasks}
    block_ids = re.findall(r"### (TASK-\d+):", tasks_block)

    # 1. Detect and fix duplicate IDs within the generated block
    seen_ids: set[str] = set()
    duplicates: list[str] = []
    for tid in block_ids:
        if tid in seen_ids:
            duplicates.append(tid)
        seen_ids.add(tid)

    if duplicates:
        # Renumber duplicates: find max number in block + existing
        all_nums = [int(re.match(r"TASK-(\d+)", t).group(1))
                     for t in (seen_ids | existing_ids) if re.match(r"TASK-(\d+)", t)]
        next_num = max(all_nums, default=0) + 1
        for dup_id in duplicates:
            # Replace only the second (and later) occurrence
            parts = tasks_block.split(f"### {dup_id}:")
            if len(parts) > 2:
                # Rebuild: keep first occurrence, renumber the rest
                rebuilt = parts[0] + f"### {dup_id}:" + parts[1]
                for extra_part in parts[2:]:
                    new_id = f"TASK-{next_num:03d}"
                    rebuilt += f"### {new_id}:" + extra_part
                    # Also fix internal refs to the duplicate
                    log.warning(f"  Duplicate {dup_id} renumbered to {new_id}")
                    next_num += 1
                tasks_block = rebuilt

    # 2. Validate dependencies: remove phantom refs
    all_valid_ids = existing_ids | set(re.findall(r"### (TASK-\d+):", tasks_block))

    def _fix_deps_line(match: re.Match) -> str:
        prefix = match.group(1)
        deps_str = match.group(2)
        dep_refs = re.findall(r"TASK-\d+", deps_str)
        if not dep_refs:
            return match.group(0)
        valid_deps = [d for d in dep_refs if d in all_valid_ids]
        phantom = [d for d in dep_refs if d not in all_valid_ids]
        for p in phantom:
            log.warning(f"  Phantom dependency removed: {p}")
        if valid_deps:
            return prefix + ", ".join(valid_deps)
        return prefix + "\u2014"

    tasks_block = re.sub(
        r"(\*\*Зависимости\*\*:\s*)(.+)",
        _fix_deps_line,
        tasks_block,
    )

    # 3. Warn about suspicious file paths (common agent hallucinations)
    for path_match in re.finditer(r"\*\*Файлы \((новые|modify)\)\*\*:\s*(.+)", tasks_block):
        paths_str = path_match.group(2).strip()
        if paths_str in ("\u2014", "-", ""):
            continue
        for fpath in paths_str.split(","):
            fpath = fpath.strip()
            if not fpath or fpath in ("\u2014", "-"):
                continue
            # Check for hallucinated paths: non-existent deep paths
            full_path = cfg.root_dir / fpath
            parent = full_path.parent
            if not parent.exists() and "/" in fpath:
                log.warning(f"  Suspicious path (parent dir missing): {fpath}")

    return tasks_block


def get_task_complexity(task_id: str) -> int:
    """Возвращает оцененную сложность задачи (или 0 если не оценивалась)."""
    return _task_complexity.get(task_id, 0)


def assess_and_maybe_decompose(task: Task, agent_name: str = "") -> bool:
    """Оценивает сложность задачи через LLM. Если > max — разбивает.
    agent_name — конкретный агент для оценки, иначе round-robin.
    Возвращает True если задача была декомпозирована."""
    if task.id in _task_complexity:
        task.complexity = _task_complexity[task.id]
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
- **Описание**: что именно реализовать (конкретно, с функциями и сигнатурами)
- **Файлы (новые)**: путь/к/файлу.py (относительно корня, НЕ src/)
- **Файлы (modify)**: путь (только если меняет существующий)
- **Зависимости**: TASK-YYY (только если реально зависит)
- **Дискуссия**: {task.discussion}

НЕ ПИШИ поля со значением "—". Пропускай пустые поля.

Правила подзадач:
- Нумерация с TASK-{next_num:03d}
- Каждая подзадача — один файл или одна функциональность
- Зависимости: ставь "—" для ВСЕХ подзадач. Подзадачи ВСЕГДА параллельны.
- Максимум 3 подзадачи
- ЗАПРЕЩЕНО: цепочки зависимостей между подзадачами
- Пиши на русском
"""

    log.info(f"  🔍 Оценка сложности {task.id}...")
    from .agents import run_text_agent

    # agent_name задан — используем его, иначе round-robin с fallback
    agent_names = [agent_name] if agent_name else list(cfg.agent_names)
    complexity = None
    tasks_block = ""
    new_task_ids = []

    for attempt_num in range(len(agent_names)):
        cur_agent = agent_names[attempt_num % len(agent_names)]
        output = run_text_agent(prompt, timeout=120, tag=task.id, agent_name=cur_agent)
        if not output:
            continue

        complexity_match = re.search(r"COMPLEXITY:\s*(\d)", output)
        if not complexity_match:
            log.warning(f"  ⚠ [{task.id}] Агент не вернул COMPLEXITY, пробую следующего...")
            continue

        complexity = int(complexity_match.group(1))
        log.info(f"  📊 {task.id} сложность: {complexity}/5 (порог: {cfg.max_task_complexity})")
        task.complexity = complexity
        _task_complexity[task.id] = complexity

        if complexity <= cfg.max_task_complexity:
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
        c = complexity or 3
        task.complexity = c
        _task_complexity[task.id] = c
        return False

    # Сохраняем копию
    decompose_file = cfg.log_dir / f"{task.id.lower()}-decomposed.md"
    decompose_file.write_text(tasks_block + "\n", encoding="utf-8")

    # Validate generated tasks (phantom deps, duplicates, suspicious paths)
    tasks_block = validate_generated_tasks(tasks_block, parse_tasks())

    # Принудительно подставляем дискуссию родителя в подзадачи
    disc = task.discussion if task.discussion and task.discussion != "—" else ""
    if disc:
        # Replace existing discussion field
        if re.search(r"\*\*Дискуссия\*\*:", tasks_block):
            tasks_block = re.sub(
                r"(\*\*Дискуссия\*\*:\s*).*",
                rf"\g<1>{disc}",
                tasks_block,
            )
        else:
            # Field missing — add after each task header's last field
            tasks_block = re.sub(
                r"(- \*\*Ветка\*\*:\s*.*)",
                rf"\g<1>\n- **Дискуссия**: {disc}",
                tasks_block,
            )
            # If no Ветка field either, add after Агент
            if "**Дискуссия**:" not in tasks_block:
                tasks_block = re.sub(
                    r"(- \*\*Агент\*\*:\s*.*)",
                    rf"\g<1>\n- **Дискуссия**: {disc}",
                    tasks_block,
                )

    # Перенумеровать и вставить под file lock (avoid duplicate IDs)
    with tasks_file_lock():
        current_tasks = parse_tasks()
        existing_ids = {t.id for t in current_tasks}
        actual_max = max((int(re.match(r"TASK-(\d+)", t.id).group(1))
                          for t in current_tasks if re.match(r"TASK-(\d+)", t.id)), default=0)
        # Renumber subtasks to avoid collisions
        old_ids = sorted(set(re.findall(r"TASK-(\d+)", tasks_block)), key=int)
        new_start = actual_max + 1
        id_map = {}
        for i, old_id in enumerate(old_ids):
            old_full = f"TASK-{old_id}"
            if old_full in existing_ids or old_full == task.id:
                new_full = f"TASK-{new_start + i:03d}"
                tasks_block = tasks_block.replace(old_full, new_full)
                id_map[old_full] = new_full
            else:
                id_map[old_full] = old_full
        new_task_ids = [id_map.get(f"TASK-{x}", f"TASK-{x}") for x in old_ids
                        if id_map.get(f"TASK-{x}", f"TASK-{x}") != task.id]

        # Insert directly (already under lock)
        content = cfg.tasks_file.read_text(encoding="utf-8")
        # Replace parent task or append
        pattern = rf"(### {re.escape(task.id)}: .+?)(?=\n### TASK-|\n---|\Z)"
        m = re.search(pattern, content, re.DOTALL)
        if m:
            content = content[:m.start()] + tasks_block.rstrip() + "\n" + content[m.end():]
        else:
            content = content.rstrip() + "\n\n" + tasks_block.rstrip() + "\n"
        _atomic_write(cfg.tasks_file, content)

    if new_task_ids:
        last_subtask = new_task_ids[-1]
        with tasks_file_lock():
            content = cfg.tasks_file.read_text(encoding="utf-8")
            lines = content.splitlines()
            for i, line in enumerate(lines):
                if "**Зависимости**" in line and task.id in line:
                    if i > 0 and any(tid in lines[i - 1] for tid in new_task_ids):
                        continue
                    lines[i] = line.replace(task.id, last_subtask)
                    log.info(f"  🔗 Обновлена зависимость: {task.id} → {last_subtask} в строке {i + 1}")
            _atomic_write(cfg.tasks_file, "\n".join(lines) + "\n")

    # Валидируем зависимости подзадач
    all_tasks = parse_tasks()
    all_ids = {t.id for t in all_tasks}
    for t in all_tasks:
        if t.id in new_task_ids:
            for dep in t.deps:
                if dep not in all_ids:
                    log.warning(f"  ⚠ {t.id}: зависимость {dep} не существует (призрак)")

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

    # TASKS.md обновлён на диске, но НЕ коммитим автоматически —
    # при --team это засирает develop, пользователь коммитит сам

    return True


def insert_tasks_into_tasksmd(tasks_block: str, linked_task_id: str):
    """Вставляет сгенерированные задачи в TASKS.md."""
    from .tasks import tasks_file_lock
    with tasks_file_lock():
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

        _atomic_write(cfg.tasks_file, content)


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
    print(f"  ✅ Создана {next_id}: Чекпоинт — починить make check")
    print(f"\n    → {run_hint()}")
