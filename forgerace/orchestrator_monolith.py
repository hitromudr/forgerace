#!/usr/bin/env python3
"""
Конкурентный мультиагентный оркестратор разработки.

Читает TASKS.md, запускает N агентов параллельно на каждую задачу,
сравнивает результаты по метрикам, мержит победителя.

Использование:
    python orchestrator.py run                 # запуск утверждённых задач
    python orchestrator.py run --auto          # все задачи по цепочке
    python orchestrator.py run --task TASK-001 # конкретная задача
    python orchestrator.py run --dry-run       # план без выполнения
    python orchestrator.py run --retry         # перезапустить упавшие
    python orchestrator.py discuss new <topic> '<вопрос>'
    python orchestrator.py discuss chat <topic>
    python orchestrator.py discuss list
"""

import argparse
import json
import logging
import os
import random
import readline
import re
import select
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# ╔══════════════════════════════════════════════════════════════════╗
# ║  КОНФИГУРАЦИЯ ПРОЕКТА — единственная секция для адаптации       ║
# ╚══════════════════════════════════════════════════════════════════╝

# --- Пути ---
ROOT_DIR = Path(__file__).parent.resolve()
AGENTS_DIR = ROOT_DIR / ".agents"
TASKS_FILE = ROOT_DIR / "TASKS.md"
LOG_DIR = ROOT_DIR / ".agents" / "logs"
DISCUSS_DIR = ROOT_DIR / "docs" / "discuss"

# --- Лимиты ---
MAX_RETRIES = 3       # макс. попыток на задачу
MAX_PARALLEL_TASKS = 4  # макс. задач параллельно (агентов = задачи × len(AGENTS))
AGENT_TIMEOUT = 900   # таймаут агента в секундах (15 мин)
BUILD_TIMEOUT = 120   # таймаут сборки в секундах
DEV_BRANCH = "develop"

# --- Описание проекта (вставляется в промпты агентов) ---
PROJECT_CONTEXT = "ядра EthOS (bare-metal Rust, x86_64, no_std)"

# --- Правила для агентов (вставляются в промпт задачи) ---
AGENT_RULES = """\
1. Трогай ТОЛЬКО файлы из секции "Файлы" выше. Никакие другие файлы не изменяй и не создавай.
2. Интеграцию (pub mod, use, вызовы init) делай ТОЛЬКО в файлах из "Файлы (modify)".
3. НЕ реализуй функциональность из других задач. Если задача — context switch, не пиши планировщик.
4. Прочитай существующий код чтобы понять контекст, но НЕ изменяй его.
5. Пиши комментарии на русском.
6. unsafe — минимум, инкапсулируй в safe обёртки с обоснованием в doc-comment.
7. НИКОГДА не удаляй существующий код, который не относится к твоей задаче. Если функция, тест или модуль уже существует и не мешает — оставь как есть.
8. Работай ТОЛЬКО в текущей директории. НЕ делай cd в другие директории, не используй абсолютные пути к /home/dms/work/ethos."""

# --- Описание проекта для дискуссий ---
DISCUSS_CONTEXT = "проекта EthOS (bare-metal ОС на Rust, x86_64)"

# Инструкция для оценки уверенности в конце каждого ответа в дискуссии
CONFIDENCE_INSTRUCTION = """
В ПОСЛЕДНЕЙ строке ответа ОБЯЗАТЕЛЬНО напиши свою оценку готовности решения к реализации:
CONFIDENCE: XX%
где XX — число от 0 до 100:
- 0-30%: есть принципиальные разногласия или неясности
- 30-60%: направление понятно, но детали требуют обсуждения
- 60-80%: решение в целом готово, мелкие вопросы остались
- 80-100%: можно утверждать и реализовывать"""

# --- Агенты ---
# Каждый агент: (имя, функция запуска). Добавь сюда новые модели.
# Функция: (workdir, task, prompt) -> subprocess.CompletedProcess

def _run_claude(workdir: Path, task: "Task", prompt: str) -> subprocess.CompletedProcess:
    """Запускает Claude Code в headless-режиме со стримингом событий."""
    cmd = [
        "claude", "-p", prompt,
        "--allowedTools", "Read,Write,Edit,Bash,Grep,Glob",
        "--max-turns", "50",
        "--output-format", "stream-json", "--verbose",
    ]
    tag = f"{task.id}/claude"
    stdout_lines = []
    deadline = time.time() + AGENT_TIMEOUT
    inactivity_timeout = 300  # 5 мин без tool_use → убить (claude думает дольше gemini)
    last_activity = time.time()
    try:
        proc = subprocess.Popen(
            cmd, cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                proc.kill()
                proc.wait()
                log.error(f"[{tag}] ⏰ Таймаут ({AGENT_TIMEOUT}с)")
                return subprocess.CompletedProcess(cmd, 1, "", "TIMEOUT")

            if time.time() - last_activity > inactivity_timeout:
                proc.kill()
                proc.wait()
                log.error(f"[{tag}] ⏰ Нет tool_use {inactivity_timeout}с — завис, убиваю")
                return subprocess.CompletedProcess(cmd, 1, "", "INACTIVITY_TIMEOUT")

            ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 5.0))
            if ready:
                line = proc.stdout.readline()
                if not line:
                    break  # EOF — процесс завершился
                stdout_lines.append(line)
                stripped = line.strip()
                if stripped:
                    try:
                        event = json.loads(stripped)
                        # Сбрасываем таймер при tool_use
                        if event.get("type") == "assistant":
                            content = event.get("message", {}).get("content", [])
                            if any(b.get("type") == "tool_use" for b in content):
                                last_activity = time.time()
                        _log_claude_event(tag, event)
                    except json.JSONDecodeError:
                        pass
            elif proc.poll() is not None:
                # Процесс завершился, дочитываем
                for line in proc.stdout:
                    stdout_lines.append(line)
                break

        proc.wait(timeout=10)
        stderr = proc.stderr.read() if proc.stderr else ""

        # Извлекаем финальный result из последнего JSON-события
        result_text = ""
        for raw in reversed(stdout_lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
                if ev.get("type") == "result":
                    result_text = ev.get("result", "")
                    break
            except json.JSONDecodeError:
                continue

        return subprocess.CompletedProcess(
            cmd, returncode=proc.returncode or 0,
            stdout=result_text or "".join(stdout_lines), stderr=stderr,
        )
    except Exception as e:
        log.error(f"[{tag}] Ошибка: {e}")
        try:
            proc.kill()
        except Exception:
            pass
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=str(e))


def _log_claude_event(tag: str, event: dict):
    """Логирует событие из stream-json вывода Claude."""
    etype = event.get("type", "")

    if etype == "assistant" and "message" in event:
        msg = event["message"]
        content = msg.get("content", [])
        for block in content:
            if block.get("type") != "tool_use":
                continue
            tool = block.get("name", "?")
            inp = block.get("input", {})
            if tool == "Read":
                path = inp.get("file_path", "?").rsplit("/", 1)[-1]
                log.info(f"[{tag}] 📖 Read {path}")
            elif tool in ("Write", "Edit"):
                path = inp.get("file_path", "?").rsplit("/", 1)[-1]
                log.info(f"[{tag}] ✏️  {tool} {path}")
            elif tool == "Bash":
                cmd_str = inp.get("command", "?")[:60]
                log.info(f"[{tag}] 💻 Bash: {cmd_str}")
            elif tool in ("Grep", "Glob"):
                pattern = inp.get("pattern", "?")[:40]
                log.info(f"[{tag}] 🔍 {tool}: {pattern}")

    elif etype == "result":
        turns = event.get("num_turns", "?")
        cost = event.get("total_cost_usd", 0)
        duration = event.get("duration_ms", 0) // 1000
        usage = event.get("usage", {})
        in_tok = usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        log.info(f"[{tag}] 📊 {turns} turns, {duration}s, {in_tok//1000}k in/{out_tok//1000}k out, ${cost:.2f}")

def _run_gemini(workdir: Path, task: "Task", prompt: str) -> subprocess.CompletedProcess:
    """Запускает Gemini CLI со стримингом событий."""
    cmd = [
        "gemini", "-p", prompt,
        "--approval-mode", "yolo",
        "--output-format", "stream-json",
    ]
    tag = f"{task.id}/gemini"
    stdout_lines = []
    deadline = time.time() + AGENT_TIMEOUT
    inactivity_timeout = 180  # убиваем если нет tool_call 3 минуты
    last_activity = time.time()
    try:
        proc = subprocess.Popen(
            cmd, cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                proc.kill()
                proc.wait()
                log.error(f"[{tag}] ⏰ Таймаут ({AGENT_TIMEOUT}с)")
                return subprocess.CompletedProcess(cmd, 1, "", "TIMEOUT")

            # Проверяем inactivity таймаут (нет tool_call событий)
            if time.time() - last_activity > inactivity_timeout:
                proc.kill()
                proc.wait()
                log.error(f"[{tag}] ⏰ Нет tool_call {inactivity_timeout}с — завис, убиваю")
                return subprocess.CompletedProcess(cmd, 1, "", "INACTIVITY_TIMEOUT")

            ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 5.0))
            if ready:
                line = proc.stdout.readline()
                if not line:
                    break
                stdout_lines.append(line)
                stripped = line.strip()
                if stripped:
                    try:
                        event = json.loads(stripped)
                        if event.get("type") in ("tool_use", "tool_call"):
                            last_activity = time.time()
                        _log_gemini_event(tag, event)
                    except json.JSONDecodeError:
                        pass
            elif proc.poll() is not None:
                for line in proc.stdout:
                    stdout_lines.append(line)
                break

        proc.wait(timeout=10)
        stderr = proc.stderr.read() if proc.stderr else ""

        # Извлекаем результат из последнего message
        result_text = ""
        for raw in reversed(stdout_lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
                if ev.get("type") == "result":
                    result_text = ev.get("content", "")
                    break
                if ev.get("type") == "message" and ev.get("role") == "assistant":
                    result_text = ev.get("content", "")
                    break
            except json.JSONDecodeError:
                continue

        return subprocess.CompletedProcess(
            cmd, returncode=proc.returncode or 0,
            stdout=result_text or "".join(stdout_lines), stderr=stderr,
        )
    except Exception as e:
        log.error(f"[{tag}] Ошибка: {e}")
        try:
            proc.kill()
        except Exception:
            pass
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=str(e))


def _log_gemini_event(tag: str, event: dict):
    """Логирует событие из stream-json вывода Gemini."""
    etype = event.get("type", "")

    if etype in ("tool_call", "tool_use"):
        tool = event.get("tool", event.get("tool_name", "?"))
        args = event.get("args", event.get("parameters", {}))
        tool_lower = tool.lower()
        if "read" in tool_lower:
            path = args.get("file_path", args.get("path", "?")).rsplit("/", 1)[-1]
            log.info(f"[{tag}] 📖 Read {path}")
        elif any(w in tool_lower for w in ("write", "edit", "replace")):
            path = args.get("file_path", args.get("path", "?")).rsplit("/", 1)[-1]
            log.info(f"[{tag}] ✏️  {tool} {path}")
        elif any(w in tool_lower for w in ("run", "command", "shell", "bash")):
            cmd_str = args.get("command", "?")[:60]
            log.info(f"[{tag}] 💻 Bash: {cmd_str}")
        elif any(w in tool_lower for w in ("search", "list", "glob", "grep")):
            pattern = str(args.get("pattern", args.get("path", "?")))[:40]
            log.info(f"[{tag}] 🔍 {tool}: {pattern}")
        else:
            log.info(f"[{tag}] 🔧 {tool}")

    elif etype == "result":
        stats = event.get("stats", {})
        in_tok = stats.get("input_tokens", 0)
        out_tok = stats.get("output_tokens", 0)
        duration = stats.get("duration_ms", 0) // 1000
        tool_calls = stats.get("tool_calls", 0)
        log.info(f"[{tag}] 📊 {tool_calls} tools, {duration}s, {in_tok//1000}k in/{out_tok//1000}k out")

AGENTS = [
    ("claude", _run_claude),
    ("gemini", _run_gemini),
    # ("grok", _run_grok),  # добавь новых агентов здесь
]

# --- Команды сборки/проверки ---
BUILD_COMMANDS = [
    ["cargo", "build"],
    ["cargo", "test", "--no-run"],
]

# --- Метрики: паттерн поиска бинарника ---
BINARY_GLOB_DIR = "target/x86_64-unknown-none/debug"
BINARY_GLOBS = ["*.bin", "ethos*"]

# ╔══════════════════════════════════════════════════════════════════╗
# ║  КОНЕЦ КОНФИГУРАЦИИ — ниже универсальный код оркестратора       ║
# ╚══════════════════════════════════════════════════════════════════╝

# --- Логирование ---

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "orchestrator.log"),
    ],
)
log = logging.getLogger("orchestrator")


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
    acceptance: str     # критерий готовности
    agent: str          # claude / gemini / —
    branch: str         # task/001-frame-allocator / —
    discussion: str     # 001-scheduler-design / —
    raw_section: str    # исходный markdown-блок


