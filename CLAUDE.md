# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Проект

ForgeRace — мультиагентный оркестратор разработки. Запускает несколько AI-агентов (Claude, Gemini и др.) параллельно на задачах кодирования, с крест-на-крест ревью, авто-декомпозицией и race-to-merge.

## Команды

```bash
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

Тестов пока нет. При добавлении — pytest.

## Архитектура

Рефакторинг из монолита завершён. Модули в `forgerace/`:

- **config.py** — `Config` (dataclass) + загрузка из `forgerace.toml`. Глобальный `cfg` обновляется in-place через `init_config()`, чтобы все модули видели изменения через `from .config import cfg`.
- **tasks.py** — парсер `TASKS.md`, модель `Task`, `update_task_status`, `find_ready_tasks`.
- **agents.py** — запуск agent-процессов (`run_agent_process`), стриминг событий (`_log_claude_event`, `_log_gemini_event`), `build_prompt`. Агенты — внешние CLI (`claude`, `gemini`), общение через stdin/stream-json.
- **review.py** — кросс-ревью: `code_review` запускает агентов-ревьюеров друг на друга, парсит `VERDICT:`/`COMMENTS:`. `send_to_rework` возвращает агенту замечания.
- **pipeline.py** — главный цикл: `run_pipeline` → `execute_task_competitive` (все агенты на одну задачу, race-to-merge). Heartbeat-поток мониторит прогресс. `_pre_check` валидирует задачу перед запуском.
- **decompose.py** — LLM оценивает сложность задачи, при необходимости разбивает на подзадачи и вставляет в TASKS.md.
- **discuss.py** — система архитектурных дискуссий: агенты обсуждают подход до реализации, auto-resolve при достижении консенсуса (CONFIDENCE ≥ 80%).
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

## Конфигурация

Всё настраивается через `forgerace.toml`. См. `examples/ethos.toml` для полного примера. Секции: `[project]`, `[agents.*]`, `[build]`, `[limits]`, `[rules]`, `[metrics]`. Без TOML-файла работают дефолты из `Config` dataclass.

Python 3.10+. Зависимость `tomli` нужна только для Python < 3.11 (в 3.11+ есть `tomllib`).

## Оставшиеся задачи рефакторинга

- **Фаза 3**: убрать специфику Rust/cargo и EthOS — сделать build/check команды полностью конфигурируемыми, поддержка произвольных моделей
- **Фаза 4**: `python -m forgerace`, примеры

## Соглашения

- Python 3.10+
- Комментарии: на русском или английском
- Каждый модуль должен быть импортируемым отдельно

## Известные баги/TODO

1. Gemini как ревьюер ненадёжен — ложные APPROVED. Решается через промпт + валидацию COMMENTS > 20 символов.
2. Декомпозиция должна обновлять зависимости в других задачах (частично реализовано).
3. `git checkout merge_sha -- .` может затереть файлы — используем поимённый checkout изменённых файлов.
4. `stty sane` после завершения — агенты ломают терминал.
5. Ревью прогона (review_run_log) — полезно но медленно. Можно сделать опциональным.
