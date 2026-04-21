"""Модель задачи, парсер TASKS.md, обновление статусов."""

import os
import re
import tempfile
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .config import cfg
from .utils import log, slugify, is_valid_path

# Legacy threading lock — kept for backward compat but file lock is primary.
_tasks_file_lock = threading.Lock()


import fcntl

class _FileLock:
    """Cross-process file lock using fcntl.flock."""
    def __init__(self, path):
        self._path = path
        self._fd = None
    def __enter__(self):
        self._fd = open(self._path, "a+")
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self
    def __exit__(self, *args):
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        self._fd.close()


def tasks_file_lock():
    """Combined thread + file lock for TASKS.md operations."""
    return _FileLock(str(cfg.tasks_file) + ".lock")


# --- Модель задачи ---

@dataclass
class Task:
    id: str             # TASK-001
    name: str           # Аллокатор физических фреймов
    status: str         # open / claimed:agent / in_progress:agent / review:agent / done
    priority: str       # P1 / P2 / P3
    stage: str          # 2
    deps: list[str]     # [TASK-001, ...]
    files_new: str      # src/memory/frame_allocator.rs
    files_modify: str   # src/memory.rs
    integration: str    # pub mod frame_allocator в memory.rs
    description: str    # полное описание
    forbidden: str      # антипаттерны / kill box для этой задачи
    verification: str   # команда верификации (агент ОБЯЗАН запустить)
    acceptance: str     # критерий готовности
    agent: str          # claude / gemini / —
    branch: str         # task/001-frame-allocator / —
    discussion: str     # 001-scheduler-design / —
    raw_section: str    # исходный markdown-блок
    complexity: int = 0 # оценка сложности (в памяти)
    rework_count: int = 0
    last_attempts: list = field(default_factory=list)


# --- Парсер TASKS.md ---

def parse_tasks(path: Path | None = None) -> list[Task]:
    """Парсит TASKS.md, возвращает список задач."""
    if path is None:
        path = cfg.tasks_file
    text = path.read_text(encoding="utf-8")
    pattern = r"(### (TASK-\d+): .+?)(?=\n### TASK-|\n---|\Z)"
    matches = re.findall(pattern, text, re.DOTALL)

    tasks = []
    for raw, task_id in matches:
        tasks.append(Task(
            id=task_id,
            name=_field(raw, r"### TASK-\d+: (.+)"),
            status=_field(raw, r"\*\*Статус\*\*:\s*(.+)"),
            priority=_field(raw, r"\*\*Приоритет\*\*:\s*(.+)"),
            stage=_field(raw, r"\*\*Этап\*\*:\s*(.+)"),
            deps=_parse_deps(_field(raw, r"\*\*Зависимости\*\*:\s*(.+)")),
            files_new=_field(raw, r"\*\*Файлы \(новые\)\*\*:\s*(.+)"),
            files_modify=_field(raw, r"\*\*Файлы \(modify\)\*\*:\s*(.+)"),
            integration=_field(raw, r"\*\*Интеграция\*\*:\s*(.+)"),
            description=_field(raw, r"\*\*Описание\*\*:\s*(.+)"),
            forbidden=_field(raw, r"\*\*Запрещено\*\*:\s*(.+)"),
            verification=_field(raw, r"\*\*Проверка\*\*:\s*(.+)"),
            acceptance=_field(raw, r"\*\*Критерий готовности\*\*:\s*(.+)"),
            agent=_field(raw, r"\*\*Агент\*\*:\s*(.+)"),
            branch=_field(raw, r"\*\*Ветка\*\*:\s*(.+)"),
            discussion=_field(raw, r"\*\*Дискуссия\*\*:\s*(.+)"),
            raw_section=raw.strip(),
            rework_count=int(_field(raw, r"\*\*Переделки\*\*:\s*(\d+)") or "0"),
            last_attempts=_parse_last_attempts(raw),
        ))
    return _deduplicate_tasks(tasks)


def _field(text: str, pattern: str) -> str:
    m = re.search(pattern, text)
    return m.group(1).strip() if m else ""

def _parse_last_attempts(text: str) -> list[dict]:
    """Парсит поле last_attempts из Markdown.
    Ожидается формат: **Последние попытки**: [JSON-массив]
    Возвращает список словарей или пустой список при ошибке.
    """
    pattern = r"\*\*Последние попытки\*\*:\s*(.+)"
    m = re.search(pattern, text)
    if not m:
        return []
    
    raw = m.group(1).strip()
    if not raw or raw in ("—", "[]"):
        return []
    
    try:
        # Пытаемся распарсить как JSON
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        return []
    except json.JSONDecodeError:
        # Fallback: если JSON битый, возвращаем пустой список, чтобы не ломать парсинг
        log.warning(f"Не удалось распарсить last_attempts как JSON: {raw[:50]}...")
        return []


def _parse_deps(deps_str: str) -> list[str]:
    if not deps_str or deps_str == "—":
        return []
    return [d.strip() for d in re.findall(r"TASK-\d+", deps_str)]


# --- Atomic write helper ---