# --- Парсер TASKS.md ---

def parse_tasks(path: Path) -> list[Task]:
    """Парсит TASKS.md, возвращает список задач."""
    text = path.read_text(encoding="utf-8")
    # Разбиваем по ### TASK-XXX:
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
            acceptance=_field(raw, r"\*\*Критерий готовности\*\*:\s*(.+)"),
            agent=_field(raw, r"\*\*Агент\*\*:\s*(.+)"),
            branch=_field(raw, r"\*\*Ветка\*\*:\s*(.+)"),
            discussion=_field(raw, r"\*\*Дискуссия\*\*:\s*(.+)"),
            raw_section=raw.strip(),
        ))
    return tasks


def _field(text: str, pattern: str) -> str:
    m = re.search(pattern, text)
    return m.group(1).strip() if m else ""


def _parse_deps(deps_str: str) -> list[str]:
    if not deps_str or deps_str == "—":
        return []
    return [d.strip() for d in re.findall(r"TASK-\d+", deps_str)]


def is_task_approved(task: Task) -> bool:
    """Проверяет, утверждена ли задача (есть дискуссия с резолюцией)."""
    # Автогенерированные задачи (чекпоинты) не требуют обсуждения
    if "make check" in (task.acceptance or ""):
        return True
    if not task.discussion or task.discussion == "—":
        return False
    filepath = DISCUSS_DIR / f"{task.discussion}.md"
    if not filepath.exists():
        return False
    text = filepath.read_text(encoding="utf-8")
    return "РЕЗОЛЮЦИЯ" in text


def update_task_status(task_id: str, new_status: str, agent: str = "", branch: str = ""):
    """Обновляет статус задачи в TASKS.md (в основном репозитории)."""
    lines = TASKS_FILE.read_text(encoding="utf-8").splitlines()
    in_task = False
    result = []

    for line in lines:
        # Определяем границы нужной задачи
        if line.startswith(f"### {task_id}:"):
            in_task = True
        elif line.startswith("### TASK-"):
            in_task = False

        # Заменяем поля только внутри нужной задачи
        if in_task:
            if line.startswith("- **Статус**:"):
                line = f"- **Статус**: {new_status}"
            elif agent and line.startswith("- **Агент**:"):
                line = f"- **Агент**: {agent}"
            elif branch and line.startswith("- **Ветка**:"):
                line = f"- **Ветка**: {branch}"

        result.append(line)

    TASKS_FILE.write_text("\n".join(result) + "\n", encoding="utf-8")


def link_task_discussion(task_id: str, topic: str):
    """Прописывает дискуссию в TASKS.md для задачи."""
    lines = TASKS_FILE.read_text(encoding="utf-8").splitlines()
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

    TASKS_FILE.write_text("\n".join(result) + "\n", encoding="utf-8")


def _topic_for_task(task: Task) -> str:
    """Генерирует имя топика дискуссии из задачи. Английский slug."""
    num = task.id.replace("TASK-", "").lstrip("0")
    # Маппинг русских названий → короткие английские slug
    # Если нет в маппинге — fallback на транслит
    slug = _translate_slug(task.name)
    return f"{num}-{slug}"


_slug_cache_file = ROOT_DIR / ".agents" / "slug_cache.json"

