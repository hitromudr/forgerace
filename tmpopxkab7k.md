Ты автономный агент разработки .

## Документация проекта (CLAUDE.md)
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Проект

ForgeRace — мультиагентный оркестратор разработки. Запускает несколько AI-агентов (Claude, Gemini и др.) параллельно на задачах кодирования, с крест-на-крест ревью, авто-декомпозицией и race-to-merge.

## Команды

```bash
# Инициализация в проекте (создаёт forgerace.toml, TASKS.md, PROJECT_BRIEF.md, обёртку ./fr)
python3 forgerace.py init

# Запуск пайплайна
python3 forgerace.py run
python3 forgerace.py run --task TASK-032        # конкретная задача
python3 forgerace.py run --retry                # перезапуск упавших
python3 forgerace.py run --auto --max-tasks 4   # автозапуск разблокированных

# С конфигом
python3 forgerace.py --config examples/ethos.toml run

# Дискуссии
python3 forgerace.py discuss new my-feature 'Как реализовать X?'
python3 forgerace.py discuss chat my-feature
python3 forgerace.py discuss list
python3 forgerace.py discuss show my-feature
python3 forgerace.py discuss regen my-feature   # перегенерация задач из резолюции

# Статус и мерж
python3 forgerace.py status
python3 forgerace.py merge-pending
```

После `init` можно использовать `./fr` вместо `python3 forgerace.py`.

Тестов пока нет. При добавлении — pytest.

## Архитектура

Рефакторинг из монолита завершён. `orchestrator_monolith.py` УДАЛЁН — НЕ создавать и НЕ править его. Весь код в `forgerace/`:

- **config.py** — `Config` (dataclass) + загрузка из `forgerace.toml`. Глобальный `cfg` обновляется in-place через `init_config()`, чтобы все модули видели изменения через `from .config import cfg`.
- **tasks.py** — парсер `TASKS.md`, модель `Task`, `update_task_status`, `find_ready_tasks`.
- **agents.py** — запуск agent-процессов (`run_agent_process`), стриминг событий (`_log_claude_event`, `_log_gemini_event`), `build_prompt`. Агенты — внешние CLI (`claude`, `gemini`), общение через stdin/stream-json.
- **review.py** — кросс-ревью: `code_review` запускает агентов-ревьюеров друг на друга, парсит `VERDICT:`/`COMMENTS:`. `send_to_rework` возвращает агенту замечания.
- **pipeline.py** — главный цикл: `run_pipeline` → `execute_task_competitive` (все агенты на одну задачу, race-to-merge). Heartbeat-поток мониторит прогресс. `_pre_check` валидирует задачу перед запуском.
- **decompose.py** — LLM оценивает сложность задачи, при необходимости разбивает на подзадачи и вставляет в TASKS.md.
- **discuss.py** — система архитектурных дискуссий: агенты обсуждают подход до реализации, auto-resolve при достижении консенсуса (CONFIDENCE ≥ 80%). Интерактивный чат с командами: `/solo` (чистый контекст без дискуссии), `/fresh` (вводные без истории), `/compact` (LLM-сжатие с сохранением якорей), `/undo`, `/reopen`, `/show N`, `/stats`, `/summary`, `/tasks`, `/cd` (смена рабочей директории агентов). Агенты solo/fresh запускаются из `/tmp` для изоляции от файлов проекта.
- **cost.py** — `TokenUsage` (dataclass) для учёта токенов и оценки стоимости LLM-вызовов.
- **task_queue.py** — приоритетная очередь задач (`heapq`) + `ConcurrencyLimiter` для ограничения параллельного выполнения.
- **worktree.py** — git worktree для изоляции агентов (каждый агент работает в своём worktree).
- **merge.py** — мерж через detached worktree + `git update-ref` (без checkout в основном repo).
- **utils.py** — `run_cmd`, `slugify`, валидация путей, логирование.
- **cli.py** — argparse, обработка сигналов (SIGINT убивает process group).

Точка входа: `forgerace.py` → `forgerace.cli.main_with_signal_handling()`.

### Ключевые паттерны

- **Competitive mode**: все сконфигурированные агенты получают одну задачу параллельно. Первый прошедший кросс-ревью мержится, остальные убиваются.
- **Конфиг in-place**: `cfg` — глобальный singleton. `init_config()` мутирует его поля, не заменяет объект. Это критично — иначе `from .config import cfg` в других модулях будет указывать на старый объект.
- **Worktree изоляция**: каждый агент работает в отдельном git worktree. Мерж — через отдельный detached worktree + `update-ref`, чтобы не трогать рабочее дерево.
- **Progress timeout**: агент убивается, если его diff не меняется `progress_timeout` секунд.
- **Signal handling**: `run` создаёт process group (`setpgrp`) и ловит SIGINT/SIGTERM для убийства всех дочерних процессов. `discuss` использует обычный Ctrl+C. Pipeline завершается через `os._exit(0)` для гарантированного убийства застрявших потоков.
- **Дефолты build_commands** пустые — задавать через `[build]` в TOML. `lint_fix` — авто-фикс линтером после агента (до build_commands), изменения коммитятся автоматически. `review_run_log` опциональна (`[limits] review_run_log = true`).
- **Промпты агентам через stdin**: все агенты (claude, gemini) получают промпт через stdin, не через CLI-аргументы (ограничение на длину аргументов ОС).
- **Контекст проекта**: `init` генерирует `PROJECT_BRIEF.md` (LLM-анализ README, зависимостей, структуры) для архитектурных дискуссий. При старте `cfg.project_docs` загружает `PROJECT_BRIEF.md` (приоритет) или `CLAUDE.md` (fallback). В промпты инжектится только не-claude агентам (claude CLI сам читает CLAUDE.md). В discuss при >80K символов — auto-compact.