def _atomic_write(path: Path, content: str):
    """Write content to path atomically via tempfile + os.rename."""
    dir_path = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.rename(tmp_path, str(path))
    except BaseException:
        # Clean up temp file on any failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# --- Обновление статусов ---

def update_task_status(task_id: str, new_status: str, agent: str = "", branch: str = ""):
    """Обновляет статус задачи в TASKS.md (в основном репозитории)."""
    with tasks_file_lock():
        tasks_file = cfg.tasks_file
        lines = tasks_file.read_text(encoding="utf-8").splitlines()
        in_task = False
        result = []

        for line in lines:
            if line.startswith(f"### {task_id}:"):
                in_task = True
            elif line.startswith("### TASK-"):
                in_task = False

            if in_task:
                if line.startswith("- **Статус**:"):
                    line = f"- **Статус**: {new_status}"
                elif agent and line.startswith("- **Агент**:"):
                    line = f"- **Агент**: {agent}"
                elif branch and line.startswith("- **Ветка**:"):
                    line = f"- **Ветка**: {branch}"

            result.append(line)

        _atomic_write(tasks_file, "\n".join(result) + "\n")


def link_task_discussion(task_id: str, topic: str):
    """Прописывает дискуссию в TASKS.md для задачи."""
    with tasks_file_lock():
        tasks_file = cfg.tasks_file
        lines = tasks_file.read_text(encoding="utf-8").splitlines()
        in_task = False
        result = []

        for line in lines:
            if line.startswith(f"### {task_id}:"):
                in_task = True
            elif line.startswith("### TASK-"):
                in_task = False

            if in_task and line.startswith("- **Дискуссия**:"):
                line = f"- **Дискуссия**: {topic}"

            result.append(line)

        _atomic_write(tasks_file, "\n".join(result) + "\n")


# --- Архивирование ---

def archive_done_tasks() -> int:
    """Move done/skip tasks from TASKS.md to done/TASKS_YYYY-MM-DD.md. Returns count archived."""
    from datetime import date

    with tasks_file_lock():
        content = cfg.tasks_file.read_text(encoding="utf-8")
        # Split into header + task blocks
        blocks = re.split(r"(?=\n### TASK-)", content)
        header = blocks[0]
        task_blocks = blocks[1:] if len(blocks) > 1 else []

        keep = []
        archive = []
        for block in task_blocks:
            status_match = re.search(r"\*\*Статус\*\*:\s*(\S+)", block)
            status = status_match.group(1) if status_match else ""
            if status in ("done", "skip"):
                archive.append(block)
            else:
                keep.append(block)

        if not archive:
            return 0

        # Write archive file
        done_dir = cfg.root_dir / "done"
        done_dir.mkdir(exist_ok=True)
        archive_file = done_dir / f"TASKS_{date.today().isoformat()}.md"

        # Append to existing archive for today (multiple runs per day)
        existing = ""
        if archive_file.exists():
            existing = archive_file.read_text(encoding="utf-8")
        if not existing:
            existing = f"# Archived tasks ({date.today().isoformat()})\n"

        archive_content = existing.rstrip() + "\n" + "".join(archive).rstrip() + "\n"
        archive_file.write_text(archive_content, encoding="utf-8")

        # Rewrite TASKS.md without archived tasks
        new_content = header + "".join(keep)
        _atomic_write(cfg.tasks_file, new_content.rstrip() + "\n")

    return len(archive)


# --- Топик дискуссии ---

def translate_slug(name: str) -> str:
    """Транслитерация названия в kebab-case slug для веток."""
    slug = slugify(name)
    # Обрезаем до разумной длины (макс 6 слов, 50 символов)
    parts = slug.split("-")
    if len(parts) > 6:
        slug = "-".join(parts[:6])
    return slug[:50]


def topic_for_task(task: Task) -> str:
    """Генерирует имя топика дискуссии из задачи."""
    num = task.id.replace("TASK-", "").lstrip("0")
    slug = translate_slug(task.name)
    return f"{num}-{slug}"


# --- Проверка утверждённости ---

def is_task_approved(task: Task) -> bool:
    """Проверяет, утверждена ли задача.

    Утверждена если:
    - Нет привязанной дискуссии (discussion: — или пустое) → не требует обсуждения
    - Дискуссия есть и содержит РЕЗОЛЮЦИЮ → обсуждена и утверждена
    - acceptance содержит "make check" → автоматическая проверка
    - Дискуссия привязана как "future" → ещё не создана, считаем утверждённой
    """
    if "make check" in (task.acceptance or ""):
        return True
    if not task.discussion or task.discussion in ("—", "future"):
        return True
    # Skip garbage discussion values (descriptions leaked into field)
    if len(task.discussion) > 80 or " " in task.discussion or "**" in task.discussion:
        return True
    filepath = cfg.discuss_dir / f"{task.discussion}.md"
    if not filepath.exists():
        return True  # discussion file missing — don't block execution
    text = filepath.read_text(encoding="utf-8")
    return "РЕЗОЛЮЦИЯ" in text


# --- Поиск задач ---