def _translate_slug(name: str) -> str:
    """Переводит русское название в короткий английский slug. Кэширует."""
    import json
    # Загружаем кэш
    cache = {}
    if _slug_cache_file.exists():
        try:
            cache = json.loads(_slug_cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    if name in cache:
        return cache[name]

    result = run_cmd(
        ["claude", "-p",
         f"Переведи на английский и сделай kebab-case slug (2-4 слова, без кавычек, только slug): {name}",
         "--output-format", "text"],
        cwd=ROOT_DIR, timeout=15, check=False,
    )
    slug = (result.stdout or "").strip().lower()
    slug = re.sub(r"[^a-z0-9-]", "", slug).strip("-")
    if not slug or len(slug) >= 50:
        slug = slugify(name)

    # Сохраняем в кэш
    cache[name] = slug
    _slug_cache_file.parent.mkdir(parents=True, exist_ok=True)
    _slug_cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return slug


# --- Git Worktree ---

def create_worktree(agent_num: int, branch: str) -> Path:
    """Создаёт worktree для агента, возвращает путь."""
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    agent_dir = AGENTS_DIR / f"agent-{agent_num}"

    if agent_dir.exists():
        log.warning(f"Worktree {agent_dir} уже существует, удаляю")
        run_cmd(["git", "worktree", "remove", str(agent_dir), "--force"], cwd=ROOT_DIR)

    # Удаляем старую ветку если есть — иначе worktree возьмёт устаревший код
    run_cmd(["git", "branch", "-D", branch], cwd=ROOT_DIR, check=False)

    # Создаём новую ветку от develop
    run_cmd(
        ["git", "worktree", "add", str(agent_dir), "-b", branch, DEV_BRANCH],
        cwd=ROOT_DIR,
    )

    # Сбрасываем не-src файлы (могут быть грязными от stash pop)
    run_cmd(["git", "checkout", "--", "orchestrator.py", "TASKS.md"], cwd=agent_dir, check=False)

    log.info(f"Worktree создан: {agent_dir} → {branch}")
    return agent_dir


def remove_worktree(agent_num: int):
    """Удаляет worktree агента."""
    agent_dir = AGENTS_DIR / f"agent-{agent_num}"
    if agent_dir.exists():
        run_cmd(["git", "worktree", "remove", str(agent_dir), "--force"], cwd=ROOT_DIR)
        log.info(f"Worktree удалён: {agent_dir}")


# --- Запуск агентов ---

def build_prompt(task: Task, error_log: str = "") -> str:
    """Формирует промпт для агента."""
    prompt = f"""Ты автономный агент разработки {PROJECT_CONTEXT}.

## Твоя задача: {task.id} — {task.name}

{task.description}

## Файлы
- Создать: {task.files_new}
- Изменить: {task.files_modify}

## Интеграция
{task.integration}

## Критерий готовности
{task.acceptance}

## Правила
{AGENT_RULES}
"""

    if error_log:
        # Берём конец лога — ошибки Rust в конце, в начале только "Compiling..."
        log_head = error_log[-4000:]
        prompt += f"""
## ПРЕДЫДУЩАЯ ПОПЫТКА ПРОВАЛИЛАСЬ

Ошибка сборки/тестов:
```
{log_head}
```

Исправь ошибки и попробуй снова.
"""

    return prompt


def _agent_runners() -> dict:
    """Возвращает словарь {имя: функция_запуска} из конфига AGENTS."""
    return {name: fn for name, fn in AGENTS}


def run_agent(agent_type: str, workdir: Path, task: Task, prompt: str) -> subprocess.CompletedProcess:
    """Запускает агента нужного типа."""
    runners = _agent_runners()
    if agent_type not in runners:
        raise ValueError(f"Неизвестный агент: {agent_type}. Доступны: {list(runners.keys())}")
    return runners[agent_type](workdir, task, prompt)


# --- Проверка результата ---

def verify_build(workdir: Path, task: Task = None) -> tuple[bool, str]:
    """Проверяет сборку в worktree. Возвращает (успех, лог ошибки)."""
    # Сначала проверяем что файлы задачи существуют
    if task and task.files_new and task.files_new.strip() != "—":
        missing = []
        for f in task.files_new.split(","):
            f = f.strip()
            if f and _is_valid_path(f) and not (workdir / f).exists():
                missing.append(f)
        if missing:
            return False, f"Файлы задачи не созданы: {', '.join(missing)}"

    # Проверяем что агент вообще что-то написал (сравниваем с базовой веткой)
    diff = run_cmd(["git", "diff", "--stat", DEV_BRANCH], cwd=workdir, check=False)
    has_changes = bool((diff.stdout or "").strip())
    # Проверяем незакоммиченные файлы тоже
    status = run_cmd(["git", "status", "--porcelain"], cwd=workdir, check=False)
    has_new_files = bool((status.stdout or "").strip())

    # Для задач-валидаторов (критерий = команда, например "make check == 0"):
    # если код уже чист, diff пустой — это ок, проверяем результат команды
    if not has_changes and not has_new_files:
        if task and "make check" in (task.acceptance or ""):
            result = run_cmd(["make", "check"], cwd=workdir, timeout=BUILD_TIMEOUT, check=False)
            if result.returncode == 0:
                return True, ""  # критерий выполнен без изменений
            return False, f"make check failed:\n{result.stderr}\n{result.stdout}"
        return False, "Агент не внёс никаких изменений"

    for cmd in BUILD_COMMANDS:
        result = run_cmd(cmd, cwd=workdir, timeout=BUILD_TIMEOUT, check=False)
        if result.returncode != 0:
            return False, f"{' '.join(cmd)} failed:\n{result.stderr}\n{result.stdout}"

    return True, ""


def verify_design_task(workdir: Path, task: Task) -> tuple[bool, str]:
    """Проверяет дизайн-задачу (наличие файлов)."""
    for f in task.files_new.split(","):
        f = f.strip()
        if f and _is_valid_path(f) and not (workdir / f).exists():
            return False, f"Файл не создан: {f}"
    return True, ""


# --- Git: develop-ветка и автомерж ---

# Лок для сериализации мержей в develop (из разных потоков)
import threading
_merge_lock = threading.Lock()


def ensure_develop_branch():
    """Создаёт ветку develop от master если её нет."""
    result = run_cmd(
        ["git", "branch", "--list", DEV_BRANCH], cwd=ROOT_DIR, check=False,
    )
    if not result.stdout.strip():
        run_cmd(["git", "branch", DEV_BRANCH, "master"], cwd=ROOT_DIR)
        log.info(f"Создана ветка {DEV_BRANCH} от master")


def merge_to_develop(branch: str, task_id: str) -> bool:
    """Мержит task-ветку в develop без переключения веток и без stash.
    Использует git merge напрямую, обновляя develop через отдельный worktree."""
    with _merge_lock:
        # Мержим task-ветку в develop. Основной репо может быть на develop,
        # поэтому worktree add develop не сработает. Используем detached worktree
        # на коммите develop, потом обновляем ветку.
        merge_dir = AGENTS_DIR / "_merge_tmp"
        if merge_dir.exists():
            run_cmd(["git", "worktree", "remove", str(merge_dir), "--force"], cwd=ROOT_DIR, check=False)

        # Detached HEAD на текущем коммите develop
        dev_sha = run_cmd(
            ["git", "rev-parse", DEV_BRANCH], cwd=ROOT_DIR, check=False
        ).stdout.strip()
        run_cmd(
            ["git", "worktree", "add", "--detach", str(merge_dir), dev_sha],
            cwd=ROOT_DIR, check=False,
        )

        try:
            result = run_cmd(
                ["git", "merge", branch, "--no-ff", "-X", "theirs",
                 "-m", f"Merge {task_id}: {branch} → {DEV_BRANCH}"],
                cwd=merge_dir, check=False,
            )

            if result.returncode != 0:
                err_msg = (result.stderr or result.stdout or "неизвестная ошибка")[:500]
                log.error(f"  ✗ Merge {branch} → {DEV_BRANCH} провалился:\n{err_msg}")
                run_cmd(["git", "merge", "--abort"], cwd=merge_dir, check=False)
                return False

            # Обновляем ветку develop на новый merge-коммит
            merge_sha = run_cmd(
                ["git", "rev-parse", "HEAD"], cwd=merge_dir, check=False
            ).stdout.strip()
            run_cmd(
                ["git", "update-ref", f"refs/heads/{DEV_BRANCH}", merge_sha],
                cwd=ROOT_DIR, check=False,
            )

            # Синхронизируем рабочую копию: подтягиваем только src/ из нового коммита.
            # НЕ используем reset --hard — он убьёт незакоммиченные изменения в TASKS.md
            # Синхронизируем файлы из мержа, но НЕ трогаем TASKS.md и orchestrator.py
            # (оркестратор обновляет их параллельно, checkout затрёт)
            changed = run_cmd(
                ["git", "diff", "--name-only", f"{dev_sha}..{merge_sha}"],
                cwd=merge_dir, check=False,
            )
            for fname in (changed.stdout or "").strip().splitlines():
                fname = fname.strip()
                if fname and fname not in ("TASKS.md", "orchestrator.py"):
                    run_cmd(["git", "checkout", merge_sha, "--", fname], cwd=ROOT_DIR, check=False)

            log.info(f"  ✓ {branch} вмержен в {DEV_BRANCH}")
            return True
        finally:
            run_cmd(["git", "worktree", "remove", str(merge_dir), "--force"], cwd=ROOT_DIR, check=False)


# --- Heartbeat-монитор для агентов ---

_active_agents: dict[str, tuple[str, Path, float]] = {}  # tag → (task_id, workdir, start_time)
_active_agents_lock = threading.Lock()

def _heartbeat_loop(interval: int = 15):
    """Фоновый поток: раз в interval секунд показывает прогресс активных агентов."""
    while True:
        time.sleep(interval)
        with _active_agents_lock:
            agents = dict(_active_agents)
        for tag, (task_id, workdir, start_time) in agents.items():
            elapsed = int(time.time() - start_time)
            mins, secs = divmod(elapsed, 60)
            # Проверяем какие файлы изменены
            result = subprocess.run(
                ["git", "diff", "--name-only"],
                cwd=workdir, capture_output=True, text=True, timeout=5,
            )
            files = [f.strip() for f in (result.stdout or "").strip().split("\n") if f.strip()]
            if files:
                files_str = ", ".join(f.rsplit("/", 1)[-1] for f in files[:5])
                if len(files) > 5:
                    files_str += f" (+{len(files)-5})"
                log.info(f"[{tag}] ⏳ {mins}m{secs:02d}s — правит: {files_str}")
            else:
                log.info(f"[{tag}] ⏳ {mins}m{secs:02d}s — читает код...")

def _start_heartbeat():
    t = threading.Thread(target=_heartbeat_loop, daemon=True)
    t.start()

def _register_agent(tag: str, task_id: str, workdir: Path):
    with _active_agents_lock:
        _active_agents[tag] = (task_id, workdir, time.time())

def _unregister_agent(tag: str):
    with _active_agents_lock:
        _active_agents.pop(tag, None)


# --- Основной цикл задачи ---

@dataclass
class AgentResult:
    """Результат работы одного агента над задачей."""
    agent_type: str
    branch: str
    workdir: Path
    success: bool
    # Метрики для сравнения (заполняются при успехе)
    binary_size: int = 0
    unsafe_count: int = 0
    code_lines: int = 0


def run_single_agent(task: Task, agent_num: int, agent_type: str) -> AgentResult:
    """Запускает одного агента на задачу. Возвращает результат с метриками."""
    slug = _translate_slug(task.name)
    branch = f"task/{task.id.lower()}-{slug}-{agent_type}"
    # Удаляем старую ветку если есть (от предыдущего прогона)
    run_cmd(["git", "branch", "-D", branch], cwd=ROOT_DIR, check=False)
    workdir = create_worktree(agent_num, branch)

    tag = f"{task.id}/{agent_type}"
    log.info(f"  ▶ [{tag}] agent-{agent_num}")
    _register_agent(tag, task.id, workdir)

    is_design = task.files_new.startswith("docs/")
    error_log = ""

    for attempt in range(1, MAX_RETRIES + 1):
        log.info(f"[{tag}] Попытка {attempt}/{MAX_RETRIES}")

        prompt = build_prompt(task, error_log)
        result = run_agent(agent_type, workdir, task, prompt)

        # Сохраняем вывод агента для диагностики
        agent_log = LOG_DIR / f"{task.id.lower()}-{agent_type}-attempt{attempt}.log"
        agent_log.write_text(
            f"=== STDOUT ===\n{result.stdout or '(empty)'}\n\n=== STDERR ===\n{result.stderr or '(empty)'}\n",
            encoding="utf-8",
        )

        if result.returncode != 0:
            log.warning(f"[{tag}] Агент завершился с ошибкой (код {result.returncode})")
            error_log = result.stderr or result.stdout or "Агент упал без вывода"
            continue

        # Коммит — добавляем файлы задачи (src/, Makefile, docs/ и т.д.)
        for p in _task_paths(task):
            run_cmd(["git", "add", p], cwd=workdir, check=False)
        diff = run_cmd(["git", "diff", "--cached", "--stat"], cwd=workdir, check=False)
        if diff.stdout.strip():
            run_cmd(
                ["git", "commit", "-m", f"{task.id}: {task.name} [{agent_type}] (попытка {attempt})"],
                cwd=workdir, check=False,
            )

        # Проверка сборки
        if is_design:
            ok, error_log = verify_design_task(workdir, task)
        else:
            ok, error_log = verify_build(workdir, task)

        if not ok and "не внёс никаких изменений" in error_log:
            # Логируем хвост вывода агента — почему он ничего не написал
            stdout_tail = (result.stdout or "")[-500:].strip()
            stderr_tail = (result.stderr or "")[-300:].strip()
            if stdout_tail:
                log.warning(f"[{tag}] stdout (хвост): {stdout_tail[:200]}")
            if stderr_tail:
                log.warning(f"[{tag}] stderr: {stderr_tail[:200]}")

        if ok:
            log.info(f"[{tag}] ✓ сборка пройдена")
            _unregister_agent(tag)
            metrics = collect_metrics(workdir, task)
            return AgentResult(
                agent_type=agent_type, branch=branch, workdir=workdir,
                success=True, **metrics,
            )
        else:
            log.warning(f"[{tag}] ✗ сборка провалена:\n{error_log[-500:]}")

    log.error(f"[{tag}] ✗ BLOCKED после {MAX_RETRIES} попыток")
    _unregister_agent(tag)
    return AgentResult(agent_type=agent_type, branch=branch, workdir=workdir, success=False)


def collect_metrics(workdir: Path, task: Task) -> dict:
    """Собирает метрики реализации для сравнения."""
    metrics = {"binary_size": 0, "unsafe_count": 0, "code_lines": 0}

    # Собираем все файлы задачи (новые + модифицируемые), убираем пояснения в скобках
    task_files = []
    for files_str in (task.files_new, task.files_modify):
        if files_str and files_str.strip() != "—":
            for f in files_str.split(","):
                f = re.sub(r"\s*\(.*?\)", "", f).strip()
                if f and _is_valid_path(f):
                    task_files.append(f)

    # Считаем unsafe и строки кода в файлах задачи
    for f in task_files:
        filepath = workdir / f
        if filepath.exists() and filepath.is_file():
            content = filepath.read_text(encoding="utf-8", errors="ignore")
            metrics["unsafe_count"] += content.count("unsafe")

    # Строки кода — через git diff от базовой ветки (файлы задачи)
    paths = _task_paths(task) if task else ["src/"]
    diff_result = run_cmd(
        ["git", "diff", "--numstat", DEV_BRANCH, "--"] + paths,
        cwd=workdir, check=False,
    )
    if diff_result.returncode == 0:
        for line in diff_result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                try:
                    added = int(parts[0])
                    metrics["code_lines"] += added
                except ValueError:
                    pass

    # Размер бинарника (если собрался)
    target_dir = workdir / BINARY_GLOB_DIR
    if target_dir.exists():
        bins = []
        for pattern in BINARY_GLOBS:
            bins.extend(target_dir.glob(pattern))
        if bins:
            metrics["binary_size"] = max(b.stat().st_size for b in bins if b.is_file())

    return metrics


def _is_valid_path(p: str) -> bool:
    """Проверяет, что строка похожа на путь к файлу, а не на описание."""
    # Отсеиваем строки с пробелами или кириллицей — это описания, не пути
    if " " in p:
        return False
    if re.search(r"[а-яА-ЯёЁ]", p):
        return False
    return True


def _task_paths(task: Task) -> list[str]:
    """Возвращает список путей из задачи (новые + modify) + src/ как fallback."""
    paths = set()
    for files_str in (task.files_new, task.files_modify):
        if files_str and files_str.strip() != "—":
            for f in files_str.split(","):
                f = re.sub(r"\s*\(.*?\)", "", f).strip()
                if f and _is_valid_path(f):
                    paths.add(f)
    if not paths:
        paths.add("src/")
    return sorted(paths)


def _get_diff(result: "AgentResult", task: "Task" = None) -> str:
    """Получает diff агента относительно develop (по файлам задачи)."""
    paths = _task_paths(task) if task else ["src/"]
    diff_result = run_cmd(
        ["git", "diff", DEV_BRANCH, "--"] + paths,
        cwd=result.workdir, check=False,
    )
    diff_text = (diff_result.stdout or "").strip()
    if len(diff_text) > 6000:
        diff_text = diff_text[:6000] + "\n... (обрезано)"
    return diff_text


def _pick_reviewer(passed: list["AgentResult"]) -> str:
    """Выбирает ревьюера: не автор кода. Ревьюить может любой агент,
    даже если он не смог написать код (заблокировался на сборке)."""
    all_agent_names = [name for name, _ in AGENTS]
    author_names = {r.agent_type for r in passed}

    # Предпочитаем агента, который НЕ писал код
    non_authors = [n for n in all_agent_names if n not in author_names]
    if non_authors:
        return random.choice(non_authors)

    # Все агенты — авторы (оба прошли сборку). Берём не первого (чтобы не ревьюил сам себя)
    if len(passed) >= 2:
        return passed[1].agent_type

    # Единственный агент в системе — ревьюит сам себя
    return passed[0].agent_type


def _run_reviewer(reviewer_type: str, prompt: str) -> str:
    """Вызывает агента в текстовом режиме для ревью. Промпт через stdin."""
    if reviewer_type == "gemini":
        cmd = ["gemini", "-p", "-"]
    else:
        cmd = ["claude", "-p", "-", "--output-format", "text",
               "--permission-mode", "auto"]
    result = subprocess.run(
        cmd, cwd=ROOT_DIR, input=prompt,
        capture_output=True, text=True, timeout=300,
    )
    return (result.stdout or "").strip()


def _single_review(reviewer: str, author: str, diff: str, task: Task) -> dict:
    """Один ревьюер проверяет одного автора. Возвращает parsed review."""
    prompt = f"""Ты ревьюер кода {PROJECT_CONTEXT}. Ты проверяешь реализацию агента {author}.

Задача: {task.id} — {task.name}
Описание: {task.description}
Критерий готовности: {task.acceptance}

Проверь:
1. **Корректность**: баги, UB, гонки данных, use-after-free?
2. **Соответствие задаче**: реализовано ли то, что нужно по описанию и критерию готовности?
3. **Качество**: unsafe обоснован? Мёртвый код? Архитектура?

Формат ответа — строго:
VERDICT: APPROVED или NEEDS_WORK
COMMENTS: <что конкретно проверено и какие проблемы найдены. При APPROVED — перечисли что проверил и почему ок. При NEEDS_WORK — конкретные замечания. ПУСТЫЕ КОММЕНТАРИИ ЗАПРЕЩЕНЫ.>
SUMMARY: <итог в 1-2 строки>

APPROVED = код готов к мержу, ты ЛИЧНО проверил каждый пункт выше.
NEEDS_WORK = нужны правки.
ВАЖНО:
- APPROVED без обоснования в COMMENTS будет отклонён. Ты должен доказать что проверил код.
- НЕЛЬЗЯ допускать "считаем что X реализовано в коде, не попавшем в diff". Если чего-то нет в diff — это NEEDS_WORK.
- Оценивай ТОЛЬКО то, что видишь в diff. Не додумывай.
Пиши на русском.

### Diff от {author}
```diff
{diff}
```"""

    try:
        review_text = _run_reviewer(reviewer, prompt)
        if not review_text:
            return {"verdict": "error", "reviewer": reviewer, "author": author,
                    "full_text": "", "comments": "", "summary": "Пустой ответ"}

        verdict_match = re.search(r"\**VERDICT\**:\s*\**(\w+)\**", review_text, re.IGNORECASE)
        comments_match = re.search(r"\**COMMENTS\**:\s*(.+?)(?=\n\**SUMMARY\**:|\Z)", review_text, re.IGNORECASE | re.DOTALL)
        summary_match = re.search(r"\**SUMMARY\**:\s*(.+)", review_text, re.IGNORECASE)

        verdict = verdict_match.group(1).upper() if verdict_match else "NEEDS_WORK"
        comments = comments_match.group(1).strip() if comments_match else ""

        # APPROVED без обоснования — невалидное ревью, понижаем до NEEDS_WORK
        if verdict == "APPROVED" and len(comments) < 20:
            log.warning(f"[{reviewer}] APPROVED без обоснования — понижаю до NEEDS_WORK")
            verdict = "NEEDS_WORK"
            comments = "Ревьюер не обосновал APPROVED. Требуется повторное ревью с конкретным анализом."

        return {
            "verdict": verdict,
            "reviewer": reviewer,
            "author": author,
            "full_text": review_text,
            "comments": comments,
            "summary": summary_match.group(1).strip() if summary_match else "",
        }
    except Exception as e:
        return {"verdict": "error", "reviewer": reviewer, "author": author,
                "full_text": "", "comments": "", "summary": f"Ошибка: {e}"}


def code_review(passed: list["AgentResult"], task: Task) -> dict:
    """Крест-на-крест ревью. Возвращает {verdict, best, reason, comments, full_text}."""
    diffs = {}
    for r in passed:
        diff = _get_diff(r, task)
        if diff:
            diffs[r.agent_type] = diff

    if not diffs:
        return {"verdict": "error", "reason": "Нет diff для ревью"}

    all_agent_names = [name for name, _ in AGENTS]
    author_names = list(diffs.keys())

    if len(author_names) >= 2:
        # Крест-на-крест: каждый ревьюит оппонента параллельно
        log.info(f"    Ревью крест-на-крест: {author_names[0]}→{author_names[1]}, {author_names[1]}→{author_names[0]}")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(_single_review, author_names[1], author_names[0], diffs[author_names[0]], task): author_names[0],
                pool.submit(_single_review, author_names[0], author_names[1], diffs[author_names[1]], task): author_names[1],
            }
            reviews = {}
            for f in as_completed(futures):
                author = futures[f]
                reviews[author] = f.result()
    else:
        # Один автор — ревьюер другой агент
        author = author_names[0]
        reviewer = next((n for n in all_agent_names if n != author), author)
        log.info(f"    Ревьюер: {reviewer} → {author}")
        reviews = {author: _single_review(reviewer, author, diffs[author], task)}

    # Собираем результат — логируем каждое ревью отдельно
    full_text_parts = []
    for author, rv in reviews.items():
        header = f"📋 {rv['reviewer']} ревьюит {author}: {rv['verdict']}"
        log.info(f"    {header}")
        log.info(f"    {rv.get('summary', rv.get('comments', '')[:200])}")
        full_text_parts.append(f"=== {rv['reviewer']} ревьюит {author} ===\n{rv['full_text']}")

    full_text = "\n\n".join(full_text_parts)

    # Определяем лучшего и общий вердикт
    if len(reviews) >= 2:
        a, b = list(reviews.keys())
        rv_a, rv_b = reviews[a], reviews[b]
        a_approved = rv_a["verdict"] == "APPROVED"
        b_approved = rv_b["verdict"] == "APPROVED"

        if a_approved and not b_approved:
            best, verdict, reason = a, "APPROVED", f"{rv_b['reviewer']} нашёл проблемы в {b}, а {a} прошёл ревью"
            comments = rv_b.get("comments", "")
        elif b_approved and not a_approved:
            best, verdict, reason = b, "APPROVED", f"{rv_a['reviewer']} нашёл проблемы в {a}, а {b} прошёл ревью"
            comments = rv_a.get("comments", "")
        elif a_approved and b_approved:
            # Оба прошли — берём с меньшим unsafe или первого
            best, verdict = a, "APPROVED"
            reason = f"Оба прошли ревью, выбран {a}"
            comments = ""
        else:
            # Оба NEEDS_WORK — выбираем менее плохого (у кого ревью короче/мягче)
            best = a  # по умолчанию первый
            verdict, reason = "NEEDS_WORK", f"Оба требуют доработки"
            comments = reviews[best].get("comments", "")
    else:
        author = list(reviews.keys())[0]
        rv = reviews[author]
        best = author if rv["verdict"] != "error" else "none"
        verdict = rv["verdict"]
        comments = rv.get("comments", "")
        reason = rv.get("summary", "")

    return {
        "full_text": full_text,
        "reviewer": "cross-review",
        "best": best,
        "verdict": verdict,
        "comments": comments,
        "reason": reason,
        "reviews": reviews,  # per-agent reviews для индивидуальной доработки
    }