## Конфигурация

Всё настраивается через `forgerace.toml`. См. `examples/example.toml` для полного примера. Секции: `[project]`, `[agents.*]`, `[build]`, `[limits]`, `[rules]`, `[metrics]`. Без TOML-файла работают дефолты из `Config` dataclass.

Порядок поиска конфига: `--config` → `forgerace.toml` вверх по дереву от CWD → `~/.forgerace-last` (последний использованный) → дефолты.

Python 3.10+. Зависимость `tomli` нужна только для Python < 3.11 (в 3.11+ есть `tomllib`).

## Соглашения

- Python 3.10+
- Комментарии: на русском или английском
- Каждый модуль должен быть импортируемым отдельно

## Известные баги/TODO

1. Gemini как ревьюер ненадёжен — ложные APPROVED. Решено: валидация COMMENTS > 20 символов в review.py.
2. Декомпозиция должна обновлять зависимости в других задачах (частично реализовано).


ЗАПРЕЩЕНО РЕДАКТИРОВАТЬ: TASKS.md, CLAUDE.md, forgerace.toml, .gitignore — это файлы оркестратора.
Правь ТОЛЬКО файлы указанные в секции "Файлы".

## Твоя задача: TASK-032 — Реализация команды ./fr doctor

реализовать команду `./fr doctor` для диагностики и автолечения среды, включая проверку валидности TOML-файла, наличия и корректности `PROJECT_BRIEF.md`, наличия директорий `logs/` и `docs/discuss/`, проверку наличия файла, указанного в `discuss_context`, проверку агентов, LiteLLM и очистку зависших worktrees

## Файлы
- Создать: forgerace/doctor.py
- Изменить: forgerace/pipeline.py, forgerace/cli.py

## Интеграция
добавить вызов `doctor()` в `cli.py` для команды `doctor`

## Критерий готовности
команда `./fr doctor` должна успешно запускаться и производить диагностику среды

## ЗАПРЕЩЕНО (kill box этой задачи)
использовать внешние зависимости, менять сигнатуры существующих функций

## Обязательная проверка
После реализации ОБЯЗАТЕЛЬНО выполни эту команду. Задача НЕ считается выполненной пока команда не вернёт exit code 0:
```
`python -m forgerace.doctor`
```

## Правила
- Правь ТОЛЬКО файлы указанные в "Файлы" выше. Не переписывай файлы целиком — делай точечные правки.


## Current file contents

--- forgerace/pipeline.py ---
"""Основной пайплайн: запуск агентов, верификация, конкурентный/одиночный режим."""

import atexit
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

# Optional dependency for CPU/IO metrics
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

from .agents import AgentResult, build_prompt, run_agent_process, build_post_mortem_prompt, run_text_agent
from .benchmark import BenchmarkStore
from .config import cfg, run_hint, run_hook
from .cost import TokenUsage
from .decompose import assess_and_maybe_decompose, create_checkpoint_task, get_task_complexity
from .merge import merge_to_develop
from .review import code_review, get_changed_files, get_diff, send_to_rework, single_review
from .tasks import (
    Task, find_ready_tasks, find_retryable_tasks, is_task_approved,
    parse_tasks, task_paths, topic_for_task, translate_slug,
    update_task_status, link_task_discussion,
)
from .types import MergeResult
from .utils import log, run_cmd, is_valid_path, C, R, agent_color, parse_pytest_output
from .worktree import cleanup_worktrees, create_worktree


# --- Heartbeat ---

@dataclass
class AgentStatus:
    """Статус активного агента для heartbeat и dashboard."""
    task_id: str
    agent_type: str
    workdir: Path
    start_time: float
    usage: TokenUsage = field(default_factory=TokenUsage)
    elapsed_sec: int = 0
    files_changed: list[str] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        return self.usage.estimated_usd


_active_agents: dict[str, AgentStatus] = {}
_active_agents_lock = threading.Lock()
_perf_stats: dict[int, list[dict]] = {}
_heartbeat_stop_event = threading.Event()
_heartbeat_thread: threading.Thread | None = None

# --- Round-robin ---
_rr_agent_index = 0