def find_ready_tasks(tasks: list[Task]) -> list[Task]:
    """Находит задачи, которые можно взять (open + зависимости done).
    Призрачные зависимости (ссылки на несуществующие задачи) игнорируются с предупреждением."""
    all_ids = {t.id for t in tasks}
    done_ids = {t.id for t in tasks if t.status in ("done", "skip")}
    ready = []
    for t in tasks:
        if t.status != "open":
            continue
        real_deps = []
        for d in t.deps:
            if d not in all_ids:
                log.warning(f"⚠ {t.id}: зависимость {d} не существует (призрак), игнорируется")
            else:
                real_deps.append(d)
        if all(d in done_ids for d in real_deps):
            ready.append(t)
    # Priority: P1 < P2, then tasks that unblock more others go first
    blocked_by = {}  # task_id → how many tasks depend on it
    for t in tasks:
        if t.status == "open":
            for d in t.deps:
                if d in all_ids:
                    blocked_by[d] = blocked_by.get(d, 0) + 1
    return sorted(ready, key=lambda t: (t.priority, -blocked_by.get(t.id, 0)))


def _deduplicate_tasks(tasks: list[Task]) -> list[Task]:
    """Keep only first occurrence of each task ID."""
    seen = set()
    result = []
    for t in tasks:
        if t.id not in seen:
            seen.add(t.id)
            result.append(t)
    return result


def find_retryable_tasks(tasks: list[Task]) -> list[Task]:
    """Находит задачи для повторного запуска (review, blocked, in_progress)."""
    retryable = []
    for t in tasks:
        status_base = t.status.split(":")[0]
        if status_base in ("review", "blocked", "in_progress"):
            retryable.append(t)
    return sorted(retryable, key=lambda t: t.priority)


# --- Пути файлов задачи ---

def task_paths(task: Task) -> list[str]:
    """Возвращает список путей из задачи (новые + modify) + src/ как fallback."""
    paths = set()
    for files_str in (task.files_new, task.files_modify):
        if files_str and files_str.strip() != "—":
            for f in files_str.split(","):
                f = re.sub(r"\s*\\(.*?\\)", "", f).strip()
                if f and is_valid_path(f):
                    paths.add(f)
    if not paths:
        paths.add("src/")
    return sorted(paths)


# --- Граф зависимостей ---

class DependencyGraph:
    """Граф зависимостей задач: строится из deps полей, поддерживает
    транзитивные зависимые, детекцию циклов и приоритизацию."""

    def __init__(self, tasks: list[Task]):
        # depends_on: task_id -> set of task_ids it depends on
        self.depends_on: dict[str, set[str]] = {}
        # dependents: task_id -> set of task_ids that depend on it
        self.dependents: dict[str, set[str]] = {}
        self._all_ids: set[str] = set()

        for t in tasks:
            self._all_ids.add(t.id)
            self.depends_on.setdefault(t.id, set())
            self.dependents.setdefault(t.id, set())

        for t in tasks:
            for dep in t.deps:
                if dep in self._all_ids:
                    self.depends_on[t.id].add(dep)
                    self.dependents[dep].add(t.id)
                else:
                    log.warning(f"⚠ {t.id}: зависимость {dep} не существует (призрак)")

    def get_transitive_dependents(self, task_id: str) -> set[str]:
        """Все задачи, которые (транзитивно) зависят от task_id."""
        visited: set[str] = set()
        stack = [task_id]
        while stack:
            node = stack.pop()
            for dep in self.dependents.get(node, ()):
                if dep not in visited:
                    visited.add(dep)
                    stack.append(dep)
        return visited

    def detect_cycles(self) -> list[str] | None:
        """Топологическая сортировка (Kahn's algorithm).
        Возвращает None если циклов нет, иначе — список task_id в цикле."""
        in_degree: dict[str, int] = {tid: 0 for tid in self._all_ids}
        for tid, deps in self.depends_on.items():
            in_degree[tid] = len(deps)

        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        sorted_count = 0

        while queue:
            node = queue.pop()
            sorted_count += 1
            for dependent in self.dependents.get(node, ()):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if sorted_count == len(self._all_ids):
            return None

        # Задачи, оставшиеся с in_degree > 0, участвуют в цикле
        return sorted(tid for tid, deg in in_degree.items() if deg > 0)

    def topological_order(self) -> list[str]:
        """Возвращает задачи в топологическом порядке.
        Raises ValueError при наличии циклов."""
        cycle = self.detect_cycles()
        if cycle is not None:
            raise ValueError(f"Cycle detected involving: {', '.join(cycle)}")

        in_degree: dict[str, int] = {tid: len(deps) for tid, deps in self.depends_on.items()}
        queue = sorted(tid for tid, deg in in_degree.items() if deg == 0)
        result: list[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for dependent in sorted(self.dependents.get(node, ())):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        return result


def compute_priority(task_id: str, graph: DependencyGraph) -> int:
    """Приоритет задачи = количество транзитивных зависимых.
    Чем больше задач зависят от task_id, тем выше приоритет."""
    return len(graph.get_transitive_dependents(task_id))