def _send_to_rework(result: "AgentResult", task: Task, comments: str) -> bool:
    """Отправляет замечания ревью агенту на доработку. Возвращает True если сборка ок."""
    tag = f"{task.id}/{result.agent_type}"
    log.info(f"[{tag}] 🔧 Доработка по замечаниям ревью")

    # Для Claude: замечания через файл (он умеет Read).
    # Для Gemini/Aider: замечания прямо в промпте (aider не читает файлы по просьбе).
    review_file = result.workdir / "REVIEW_COMMENTS.md"
    review_file.write_text(comments, encoding="utf-8")

    if result.agent_type == "claude":
        comments_section = "Прочитай файл REVIEW_COMMENTS.md в корне рабочей директории — там полные замечания."
    else:
        comments_section = comments

    prompt = f"""Ты получил замечания code review для задачи {task.id} — {task.name}.

## Замечания ревьюера
{comments_section}

## Что делать
Исправь ВСЕ указанные проблемы. Не трогай файлы, которые не относятся к задаче.

## Правила
{AGENT_RULES}
"""
    runner = _agent_runners().get(result.agent_type)
    if not runner:
        return False

    run_result = runner(result.workdir, task, prompt)
    review_file.unlink(missing_ok=True)

    # Коммитим правки
    for p in _task_paths(task):
        run_cmd(["git", "add", p], cwd=result.workdir, check=False)
    diff = run_cmd(["git", "diff", "--cached", "--stat"], cwd=result.workdir, check=False)
    if diff.stdout.strip():
        run_cmd(
            ["git", "commit", "-m", f"{task.id}: правки по ревью [{result.agent_type}]"],
            cwd=result.workdir, check=False,
        )

    # Проверка сборки
    ok, error = verify_build(result.workdir, task)
    if not ok:
        log.warning(f"[{tag}] ✗ сборка после доработки: {error[-300:]}")
        return False

    log.info(f"[{tag}] ✓ сборка после доработки пройдена")
    return True


MAX_REVIEW_ROUNDS = 3  # макс. итераций ревью → доработка


def execute_task_competitive(task: Task, task_idx: int) -> bool:
    """
    Конкурентное выполнение: агенты параллельно, первый финишировавший → ревью.
    Если ревью APPROVED — мерж сразу, второй агент не ждём.
    Возвращает True если задача выполнена.
    """
    log.info(f"═══ {task.id}: {task.name} (конкурентный режим) ═══")
    update_task_status(task.id, "in_progress:both")

    # 1. Запускаем агентов параллельно, обрабатываем по мере завершения
    agent_names = [name for name, _ in AGENTS]
    all_results = []
    passed = []

    with ThreadPoolExecutor(max_workers=len(agent_names)) as pool:
        futures = {}
        for i, agent_name in enumerate(agent_names):
            agent_num = task_idx * len(agent_names) + i - (len(agent_names) - 1)
            f = pool.submit(run_single_agent, task, agent_num, agent_name)
            futures[f] = agent_name

        for future in as_completed(futures):
            agent_name = futures[future]
            result = future.result()
            all_results.append(result)

            if not result.success:
                continue
            if not result.code_lines and not _get_diff(result, task):
                continue

            passed.append(result)
            log.info(f"[{task.id}/{result.agent_type}] unsafe={result.unsafe_count}, lines={result.code_lines}, bin={result.binary_size}")

            # Первый финишировавший → другой ревьюит (новый экземпляр, stateless)
            reviewer = next((n for n in agent_names if n != result.agent_type), result.agent_type)
            log.info(f"[{task.id}] 📝 Ревью: {reviewer} → {result.agent_type}")
            rv = _single_review(reviewer, result.agent_type, _get_diff(result, task), task)
            log.info(f"[{task.id}] 📋 {reviewer} ревьюит {result.agent_type}: {rv['verdict']}")
            log.info(f"[{task.id}] {rv.get('summary', rv.get('comments', '')[:200])}")

            if rv["verdict"] == "APPROVED":
                log.info(f"[{task.id}] ✅ Ревью пройдено: {result.agent_type} — {rv.get('summary', '')}")
                log.info(f"[{task.id}] 🏆 победитель: {result.agent_type}")
                if merge_to_develop(result.branch, task.id):
                    update_task_status(task.id, "done", agent=result.agent_type, branch=result.branch)
                    log.info(f"[{task.id}] ✓ done (вмержен в {DEV_BRANCH})")
                else:
                    update_task_status(task.id, f"review:{result.agent_type}",
                                      agent=result.agent_type, branch=result.branch)
                    log.warning(f"[{task.id}] ⚠ review (мерж не удался)")
                _cleanup_worktrees(all_results)
                return True

            # NEEDS_WORK — ждём второго для сравнения
            log.info(f"[{task.id}] ⏳ {result.agent_type} NEEDS_WORK, ждём второго...")

    # Оба завершились, никто не получил APPROVED с первого раза
    if not passed:
        log.error(f"[{task.id}] ✗ ни один агент не написал рабочий код → BLOCKED")
        update_task_status(task.id, "blocked")
        _cleanup_worktrees(all_results)
        return False

    for r in passed:
        log.info(f"[{task.id}/{r.agent_type}] unsafe={r.unsafe_count}, lines={r.code_lines}, bin={r.binary_size}")

    # 2. Цикл: ревью → доработка
    best_result = None
    for review_round in range(1, MAX_REVIEW_ROUNDS + 1):
        log.info(f"[{task.id}] 📝 Code review (раунд {review_round}/{MAX_REVIEW_ROUNDS})...")
        rv = code_review(passed, task)

        if rv["verdict"] == "error":
            log.error(f"[{task.id}] ✗ Ревью не удалось: {rv.get('reason', '?')}")
            update_task_status(task.id, "blocked")
            _cleanup_worktrees(all_results)
            return False

        log.info(f"[{task.id}] 📝 Результат ревью:\n{rv.get('full_text', rv.get('reason', ''))}")

        # Находим лучшего
        best_name = rv.get("best")
        if not best_name or best_name == "none":
            # Все плохие — выбираем первого и даём шанс на доработку
            log.warning(f"[{task.id}] ⚠ Ревьюер: оба непригодны — берём {passed[0].agent_type} на доработку")
            best_name = passed[0].agent_type

        best_result = next((r for r in passed if r.agent_type.lower() == best_name), None)
        if not best_result:
            log.error(f"[{task.id}] ✗ Ревьюер выбрал '{best_name}', но такого агента нет")
            update_task_status(task.id, "blocked")
            _cleanup_worktrees(all_results)
            return False

        if rv["verdict"] == "APPROVED":
            log.info(f"[{task.id}] ✅ Ревью пройдено: {best_result.agent_type} — {rv.get('reason', '')}")
            break

        # NEEDS_WORK — отправляем на доработку (если есть замечания)
        if rv.get("verdict") == "error" or not rv.get("comments", "").strip():
            log.warning(f"[{task.id}] ⚠ Ревью ошибка/без замечаний — пропускаю раунд")
            continue

        # При крест-на-крест: у каждого агента свои замечания, дорабатываем обоих
        if len(passed) >= 2 and "reviews" in rv:
            for agent_result in passed:
                agent_comments = rv["reviews"].get(agent_result.agent_type, {}).get("comments", "")
                if agent_comments.strip():
                    log.info(f"[{task.id}/{agent_result.agent_type}] 🔧 отправлен на доработку")
                    log.info(f"[{task.id}/{agent_result.agent_type}] Замечания: {agent_comments}")
                    _send_to_rework(agent_result, task, agent_comments)
        else:
            comments = rv.get("comments", "")
            log.info(f"[{task.id}/{best_result.agent_type}] 🔧 отправлен на доработку")
            log.info(f"[{task.id}/{best_result.agent_type}] Замечания: {comments}")
            _send_to_rework(best_result, task, comments)
            # Один агент — дальше ревьюим только его
            passed = [best_result]
    else:
        # Последняя доработка сделана — даём финальное ревью (approve или block)
        log.info(f"[{task.id}] 📝 Финальное ревью после {MAX_REVIEW_ROUNDS} раундов доработки...")
        rv = code_review(passed, task)
        log.info(f"[{task.id}] 📝 Результат ревью:\n{rv.get('full_text', rv.get('reason', ''))}")

        best_name = rv.get("best")
        if best_name and best_name != "none":
            best_result = next((r for r in passed if r.agent_type.lower() == best_name), None)
        if best_result and rv.get("verdict") == "APPROVED":
            log.info(f"[{task.id}] ✅ Ревью пройдено (финал): {best_result.agent_type} — {rv.get('reason', '')}")
        else:
            log.error(f"[{task.id}] ✗ не прошёл ревью за {MAX_REVIEW_ROUNDS}+1 раундов → BLOCKED")
            update_task_status(task.id, "blocked")
            _cleanup_worktrees(all_results)
            return False

    # 3. Мерж победителя
    log.info(f"[{task.id}] 🏆 победитель: {best_result.agent_type}")
    if merge_to_develop(best_result.branch, task.id):
        update_task_status(task.id, "done", agent=best_result.agent_type, branch=best_result.branch)
        log.info(f"[{task.id}] ✓ done (вмержен в {DEV_BRANCH})")
    else:
        update_task_status(task.id, f"review:{best_result.agent_type}",
                          agent=best_result.agent_type, branch=best_result.branch)
        log.warning(f"[{task.id}] ⚠ review (мерж не удался)")

    _cleanup_worktrees(all_results)
    return True