def _heartbeat_loop(interval: int = 15):
    """Фоновый поток: прогресс активных агентов."""
    while not _heartbeat_stop_event.is_set():
        time.sleep(interval)
        with _active_agents_lock:
            agents = dict(_active_agents)
        
        max_diff_duration = 0.0
        
        for tag, status in agents.items():
            status.elapsed_sec = int(time.time() - status.start_time)
            mins, secs = divmod(status.elapsed_sec, 60)
            try:
                if not status.workdir.exists():
                    continue
                
                t0 = time.time()
                result = subprocess.run(
                    ["git", "diff", "--name-only"],
                    cwd=status.workdir, capture_output=True, text=True, timeout=5,
                )
                duration = time.time() - t0
                max_diff_duration = max(max_diff_duration, duration)
                
                files = [f.strip() for f in (result.stdout or "").strip().split("\n") if f.strip()]
                status.files_changed = files
                
                if files:
                    files_str = ", ".join(f.rsplit("/", 1)[-1] for f in files[:5])
                    if len(files) > 5:
                        files_str += f" (+{len(files) - 5})"
                    log.info(f"[{tag}] ⏳ {mins}m{secs:02d}s (${status.cost_usd:.2f}) — правит: {files_str}")
                else:
                    log.info(f"[{tag}] ⏳ {mins}m{secs:02d}s (${status.cost_usd:.2f}) — читает код...")
            except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
                continue
                
        # Агрегация системных метрик (снаружи цикла, один раз за heartbeat)
        if HAS_PSUTIL:
            active_count = len(agents)
            if active_count not in _perf_stats:
                _perf_stats[active_count] = []
            
            perf_metrics = {
                "cpu_percent": psutil.cpu_percent(),
                "io_read": psutil.disk_io_counters().read_bytes if
... (truncated)

--- forgerace/cli.py ---
"""CLI точка входа: argparse, команды run/discuss/status/merge-pending."""

import argparse
import os
import signal
import sys
# Import error‑path test runner (optional integration)
try:
    from .test_error_paths import run_error_path_tests
    # If the benchmark is invoked with a special flag, run the tests.
    if "--test-error-paths" in sys.argv:
        exit_code = run_error_path_tests()
        sys.exit(exit_code)
except Exception:
    # Silently ignore if the test module is not present or any import error occurs.
    pass
from pathlib import Path

from .config import cfg, init_config, run_hint
from .discuss import discuss_chat, discuss_create, discuss_list, discuss_reply, discuss_show
from .merge import merge_to_develop
from .utils import C, R, agent_color
from .pipeline import run_pipeline
from .tasks import parse_tasks, update_task_status
from .types import MergeResult
from .decompose import create_checkpoint_task
from .utils import log, run_cmd, setup_logging
from typing import Optional
from .benchmark import BenchmarkStore
# web_server, checkpoint, diagnose_engine — created by agents, not yet merged
# from .web_server import create_web_server


_AGENT_CONFIGS = {
    "claude": {
        "command": "claude",
        "args": '["-p", "--allowedTools", "Read,Write,Edit,Bash,Grep,Glob,WebFetch,WebSearch",\n'
                '        "--max-turns", "50", "--output-format", "stream-json", "--verbose"]',
        "review_args": '["-p", "-", "--output-format", "text", "--permission-mode", "auto"]',
        "timeout": 300,
    },
    "gemini": {
        "command": "gemini",
        "args": '["-p", "--approval-mode", "yolo", "--output-format", "stream-json"]',
        "review_args": '["-p", "-"]',
        "timeout": 180,
    },
    "qwen": {
        "command": "qwen",
        "args": '["-p", "--approval-mode", "yolo", "--output-format", "stream-json"]',
        "review_args": '["-p", "-", "--approval-mode", "yolo"]',
        "timeout": 180,
    },
}


def _build_init_toml(name: str, detected_agents: list[str], has_frames: bool) -> str:
    """Генерирует forgerace.toml с автодетектом агентов и фреймами."""
    lines = [
        '# ForgeRace configuration',
        '# Docs: https://github.com/hitromudr/forgerace',
        '',
        '[project]',
        f'name = "{name}"',
        'root = "."',
        '# dev_branch = "main"  # default: current branch',
        'discuss_dir = "docs/discuss"',
    ]

    # Frames
    if has_frames:
        lines += [
            '',
            '# ── Когнитивные фреймы ──────────────────────────────────────',
            '# /agent+frame в дискуссиях: /qwen+audit, /gemini+wild ...',
            '# Сценарий: /qwen+audit → /qwen+wild → /qwen+price',
            '',
            '[frames.audit]',
            'description = "8 осей выживаемости решения"',
            'file = "frames/audit.md"',
            '',
            '[frames.wild]',
            'description = "дикие стратегии, инверсия, слом шаблонов"',
            'file = "frames/wild.md"',
            '',
            '[frames.price]',
            'description = "цена выбора, trade-off матрицы, арбитраж"',
            'file = "frames/price.md"',
            '',
            '[frames.theory]',
            'description = "от принципов к реализации"',
            'file = "frames/theory.md"',
            '',
            '[frames.evidence]',
            'description = "от фактов к обобщению"',
            'file = "frames/evidence.md"',
            '',
            '[frames.optimizer]',
            'description = "4-фазный разбор: grounding → divergence → red-teaming"',
            'file = "frames/optimizer.md"',
   

... (prompt truncated)