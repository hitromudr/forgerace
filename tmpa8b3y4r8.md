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

## Твоя задача: TASK-034 — Реализовать команду ./fr task add

Добавить в forgerace/task_cmd.py функцию add_task(name, priority="P1", depends="—", files_new="—", files_modify="—", description="—", discussion="—"). Функция: (1) вычисляет следующий свободный TASK-{NUM} через parse_tasks(), (2) формирует markdown-блок задачи со всеми 14 полями (Статус, Приоритет, Этап, Зависимости, Файлы новые, Файлы modify, Интеграция, Описание, Запрещено, Проверка, Критерий готовности, Дискуссия, Агент, Ветка), (3) записывает в cfg.tasks_file через tasks_file_lock() + _atomic_write(). ID генерируется ВНУТРИ tasks_file_lock чтобы избежать дублей. Вспомогательные: _next_task_id() — следующий ID, _format_task_md() — формирование блока. Выводит созданную задачу в терминал с цветами из utils.C.

## Файлы
- Создать: —
- Изменить: forgerace/task_cmd.py

## Интеграция
НЕ трогать cli.py (защищённый файл). Только forgerace/task_cmd.py

## Критерий готовности
add_task("Test task") создаёт запись в TASKS.md с правильным форматом

## ЗАПРЕЩЕНО (kill box этой задачи)
readlines(), внешние зависимости, модификация cli.py

## Обязательная проверка
После реализации ОБЯЗАТЕЛЬНО выполни эту команду. Задача НЕ считается выполненной пока команда не вернёт exit code 0:
```
python3 -c "from forgerace.task_cmd import add_task; print('OK')"
```

## Правила
- Правь ТОЛЬКО файлы указанные в "Файлы" выше. Не переписывай файлы целиком — делай точечные правки.


## Edit instructions
Edit the files using SEARCH/REPLACE blocks. Example:
<<<<<<< SEARCH
old code here
=======
new code here
>>>>>>> REPLACE

Make minimal, targeted changes. Do not rewrite entire files.