def execute_task_single(task: Task, task_idx: int, agent_type: str) -> bool:
    """Выполнение задачи одним агентом + ревью другим. Для простых задач."""
    log.info(f"═══ {task.id}: {task.name} ({agent_type}, ревью другим) ═══")
    update_task_status(task.id, f"in_progress:{agent_type}")

    result = run_single_agent(task, task_idx, agent_type)
    if not result.success or result.code_lines == 0:
        # Проверяем diff на случай lines=0
        if not result.success or not _get_diff(result, task):
            log.error(f"[{task.id}/{agent_type}] ✗ не написал код → BLOCKED")
            update_task_status(task.id, "blocked")
            _cleanup_worktrees([result])
            return False

    log.info(f"[{task.id}/{result.agent_type}] unsafe={result.unsafe_count}, lines={result.code_lines}, bin={result.binary_size}")

    # Ревью другим агентом
    all_agent_names = [name for name, _ in AGENTS]
    reviewer = next((n for n in all_agent_names if n != agent_type), agent_type)
    diff = _get_diff(result, task)
    if not diff:
        log.error(f"[{task.id}] ✗ пустой diff → BLOCKED")
        update_task_status(task.id, "blocked")
        _cleanup_worktrees([result])
        return False

    # Цикл ревью → доработка (как в конкурентном, но с одним агентом)
    passed = [result]
    best_result = result
    for review_round in range(1, MAX_REVIEW_ROUNDS + 1):
        log.info(f"[{task.id}] 📝 Code review (раунд {review_round}/{MAX_REVIEW_ROUNDS})...")
        log.info(f"[{task.id}] Ревьюер: {reviewer} → {agent_type}")
        rv = _single_review(reviewer, agent_type, _get_diff(best_result, task), task)
        log.info(f"[{task.id}] 📋 {reviewer} ревьюит {agent_type}: {rv['verdict']}")
        log.info(f"[{task.id}] {rv.get('summary', rv.get('comments', '')[:200])}")

        if rv["verdict"] == "APPROVED":
            log.info(f"[{task.id}] ✅ Ревью пройдено: {agent_type} — {rv.get('summary', '')}")
            break

        comments = rv.get("comments", "")
        if not comments.strip() or rv.get("verdict") == "error":
            log.warning(f"[{task.id}] ⚠ Ревью без замечаний или ошибка — пропускаю раунд")
            continue
        log.info(f"[{task.id}/{agent_type}] 🔧 отправлен на доработку")
        log.info(f"[{task.id}/{agent_type}] Замечания: {comments}")
        ok = _send_to_rework(best_result, task, comments)
        if not ok:
            log.warning(f"[{task.id}/{agent_type}] ✗ Доработка не удалась")
    else:
        # Финальное ревью
        log.info(f"[{task.id}] 📝 Финальное ревью...")
        rv = _single_review(reviewer, agent_type, _get_diff(best_result, task), task)
        if rv["verdict"] != "APPROVED":
            log.error(f"[{task.id}] ✗ не прошёл ревью → BLOCKED")
            update_task_status(task.id, "blocked")
            _cleanup_worktrees([result])
            return False
        log.info(f"[{task.id}] ✅ Ревью пройдено (финал): {agent_type}")

    # Мерж
    log.info(f"[{task.id}] 🏆 победитель: {agent_type}")
    if merge_to_develop(best_result.branch, task.id):
        update_task_status(task.id, "done", agent=agent_type, branch=best_result.branch)
        log.info(f"[{task.id}] ✓ done (вмержен в {DEV_BRANCH})")
    else:
        update_task_status(task.id, f"review:{agent_type}", agent=agent_type, branch=best_result.branch)
        log.warning(f"[{task.id}] ⚠ review (мерж не удался)")

    _cleanup_worktrees([result])
    return True


def _cleanup_worktrees(results: list["AgentResult"]):
    """Удаляет worktree всех агентов."""
    for r in results:
        try:
            agent_num = int(r.workdir.name.split("-")[-1])
            remove_worktree(agent_num)
        except (ValueError, AttributeError):
            pass


# --- Планировщик ---

def _cleanup_task_branches(task: Task):
    """Удаляет worktree и ветки предыдущего прогона задачи."""
    slug = _translate_slug(task.name)
    # Чистим все worktree агентов
    for d in AGENTS_DIR.glob("agent-*"):
        if d.is_dir():
            run_cmd(["git", "worktree", "remove", str(d), "--force"], cwd=ROOT_DIR, check=False)
    # Удаляем ветки задачи
    for agent_type in ("claude", "gemini"):
        branch = f"task/{task.id.lower()}-{slug}-{agent_type}"
        run_cmd(["git", "branch", "-D", branch], cwd=ROOT_DIR, check=False)


def find_retryable_tasks(tasks: list[Task]) -> list[Task]:
    """Находит задачи для повторного запуска (review, blocked, in_progress)."""
    retryable = []
    for t in tasks:
        status_base = t.status.split(":")[0]
        if status_base in ("review", "blocked", "in_progress"):
            retryable.append(t)
    return sorted(retryable, key=lambda t: t.priority)


def find_ready_tasks(tasks: list[Task]) -> list[Task]:
    """Находит задачи, которые можно взять (open + зависимости done)."""
    done_ids = {t.id for t in tasks if t.status == "done"}
    ready = []
    for t in tasks:
        if t.status != "open":
            continue
        if all(d in done_ids for d in t.deps):
            ready.append(t)
    return sorted(ready, key=lambda t: t.priority)


def review_run_log():
    """Автопроверка: Claude анализирует лог прогона и сообщает о проблемах."""
    log_file = LOG_DIR / "orchestrator.log"
    if not log_file.exists():
        return

    # Берём последние 200 строк лога
    lines = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    recent = "\n".join(lines[-200:])

    prompt = f"""Ты — ревьюер процесса оркестратора мультиагентной разработки.

Проанализируй лог последнего прогона и выдай КРАТКИЙ вердикт (3-5 строк):
- Есть ли ошибки процесса (не кода, а именно флоу оркестратора)?
- Корректно ли выбран победитель? Метрики адекватны?
- Есть ли повторяющиеся паттерны ошибок?

Если всё ок — напиши "✓ Прогон чистый". Если нет — опиши проблему и предложи фикс.
НЕ пиши код. Только диагноз.

--- ЛОГ ---
{recent}
--- КОНЕЦ ---
"""

    try:
        result = run_cmd(
            ["claude", "-p", prompt, "--output-format", "text"],
            cwd=ROOT_DIR, timeout=60, check=False,
        )
        review = (result.stdout or "").strip()
        if review:
            log.info(f"📋 Ревью прогона:\n{review}")
    except Exception as e:
        log.debug(f"Ревью прогона пропущено: {e}")


def _print_next_steps(tasks: list[Task], max_tasks: int, auto: bool):
    """Выводит конкретные следующие шаги после прогона."""
    done_ids = {t.id for t in tasks if t.status == "done"}
    blocked = [t for t in tasks if t.status.startswith("blocked")]
    in_progress = [t for t in tasks if t.status.startswith("in_progress")]
    ready = find_ready_tasks(tasks)

    print(f"\n{'═' * 60}")
    print("  СЛЕДУЮЩИЕ ШАГИ")
    print(f"{'═' * 60}")

    has_action = False

    if ready:
        has_action = True
        if auto:
            log.info(f"Есть ещё задачи: {[t.id for t in ready]}. Запускаю следующий цикл.")
            run_pipeline(max_tasks=max_tasks, auto=True)
            return
        print(f"\n  ▶ Готовы к запуску ({len(ready)}):")
        for t in ready:
            print(f"    {t.id}: {t.name}")
        print(f"\n    → python3 orchestrator.py run")

    if blocked:
        has_action = True
        deps_ready = [t for t in blocked if all(d in done_ids for d in t.deps)]
        deps_waiting = [t for t in blocked if not all(d in done_ids for d in t.deps)]
        if deps_ready:
            print(f"\n  🔄 Blocked, но зависимости готовы (авто-retry при run):")
            for t in deps_ready:
                print(f"    {t.id}: {t.name}")
            print(f"\n    → python3 orchestrator.py run")
        if deps_waiting:
            print(f"\n  ⏸ Blocked, ждут зависимости:")
            for t in deps_waiting:
                waiting = [d for d in t.deps if d not in done_ids]
                print(f"    {t.id}: {t.name} (ждёт: {', '.join(waiting)})")

    if not has_action:
        all_done = all(t.status == "done" for t in tasks)
        if all_done:
            print("\n  ✅ Все задачи выполнены!")
            # Автоматический чекпоинт: make check
            print("\n  🔍 Запускаю make check...")
            check_result = run_cmd(["make", "check"], cwd=ROOT_DIR, timeout=300, check=False)
            if check_result.returncode == 0:
                print("  ✅ make check PASSED — этап закрыт, можно мержить в master")
            else:
                stderr = (check_result.stderr or check_result.stdout or "")[-500:]
                print(f"  ❌ make check FAILED — создаю задачу на фикс...")
                # Автоматически создаём задачу чекпоинта
                _create_checkpoint_task(stderr)
        else:
            print("\n  ℹ Нет задач для выполнения.")

    print(f"{'═' * 60}\n")


def _create_checkpoint_task(error_log: str):
    """Создаёт задачу-чекпоинт для починки make check."""
    tasks = parse_tasks(TASKS_FILE)
    # Проверяем что задача ещё не создана
    for t in tasks:
        if "make check" in (t.acceptance or "") and t.status != "done":
            print(f"  ℹ Задача {t.id} уже существует")
            print(f"\n    → python3 orchestrator.py run")
            return

    max_num = max((int(re.match(r"TASK-(\d+)", t.id).group(1))
                   for t in tasks if re.match(r"TASK-(\d+)", t.id)), default=0)
    next_id = f"TASK-{max_num + 1:03d}"

    error_short = error_log[:200].replace("\n", " ").strip()
    task_block = f"""### {next_id}: Чекпоинт — починить make check
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 4
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: src/
- **Интеграция**: —
- **Описание**: `make check` не проходит. Ошибка: {error_short}. Исправить все проблемы: форматирование (cargo fmt), clippy, падающие тесты.
- **Критерий готовности**: `make check` проходит с exit code 0
- **Дискуссия**: —
- **Агент**: —
- **Ветка**: —"""

    _insert_tasks_into_tasksmd(task_block, "")
    run_cmd(["git", "add", "TASKS.md"], cwd=ROOT_DIR, check=False)
    run_cmd(["git", "commit", "-m", f"auto: {next_id} — чекпоинт make check"], cwd=ROOT_DIR, check=False)
    print(f"  ✅ Создана {next_id}: Чекпоинт — починить make check")
    print(f"\n    → python3 orchestrator.py run")


def preflight_check():
    """Проверяет develop на warnings и merge conflict маркеры, автоматически фиксит."""
    # 1. Проверяем merge conflict маркеры
    result = run_cmd(
        ["grep", "-rl", "<<<<<<<", "src/"],
        cwd=ROOT_DIR, check=False,
    )
    if result.stdout.strip():
        conflicted = result.stdout.strip().split("\n")
        log.error(f"⚠ Merge conflict маркеры в: {conflicted}")
        log.error("  Разреши конфликты вручную перед запуском")
        return False

    # 2. Проверяем сборку и автофиксим warnings
    result = run_cmd(["cargo", "build"], cwd=ROOT_DIR, check=False)
    stderr = result.stderr or ""

    if result.returncode != 0 and "error" in stderr.lower():
        log.error("⚠ develop не собирается! Исправь ошибки перед запуском")
        log.error(stderr[-500:])
        return False

    # Парсим warnings и фиксим тривиальные
    warnings_fixed = 0

    # unused_mut: "remove this `mut`" → убираем mut
    for m in re.finditer(r"--> ([\w/._]+):(\d+):\d+\n.*\n.*remove this `mut`", stderr):
        filepath, lineno = ROOT_DIR / m.group(1), int(m.group(2))
        if filepath.exists():
            lines = filepath.read_text(encoding="utf-8").splitlines()
            if 0 < lineno <= len(lines):
                old = lines[lineno - 1]
                new = re.sub(r"\blet mut\b", "let", old, count=1)
                if new != old:
                    lines[lineno - 1] = new
                    filepath.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    warnings_fixed += 1

    # unused_doc_comments перед макросами: /// → //
    for m in re.finditer(r"--> ([\w/._]+):(\d+):\d+\n.*rustdoc does not generate documentation for macro", stderr):
        filepath, lineno = ROOT_DIR / m.group(1), int(m.group(2))
        if filepath.exists():
            lines = filepath.read_text(encoding="utf-8").splitlines()
            if 0 < lineno <= len(lines):
                old = lines[lineno - 1]
                new = re.sub(r"^\s*///", "//", old)
                if new != old:
                    lines[lineno - 1] = new
                    filepath.write_text("\n".join(lines) + "\n", encoding="utf-8")
                    warnings_fixed += 1

    if warnings_fixed:
        log.info(f"🔧 Preflight: исправлено {warnings_fixed} warning(s) в develop")

    return True


# --- Оценка сложности и авто-декомпозиция ---

MAX_TASK_COMPLEXITY = 3  # макс. сложности (1-5). Выше — декомпозируем.
_assessed_tasks: set[str] = set()  # кэш: задачи, которые уже оценивались и не требуют декомпозиции


def _assess_and_maybe_decompose(task: Task) -> bool:
    """Оценивает сложность задачи через LLM. Если > MAX_TASK_COMPLEXITY — разбивает.
    Возвращает True если задача была декомпозирована."""
    if task.id in _assessed_tasks:
        return False

    # Прошлые неудачи увеличивают score при оценке LLM
    task_prefix = task.id.lower()
    has_past_failures = any(LOG_DIR.glob(f"{task_prefix}-*-attempt*.log"))
    tasks_list = parse_tasks(TASKS_FILE)
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
            if f and _is_valid_path(f):
                filepath = ROOT_DIR / f
                if filepath.exists() and filepath.is_file():
                    content = filepath.read_text(encoding="utf-8", errors="ignore")
                    code_context += f"\n--- {f} ---\n{content[:3000]}\n"

    # Информация о прошлых неудачах
    task_prefix = task.id.lower()
    past_failures = list(LOG_DIR.glob(f"{task_prefix}-*-attempt*.log"))
    failure_context = ""
    if past_failures:
        failure_context = f"\n⚠ Задача ранее провалилась ({len(past_failures)} попыток). Учти это при оценке.\n"

    prompt = f"""Оцени сложность задачи для автономного агента (Claude/Gemini) в bare-metal Rust-проекте.

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
- 4: сложная (затрагивает несколько модулей, unsafe, синхронизация, переключение контекста)
- 5: очень сложная (архитектурные изменения, много взаимосвязей)

Если сложность > {MAX_TASK_COMPLEXITY}, СРАЗУ разбей на 2-4 атомарные подзадачи.

## Формат ответа
Первая строка — СТРОГО:
COMPLEXITY: N

Если N <= {MAX_TASK_COMPLEXITY} — больше ничего не пиши.

Если N > {MAX_TASK_COMPLEXITY} — ниже выведи подзадачи в формате:

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
    result = subprocess.run(
        ["claude", "-p", "-", "--output-format", "text", "--permission-mode", "auto"],
        cwd=ROOT_DIR, input=prompt, capture_output=True, text=True, timeout=120,
    )
    output = result.stdout.strip() if result.stdout else ""

    # Парсим сложность
    complexity_match = re.search(r"COMPLEXITY:\s*(\d)", output)
    if not complexity_match:
        log.warning(f"  ⚠ Не удалось оценить сложность {task.id}, пропускаю")
        return False

    complexity = int(complexity_match.group(1))
    log.info(f"  📊 {task.id} сложность: {complexity}/5 (порог: {MAX_TASK_COMPLEXITY})")

    if complexity <= MAX_TASK_COMPLEXITY:
        _assessed_tasks.add(task.id)
        return False

    # Декомпозиция — подзадачи уже в output
    tasks_block = output[complexity_match.end():].strip()
    if "TASK-" not in tasks_block:
        log.error(f"  ✗ Сложность {complexity} но подзадачи не сгенерированы")
        return False

    # Сохраняем копию
    decompose_file = LOG_DIR / f"{task.id.lower()}-decomposed.md"
    decompose_file.write_text(tasks_block + "\n", encoding="utf-8")

    # Заменяем задачу на подзадачи в TASKS.md
    _insert_tasks_into_tasksmd(tasks_block, task.id)

    # Обновляем зависимости: другие задачи, зависящие от декомпозированной,
    # теперь зависят от последней подзадачи
    new_task_ids = re.findall(r"### (TASK-\d+):", tasks_block)
    if new_task_ids:
        last_subtask = new_task_ids[-1]
        content = TASKS_FILE.read_text(encoding="utf-8")
        # Заменяем зависимость на декомпозированную задачу → последняя подзадача
        content = content.replace(f"TASK-{task.id.split('-')[1]}", f"{last_subtask}")
        # Но не в самих подзадачах — восстановим
        # Проще: заменяем только в строках "Зависимости"
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if "**Зависимости**" in line and task.id in line:
                # Не трогаем подзадачи — они зависят друг от друга корректно
                # Проверяем что это не подзадача
                if i > 0 and any(tid in lines[i-1] for tid in new_task_ids):
                    continue
                lines[i] = line.replace(task.id, last_subtask)
                log.info(f"  🔗 Обновлена зависимость: {task.id} → {last_subtask} в строке {i+1}")
        TASKS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Выводим детали
    log.info(f"  ✓ {task.id} декомпозирована на {len(new_task_ids)} подзадач:")
    new_tasks = parse_tasks(TASKS_FILE)
    done_ids = {t.id for t in new_tasks if t.status == "done"}
    for tid in new_task_ids:
        t = next((t for t in new_tasks if t.id == tid), None)
        if t:
            deps_ready = all(d in done_ids for d in t.deps)
            status = "▶ готова" if deps_ready else f"⏸ ждёт {', '.join(t.deps)}"
            desc_short = (t.description or "")[:100].rstrip()
            log.info(f"    {t.id}: {t.name} [{status}]\n      → {desc_short}")

    # Коммитим декомпозицию в develop чтобы не потерять при checkout/reset
    run_cmd(["git", "add", "TASKS.md"], cwd=ROOT_DIR, check=False)
    run_cmd(
        ["git", "commit", "-m", f"decompose: {task.id} → {', '.join(new_task_ids)}"],
        cwd=ROOT_DIR, check=False,
    )

    return True


def run_pipeline(specific_task: Optional[str] = None, dry_run: bool = False,
                  max_tasks: int = MAX_PARALLEL_TASKS, retry: bool = False, auto: bool = False):
    """Основной цикл оркестратора."""
    ensure_develop_branch()

    if not preflight_check():
        return

    _start_heartbeat()

    tasks = parse_tasks(TASKS_FILE)
    done_count = sum(1 for t in tasks if t.status == "done")
    open_count = len(tasks) - done_count
    log.info(f"Задачи: {open_count} активных, {done_count} завершённых")

    if specific_task:
        # --task позволяет запустить любую задачу кроме done
        ready = [t for t in tasks if t.id == specific_task and t.status != "done"]
        if not ready:
            log.error(f"Задача {specific_task} не найдена или уже done")
            return
        # Сбрасываем статус и чистим старые ветки
        for t in ready:
            _cleanup_task_branches(t)
            update_task_status(t.id, "open")
            t.status = "open"
    elif retry:
        # Режим retry: подбираем упавшие (review, blocked, in_progress)
        retryable = find_retryable_tasks(tasks)
        if retryable:
            log.info(f"Retry: сбрасываю {[t.id for t in retryable]} в open")
            for t in retryable:
                _cleanup_task_branches(t)
                update_task_status(t.id, "open")
                t.status = "open"
        # Ищем все open (включая только что сброшенные)
        tasks = parse_tasks(TASKS_FILE)
        ready = find_ready_tasks(tasks)
    else:
        # Обычный режим: open задачи + авто-retry застрявших с удовлетворёнными зависимостями
        done_ids = {t.id for t in tasks if t.status == "done"}
        stuck_statuses = ("blocked", "in_progress")
        stuck = [t for t in tasks
                 if any(t.status.startswith(s) for s in stuck_statuses)
                 and all(d in done_ids for d in t.deps)]
        if stuck:
            log.info(f"Авто-retry застрявших (зависимости готовы): {[t.id for t in stuck]}")
            for t in stuck:
                _cleanup_task_branches(t)
                update_task_status(t.id, "open")
                t.status = "open"
            tasks = parse_tasks(TASKS_FILE)

        ready = find_ready_tasks(tasks)
        if not ready:
            # Нет open → пробуем retry остальных
            retryable = find_retryable_tasks(tasks)
            if retryable:
                log.info(f"Авто-retry незавершённых: {[t.id for t in retryable]}")
                for t in retryable:
                    update_task_status(t.id, "open")
                    t.status = "open"
                tasks = parse_tasks(TASKS_FILE)
                ready = find_ready_tasks(tasks)

    if not ready:
        log.info("Нет задач для выполнения")
        _print_next_steps(tasks, max_tasks, auto)
        return

    # Разделяем на утверждённые (есть дискуссия с резолюцией) и неутверждённые
    approved = [t for t in ready if is_task_approved(t)]
    not_approved = [t for t in ready if not is_task_approved(t)]

    if not_approved and dry_run:
        log.info(f"⏸ Не утверждены (при run будет обсуждение): {[t.id for t in not_approved]}")

    if not_approved and not dry_run:
        log.info(f"⏸ Требуют обсуждения: {[t.id for t in not_approved]}")
        for t in not_approved:
            topic = _topic_for_task(t)
            filepath = DISCUSS_DIR / f"{topic}.md"
            # Создаём дискуссию если нет
            if not filepath.exists():
                discuss_create(topic, t.description, author="techlead")
                link_task_discussion(t.id, topic)
                log.info(f"  Создана дискуссия: {topic}")
                # Первый раунд: оба агента высказываются
                print("[Claude думает...]")
                discuss_reply(topic, "claude")
                print("[Gemini думает...]")
                discuss_reply(topic, "gemini")
            # Открываем чат для утверждения
            print(f"\n{'═' * 60}")
            print(f"  {t.id}: {t.name}")
            print(f"  Обсуди подход и утверди через /ok")
            print(f"{'═' * 60}\n")
            discuss_chat(topic)
            # Перечитываем — задача могла стать утверждённой
            tasks = parse_tasks(TASKS_FILE)
            if is_task_approved(next((x for x in tasks if x.id == t.id), t)):
                approved.append(t)


    if not approved:
        log.info("Нет утверждённых задач для выполнения")
        return

    # Оценка сложности и авто-декомпозиция
    final_ready = []
    decomposed = False
    for t in approved:
        if _assess_and_maybe_decompose(t):
            decomposed = True
        else:
            final_ready.append(t)

    if decomposed:
        # Перечитываем задачи — появились новые подзадачи
        tasks = parse_tasks(TASKS_FILE)
        new_ready = find_ready_tasks(tasks)
        new_approved = [t for t in new_ready if is_task_approved(t)]
        # Добавляем новые подзадачи (без повторов)
        existing_ids = {t.id for t in final_ready}
        for t in new_approved:
            if t.id not in existing_ids:
                final_ready.append(t)

    ready = final_ready
    if not ready:
        log.info("Нет задач для выполнения (все декомпозированы, ждут зависимости)")
        return

    log.info(f"Утверждены и готовы: {[t.id for t in ready]}")

    if dry_run:
        for t in ready:
            log.info(f"[DRY RUN] {t.id} ({t.name})")
        return

    batch = ready[:max_tasks]
    agent_names = [name for name, _ in AGENTS]

    # Если задач <= агентов — все конкурентные (агенты всё равно свободны)
    if len(batch) <= len(agent_names):
        competitive = list(batch)
        distributed = []
    else:
        # Разделяем: сложные (>=3) → конкурентный, простые (<3) → распределение
        competitive = []
        distributed = []
        for t in batch:
            task_prefix = t.id.lower()
            has_failures = any(LOG_DIR.glob(f"{task_prefix}-*-attempt*.log"))
            score = has_failures * 2 + len((t.description or "")) // 500
            if score >= MAX_TASK_COMPLEXITY:
                competitive.append(t)
            else:
                distributed.append(t)
    total_procs = len(competitive) * len(agent_names) + len(distributed)
    log.info(f"Запускаю: {len(competitive)} конкурентных + {len(distributed)} распределённых = {total_procs} процессов")

    with ThreadPoolExecutor(max_workers=max(total_procs, 1)) as pool:
        futures = {}
        idx = 1

        # Конкурентные задачи — оба агента
        for task in competitive:
            log.info(f"  {task.id} → конкурентный (claude vs gemini)")
            future = pool.submit(execute_task_competitive, task, idx)
            futures[future] = task
            idx += 1

        # Распределённые задачи — round-robin по агентам
        for i, task in enumerate(distributed):
            agent = agent_names[i % len(agent_names)]
            log.info(f"  {task.id} → {agent}")
            future = pool.submit(execute_task_single, task, idx, agent)
            futures[future] = task
            idx += 1

        for future in as_completed(futures):
            task = futures[future]
            try:
                ok = future.result()
                status = "done" if ok else "BLOCKED"
                log.info(f"■ {task.id} → {status}")
            except Exception as e:
                log.error(f"■ {task.id} → ОШИБКА: {e}")
                update_task_status(task.id, "blocked")

    # Автопроверка флоу: Claude анализирует лог и сообщает о проблемах
    review_run_log()

    # Коммитим обновлённые статусы задач
    status_diff = run_cmd(["git", "diff", "--stat", "TASKS.md"], cwd=ROOT_DIR, check=False)
    if status_diff.stdout.strip():
        run_cmd(["git", "add", "TASKS.md"], cwd=ROOT_DIR, check=False)
        run_cmd(["git", "commit", "-m", "update: статусы задач после прогона"], cwd=ROOT_DIR, check=False)
        run_cmd(["git", "push"], cwd=ROOT_DIR, check=False)

    # Перечитываем задачи — формируем следующие шаги
    tasks = parse_tasks(TASKS_FILE)
    _print_next_steps(tasks, max_tasks, auto)

    # Восстанавливаем терминал — claude/gemini CLI могут сломать tty-настройки
    try:
        os.system("stty sane 2>/dev/null")
    except Exception:
        pass


# --- Утилиты ---

def run_cmd(
    cmd: list[str],
    cwd: Path = ROOT_DIR,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Запуск команды с логированием."""
    log.debug(f"$ {' '.join(cmd)} (cwd={cwd})")
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )
        return result
    except subprocess.TimeoutExpired:
        log.error(f"Таймаут ({timeout}с): {' '.join(cmd)}")
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="TIMEOUT")


def slugify(text: str) -> str:
    """Транслитерация и slugify для имён веток."""
    # Простая замена кириллицы
    translit = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
        "ж": "zh", "з": "z", "и": "i", "й": "j", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    result = ""
    for c in text.lower():
        if c in translit:
            result += translit[c]
        elif c.isascii() and (c.isalnum() or c == "-"):
            result += c
        elif c in " _":
            result += "-"
    # Убираем дубли дефисов и обрезаем
    result = re.sub(r"-+", "-", result).strip("-")
    return result[:40]


# --- Дискуссии ---


def _auto_link_discussion(topic: str):
    """Автолинковка: находит задачу по номеру в топике и прописывает дискуссию в TASKS.md."""
    # Ищем номер задачи в начале топика: "035-preemptive-switch" → TASK-035
    m = re.match(r"(\d+)", topic)
    if not m:
        return
    task_id = f"TASK-{int(m.group(1)):03d}"
    tasks = parse_tasks(TASKS_FILE)
    for t in tasks:
        if t.id == task_id:
            link_task_discussion(task_id, topic)
            log.info(f"✓ Дискуссия '{topic}' привязана к {task_id}")
            return
    log.debug(f"Задача для топика '{topic}' не найдена")


def discuss_create(topic: str, question: str, author: str = "techlead"):
    """Создаёт новую дискуссию."""
    DISCUSS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = DISCUSS_DIR / f"{topic}.md"

    if filepath.exists():
        log.error(f"Дискуссия {topic} уже существует")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    content = f"# {topic}\n\n## @{author} ({now})\n\n{question}\n"
    filepath.write_text(content, encoding="utf-8")
    log.info(f"Дискуссия создана: {filepath}")


def discuss_reply(topic: str, agent_type: str):
    """Запускает агента чтобы он ответил в дискуссии."""
    filepath = DISCUSS_DIR / f"{topic}.md"
    if not filepath.exists():
        log.error(f"Дискуссия {topic} не найдена")
        return

    discussion = filepath.read_text(encoding="utf-8")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    prompt = f"""Ты участник архитектурной дискуссии {DISCUSS_CONTEXT}.
Твоя роль: @{agent_type}.

Прочитай дискуссию ниже и напиши свой ответ. Будь конкретен: предлагай структуры,
трейты, алгоритмы. Если не согласен с предыдущим участником — аргументируй.

Отвечай ТОЛЬКО текстом своего сообщения (без заголовка, без форматирования секции).
Пиши на русском.
{CONFIDENCE_INSTRUCTION}

--- ДИСКУССИЯ ---
{discussion}
--- КОНЕЦ ---
"""

    # Для дискуссий — только текстовые CLI (НЕ aider, он правит файлы)
    if agent_type == "gemini":
        result = run_cmd(
            ["gemini", "-p", prompt],
            cwd=ROOT_DIR, timeout=AGENT_TIMEOUT, check=False,
        )
    else:
        result = run_cmd(
            ["claude", "-p", prompt, "--output-format", "text"],
            cwd=ROOT_DIR, timeout=AGENT_TIMEOUT, check=False,
        )
    reply_text = (result.stdout or "").strip() or "(пустой ответ)"

    # Парсим и выводим confidence
    _print_confidence(reply_text, agent_type)
    reply_text = re.sub(r"\n?CONFIDENCE:\s*\d+\s*%\s*$", "", reply_text).rstrip()

    # Дописываем ответ в файл дискуссии
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(f"\n## @{agent_type} ({now})\n\n{reply_text}\n")

    log.info(f"@{agent_type} ответил в {topic}")


def discuss_list():
    """Показывает открытые дискуссии."""
    if not DISCUSS_DIR.exists():
        log.info("Нет дискуссий")
        return

    for f in sorted(DISCUSS_DIR.glob("*.md")):
        if f.name == "README.md":
            continue
        text = f.read_text(encoding="utf-8")
        closed = "ЗАКРЫТО" in text or "РЕЗОЛЮЦИЯ" in text
        status = "ЗАКРЫТО" if closed else "ОТКРЫТО"
        # Считаем участников
        participants = set(re.findall(r"## @(\w+)", text))
        print(f"  [{status}] {f.stem}  участники: {', '.join(sorted(participants))}")


def discuss_show(topic: str):
    """Показывает содержимое дискуссии."""
    filepath = DISCUSS_DIR / f"{topic}.md"
    if not filepath.exists():
        log.error(f"Дискуссия {topic} не найдена")
        return
    print(_format_discussion(filepath.read_text(encoding="utf-8")))


def _format_discussion(text: str) -> str:
    """Форматирует markdown дискуссии для вывода в терминал."""
    lines = text.splitlines()
    result = []
    for line in lines:
        # ## @agent (date) → визуальный разделитель
        m = re.match(r"^## @(\w+)\s*(.*)$", line)
        if m:
            agent = m.group(1)
            meta = m.group(2)
            result.append(f"\n{'═' * 60}")
            result.append(f"  @{agent} {meta}")
            result.append(f"{'═' * 60}")
        # # topic → заголовок
        elif line.startswith("# ") and not line.startswith("## "):
            result.append(f"\n{'━' * 60}")
            result.append(f"  {line[2:]}")
            result.append(f"{'━' * 60}")
        # Убираем сырую строку CONFIDENCE из текста
        elif re.match(r"^CONFIDENCE:\s*\d+\s*%", line.strip()):
            continue
        else:
            result.append(line)
    return "\n".join(result)


def _print_chat_help():
    """Выводит справку по командам чата."""
    print("Команды:")
    print("  (текст)   — ваш комментарий, сохраняется в дискуссию (агенты не вызываются)")
    print("  /claude   — запросить ответ Claude")
    print("  /gemini   — запросить ответ Gemini")
    print("  /both     — оба последовательно (Claude → Gemini)")
    print("  /claude (текст) — записать ваш комментарий, затем вызвать Claude")
    print("  /both (текст)   — записать комментарий, затем вызвать обоих")
    print("  /show     — показать всю дискуссию")
    print("  /ok       — одобрить и закрыть (резолюция генерируется автоматически)")
    print("  /resolve  — написать резолюцию вручную")
    print("  /help     — показать эту справку")
    print("  /exit     — выйти без резолюции")


def discuss_chat(topic: str):
    """Интерактивный REPL-чат по теме. Техлид — модератор, агенты говорят по команде."""
    DISCUSS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = DISCUSS_DIR / f"{topic}.md"

    # Создаём файл если не существует
    if not filepath.exists():
        filepath.write_text(f"# {topic}\n", encoding="utf-8")
        print(f"Создана новая дискуссия: {topic}")

    # Автокомплит команд по Tab
    _chat_commands = ["/claude", "/gemini", "/both", "/show", "/ok", "/resolve", "/help", "/exit"]

    def _completer(text, state):
        buf = readline.get_line_buffer().lstrip()
        options = [c + " " for c in _chat_commands if c.startswith(buf)]
        if state < len(options):
            return options[state]
        return None

    readline.set_completer(_completer)
    readline.set_completer_delims("")  # убираем разделители — матчим всю строку
    readline.parse_and_bind("tab: complete")

    # Показываем текущее состояние
    text = filepath.read_text(encoding="utf-8")
    if text.strip() != f"# {topic}":
        print(_format_discussion(text))
        print("─" * 60)

    _print_chat_help()
    print()

    while True:
        try:
            text = input("TechLead> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            break

        if not text:
            continue

        # Разделяем команду и текст после неё
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        extra = parts[1] if len(parts) > 1 else ""

        if cmd == "/exit":
            break
        elif cmd == "/help":
            _print_chat_help()
            continue
        elif cmd == "/show":
            print(filepath.read_text(encoding="utf-8"))
            continue
        elif cmd in ("/claude", "/gemini", "/both"):
            # Если есть текст после команды — сначала записываем как сообщение техлида
            if extra:
                _chat_append(filepath, "techlead", extra)
            if cmd in ("/claude", "/both"):
                _chat_agent_reply(filepath, "claude")
            if cmd in ("/gemini", "/both"):
                _chat_agent_reply(filepath, "gemini")
            # Подсказка после ответов
            print("─" * 60)
            print("  Введите текст — добавить свой комментарий в дискуссию")
            print("  /both — пусть оба прокомментируют   /ok — одобрить и закрыть")
            print("  /help — все команды")
            print("─" * 60)
            continue
        elif cmd == "/ok":
            comment = extra or ""
            if comment:
                _chat_append(filepath, "techlead", comment)
            # Финальный раунд: оба агента высказывают последние замечания
            _chat_append(filepath, "techlead",
                         "Я готов утвердить. Ваши финальные замечания или возражения? "
                         "Если согласны — напишите 'согласен'. Если нет — аргументируйте.")
            print("[Финальный раунд — оба агента высказываются перед закрытием]\n")
            _chat_agent_reply(filepath, "claude")
            _chat_agent_reply(filepath, "gemini")
            # Генерируем резолюцию
            _chat_auto_resolve(filepath)
            _post_resolve(filepath)
            # Автолинковка: если топик соответствует задаче, прописываем дискуссию
            _auto_link_discussion(topic)
            break
        elif cmd == "/resolve":
            resolve_msg = extra or input("Резолюция> ").strip()
            if resolve_msg:
                _chat_append(filepath, "techlead", f"**РЕЗОЛЮЦИЯ:** {resolve_msg}")
                print("Дискуссия закрыта.")
            _post_resolve(filepath)
            _auto_link_discussion(topic)
            break
        else:
            # Обычный текст от техлида
            _chat_append(filepath, "techlead", text)


def _post_resolve(filepath: Path):
    """После закрытия дискуссии — генерирует задачи и вставляет их в TASKS.md."""
    discussion = filepath.read_text(encoding="utf-8")
    topic = filepath.stem

    # Определяем следующий свободный номер задачи
    tasks = parse_tasks(TASKS_FILE)
    max_num = 0
    for t in tasks:
        m = re.match(r"TASK-(\d+)", t.id)
        if m:
            max_num = max(max_num, int(m.group(1)))
    next_task_num = max_num + 1

    # Находим связанную задачу (если есть) для замены
    linked_task_id = ""
    m = re.match(r"(\d+)", topic)
    if m:
        linked_task_id = f"TASK-{int(m.group(1)):03d}"

    prompt = f"""Прочитай закрытую дискуссию и сгенерируй блок задач для TASKS.md.

Формат каждой задачи — строго такой:

### TASK-XXX: Название
- **Статус**: open
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: TASK-YYY или —
- **Файлы (новые)**: src/path/file.rs
- **Файлы (modify)**: — или путь
- **Интеграция**: что добавить в lib.rs/main.rs при мерже
- **Описание**: что именно реализовать (конкретно, со ссылкой на решения из дискуссии)
- **Критерий готовности**: что должно работать
- **Дискуссия**: {topic}
- **Агент**: —
- **Ветка**: —

Правила:
- Нумерация задач начинается с TASK-{next_task_num:03d}
- Если дискуссия привязана к задаче {linked_task_id} — первая подзадача ЗАМЕНЯЕТ её (используй тот же номер {linked_task_id}), остальные нумеруются с TASK-{next_task_num:03d}
- Задачи должны быть атомарными (одна задача = один файл/модуль)
- Зависимости между задачами должны быть указаны
- Пиши на русском

Выведи ТОЛЬКО блок задач в формате markdown, без пояснений.

--- ДИСКУССИЯ ---
{discussion}
--- КОНЕЦ ---
"""

    print("\n[Генерирую задачи из дискуссии...]")
    result = subprocess.run(
        ["claude", "-p", "-", "--output-format", "text",
         "--permission-mode", "auto"],
        cwd=ROOT_DIR, input=prompt, capture_output=True, text=True, timeout=AGENT_TIMEOUT,
    )
    tasks_block = result.stdout.strip() if result.stdout else ""

    if not tasks_block or tasks_block.startswith("Error:"):
        log.error(f"Не удалось сгенерировать задачи: {tasks_block or '(пустой ответ)'}")
        return

    # Сохраняем копию рядом с дискуссией (для истории)
    tasks_file = filepath.parent / f"{topic}-tasks.md"
    tasks_file.write_text(tasks_block + "\n", encoding="utf-8")

    # Вставляем задачи в TASKS.md
    _insert_tasks_into_tasksmd(tasks_block, linked_task_id)

    print(f"\n  ✓ Задачи сгенерированы и вставлены в TASKS.md")
    print(f"  ✓ Копия: {tasks_file}")
    log.info(f"{linked_task_id or topic}: подзадачи вставлены в TASKS.md")
    print(f"\n    → python3 orchestrator.py run\n")


def _insert_tasks_into_tasksmd(tasks_block: str, linked_task_id: str):
    """Вставляет сгенерированные задачи в TASKS.md, заменяя связанную задачу если есть."""
    content = TASKS_FILE.read_text(encoding="utf-8")

    if linked_task_id:
        # Заменяем блок связанной задачи на новые подзадачи
        # Находим начало и конец блока задачи
        pattern = rf"(### {re.escape(linked_task_id)}: .+?)(?=\n### TASK-|\n---|\Z)"
        m = re.search(pattern, content, re.DOTALL)
        if m:
            content = content[:m.start()] + tasks_block.rstrip() + "\n" + content[m.end():]
        else:
            # Задача не найдена — вставляем перед "---\n\n## Карта параллелизма"
            content = _append_before_footer(content, tasks_block)
    else:
        content = _append_before_footer(content, tasks_block)

    TASKS_FILE.write_text(content, encoding="utf-8")


def _append_before_footer(content: str, tasks_block: str) -> str:
    """Вставляет блок задач перед секцией 'Карта параллелизма' или в конец трека."""
    marker = "## Карта параллелизма"
    idx = content.find(marker)
    if idx != -1:
        # Вставляем перед маркером (с разделителем)
        return content[:idx] + tasks_block.rstrip() + "\n\n---\n\n" + content[idx:]
    # Нет маркера — добавляем в конец
    return content.rstrip() + "\n\n" + tasks_block.rstrip() + "\n"


def _chat_auto_resolve(filepath: Path):
    """Техлид одобрил — агент суммирует дискуссию в резолюцию."""
    discussion = filepath.read_text(encoding="utf-8")

    prompt = f"""Прочитай дискуссию и напиши КРАТКУЮ резолюцию (3-5 строк):
- Какое решение принято по каждому вопросу
- Порядок реализации (пронумерованный список)

Пиши на русском. Только текст резолюции, без заголовков.

--- ДИСКУССИЯ ---
{discussion}
--- КОНЕЦ ---
"""

    print("[Генерирую резолюцию...]")
    result = subprocess.run(
        ["claude", "-p", "-", "--output-format", "text",
         "--permission-mode", "auto"],
        cwd=ROOT_DIR, input=prompt, capture_output=True, text=True, timeout=AGENT_TIMEOUT,
    )
    summary = result.stdout.strip() if result.stdout else "(не удалось сгенерировать)"
    print(f"\n{summary}\n")

    _chat_append(filepath, "techlead", f"**РЕЗОЛЮЦИЯ (одобрено):**\n\n{summary}")
    print("Дискуссия закрыта.")


def _print_confidence(text: str, agent_type: str):
    """Парсит CONFIDENCE: XX% из ответа агента и печатает футер."""
    m = re.search(r"CONFIDENCE:\s*(\d+)\s*%", text)
    if m:
        pct = int(m.group(1))
        label = f"  {agent_type} confidence: {pct}%  "
        width = max(len(label) + 4, 40)
        bar = "═" * width
        pad = (width - len(label)) // 2
        print(f"\n{bar}")
        print(f"{'═' * pad}{label}{'═' * (width - pad - len(label))}")
        print(f"{bar}\n")


def _chat_append(filepath: Path, role: str, message: str):
    """Дописывает сообщение в файл дискуссии."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(f"\n## @{role} ({now})\n\n{message}\n")


def _chat_agent_reply(filepath: Path, agent_type: str):
    """Вызывает агента со стримингом вывода, дописывает в файл."""
    discussion = filepath.read_text(encoding="utf-8")

    prompt = f"""Ты участник архитектурной дискуссии {DISCUSS_CONTEXT}.
Твоя роль: @{agent_type}.

Прочитай дискуссию и напиши свой ответ. Будь конкретен: предлагай структуры,
трейты, алгоритмы. Если не согласен — аргументируй. Отвечай кратко и по делу.
Пиши на русском. Выведи ТОЛЬКО текст ответа, без заголовков и метаданных.

ВАЖНО: ты НЕ принимаешь решений. Только @techlead утверждает план и выносит резолюции.
Ты можешь предлагать и рекомендовать, но НИКОГДА не пиши "план утверждён", "решено", "фиксируем" и т.п.
{CONFIDENCE_INSTRUCTION}

--- ДИСКУССИЯ ---
{discussion}
--- КОНЕЦ ---
"""

    if agent_type == "gemini":
        cmd = ["gemini", "-p", prompt]
    else:
        cmd = ["claude", "-p", "-", "--output-format", "text",
               "--permission-mode", "auto"]

    # Стриминг через Popen с спиннером
    reply_lines = []
    spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    try:
        proc = subprocess.Popen(
            cmd, cwd=ROOT_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.PIPE, text=True, bufsize=1,
        )
        if agent_type == "claude":
            proc.stdin.write(prompt)
        proc.stdin.close()

        # Спиннер пока ждём первый вывод
        spin_idx = 0
        print(f"{agent_type.capitalize()}> ", end="", flush=True)
        got_output = False
        while True:
            ready, _, _ = select.select([proc.stdout], [], [], 0.15)
            if ready:
                line = proc.stdout.readline()
                if not line:
                    break
                if not got_output:
                    # Стираем спиннер
                    print("\r" + " " * 40 + f"\r{agent_type.capitalize()}> ", end="", flush=True)
                    got_output = True
                print(line, end="", flush=True)
                reply_lines.append(line)
            else:
                if proc.poll() is not None:
                    # Процесс завершился, дочитываем остаток
                    for line in proc.stdout:
                        if not got_output:
                            print("\r" + " " * 40 + f"\r{agent_type.capitalize()}> ", end="", flush=True)
                            got_output = True
                        print(line, end="", flush=True)
                        reply_lines.append(line)
                    break
                # Крутим спиннер только до первого вывода
                if not got_output:
                    ch = spinner_chars[spin_idx % len(spinner_chars)]
                    print(f"\r{agent_type.capitalize()}> {ch} думает...", end="", flush=True)
                    spin_idx += 1
        proc.wait(timeout=AGENT_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        print("\n[ТАЙМАУТ]")
    except KeyboardInterrupt:
        proc.kill()
        proc.wait()
        print("\n[Прервано]")
        return
    except FileNotFoundError:
        print(f"\n[ОШИБКА: команда '{cmd[0]}' не найдена]")
        return

    reply = "".join(reply_lines).strip()
    if not reply:
        stderr = proc.stderr.read() if proc.stderr else ""
        if stderr:
            print(f"\n[stderr: {stderr[:500]}]")
        reply = "(пустой ответ)"
    print()

    # Парсим и выводим confidence, убираем из текста
    _print_confidence(reply, agent_type)
    reply = re.sub(r"\n?CONFIDENCE:\s*\d+\s*%\s*$", "", reply).rstrip()

    _chat_append(filepath, agent_type, reply)


# --- Команды: merge-pending, status ---

def merge_pending_tasks():
    """Мержит все review-задачи в develop."""
    ensure_develop_branch()
    tasks = parse_tasks(TASKS_FILE)
    review_tasks = [t for t in tasks if t.status.startswith("review:")]

    if not review_tasks:
        print("Нет задач в статусе review.")
        return

    for t in review_tasks:
        branch = t.branch
        if not branch or branch == "—":
            print(f"  ⚠ {t.id} ({t.name}) — нет ветки, пропускаю")
            continue

        # Проверяем что ветка существует
        result = run_cmd(["git", "branch", "--list", branch], cwd=ROOT_DIR, check=False)
        if not result.stdout.strip():
            print(f"  ⚠ {t.id} — ветка {branch} не найдена, пропускаю")
            continue

        print(f"  → Мержу {t.id} ({t.name}): {branch} → {DEV_BRANCH}...")
        if merge_to_develop(branch, t.id):
            update_task_status(t.id, "done")
            print(f"  ✓ {t.id} → done")
        else:
            print(f"  ✗ {t.id} — конфликт, нужен ручной мерж")

    print()
    show_status()


def show_status():
    """Показывает статус всех задач."""
    tasks = parse_tasks(TASKS_FILE)
    status_groups = {}
    for t in tasks:
        s = t.status.split(":")[0]
        status_groups.setdefault(s, []).append(t)

    for status in ["done", "review", "in_progress", "open", "blocked"]:
        group = status_groups.get(status, [])
        if not group:
            continue
        icons = {"done": "✓", "review": "⏳", "in_progress": "▶", "open": "○", "blocked": "✗"}
        icon = icons.get(status, "?")
        print(f"\n{icon} {status.upper()} ({len(group)}):")
        for t in group:
            agent_info = f" [{t.agent}]" if t.agent and t.agent != "—" else ""
            deps_info = f" (ждёт: {', '.join(t.deps)})" if t.deps and status == "open" else ""
            print(f"    {t.id}: {t.name}{agent_info}{deps_info}")
    print()


# --- Entry point ---

def main():
    parser = argparse.ArgumentParser(description="Оркестратор")
    sub = parser.add_subparsers(dest="command")

    # Команда по умолчанию — запуск задач
    run_p = sub.add_parser("run", help="Запустить задачи из TASKS.md")
    run_p.add_argument("--task", help="Конкретная задача (TASK-032)")
    run_p.add_argument("--retry", action="store_true", help="Перезапустить упавшие (review/blocked/in_progress)")
    run_p.add_argument("--dry-run", action="store_true")
    run_p.add_argument("--auto", action="store_true", help="Автоматически запускать разблокированные задачи")
    run_p.add_argument("--max-tasks", type=int, default=MAX_PARALLEL_TASKS,
                        help="Макс. задач параллельно (агентов = задачи × кол-во моделей)")

    # Дискуссии
    disc_p = sub.add_parser("discuss", help="Управление дискуссиями")
    disc_sub = disc_p.add_subparsers(dest="disc_cmd")

    disc_new = disc_sub.add_parser("new", help="Создать дискуссию")
    disc_new.add_argument("topic", help="Имя темы (001-scheduler-design)")
    disc_new.add_argument("question", help="Вопрос / начальное сообщение")
    disc_new.add_argument("--author", default="techlead")

    disc_reply = disc_sub.add_parser("reply", help="Агент отвечает в дискуссии")
    disc_reply.add_argument("topic", help="Имя темы")
    disc_reply.add_argument("--agent", required=True, choices=["claude", "gemini"])

    disc_list = disc_sub.add_parser("list", help="Список дискуссий")

    disc_show = disc_sub.add_parser("show", help="Показать дискуссию")
    disc_show.add_argument("topic", help="Имя темы")

    disc_chat = disc_sub.add_parser("chat", help="Интерактивный чат по теме")
    disc_chat.add_argument("topic", help="Имя темы")

    disc_regen = disc_sub.add_parser("regen", help="Перегенерировать задачи из закрытой дискуссии")
    disc_regen.add_argument("topic", help="Имя темы")

    # Мерж pending review задач в develop
    sub.add_parser("merge-pending", help="Промержить все review-задачи в develop")

    # Статус задач
    sub.add_parser("status", help="Показать статус всех задач")

    # Общие флаги
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Дискуссии
    if args.command == "discuss":
        if args.disc_cmd == "new":
            discuss_create(args.topic, args.question, args.author)
        elif args.disc_cmd == "reply":
            discuss_reply(args.topic, args.agent)
        elif args.disc_cmd == "list":
            discuss_list()
        elif args.disc_cmd == "show":
            discuss_show(args.topic)
        elif args.disc_cmd == "chat":
            discuss_chat(args.topic)
        elif args.disc_cmd == "regen":
            filepath = DISCUSS_DIR / f"{args.topic}.md"
            if not filepath.exists():
                log.error(f"Дискуссия {args.topic} не найдена")
            elif "РЕЗОЛЮЦИЯ" not in filepath.read_text(encoding="utf-8"):
                log.error(f"Дискуссия {args.topic} не закрыта (нет РЕЗОЛЮЦИИ)")
            else:
                _post_resolve(filepath)
        else:
            disc_p.print_help()
        return

    # Мерж pending
    if args.command == "merge-pending":
        merge_pending_tasks()
        return

    # Статус
    if args.command == "status":
        show_status()
        return

    # Запуск задач — только по явной команде 'run'
    if args.command != "run":
        parser.print_help()
        return

    max_tasks = getattr(args, "max_tasks", MAX_PARALLEL_TASKS)
    log.info("=" * 60)
    log.info("Оркестратор запущен")
    log.info(f"Корень: {ROOT_DIR}")
    log.info(f"Агенты: {[n for n, _ in AGENTS]}")
    log.info(f"Макс. задач параллельно: {max_tasks}")
    log.info("=" * 60)

    run_pipeline(
        specific_task=getattr(args, "task", None),
        dry_run=getattr(args, "dry_run", False),
        max_tasks=max_tasks,
        retry=getattr(args, "retry", False),
        auto=getattr(args, "auto", False),
    )

    log.info("Оркестратор завершён")


if __name__ == "__main__":
    import signal

    def _force_exit(*_):
        print("\nПрервано. Убиваю дочерние процессы...")
        # Убиваем всё дерево процессов через process group
        try:
            os.killpg(os.getpgid(os.getpid()), 9)
        except ProcessLookupError:
            pass
        os._exit(1)

    # Создаём свою группу процессов чтобы os.killpg убил всех потомков
    os.setpgrp()
    signal.signal(signal.SIGINT, _force_exit)
    signal.signal(signal.SIGTERM, _force_exit)
    main()
