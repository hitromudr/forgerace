# CLAUDE.md — ForgeRace

## Проект

ForgeRace — мультиагентный оркестратор разработки. Запускает несколько AI-агентов (Claude, Gemini и др.) параллельно на задачах кодирования, с крест-на-крест ревью, авто-декомпозицией и race-to-merge.

## Текущее состояние

Монолит `forgerace/orchestrator_monolith.py` (2770 строк) — рабочий код из проекта EthOS. Нужно рефакторить в модули.

## Задачи рефакторинга

### Фаза 1: Выделение модулей
Разбить `orchestrator_monolith.py` на модули по архитектуре из README.md:

1. `config.py` — Конфигурация из TOML (вместо хардкода). Всё что сейчас в строках 35-80 монолита.
2. `tasks.py` — Парсер TASKS.md, модель Task, update_task_status, link_task_discussion. Строки 145-320.
3. `agents.py` — _run_claude, _run_gemini, _log_claude_event, _log_gemini_event, стриминг. Строки 82-320.
4. `review.py` — _single_review, code_review (крест-на-крест), _run_reviewer, парсинг VERDICT/COMMENTS. Строки 940-1080.
5. `pipeline.py` — execute_task_competitive, execute_task_single, run_pipeline, race-модель. Строки 1170-1960.
6. `decompose.py` — _assess_and_maybe_decompose, _insert_tasks_into_tasksmd. Строки 1440-1750.
7. `discuss.py` — discuss_create/reply/list/show/chat, _post_resolve, _chat_auto_resolve. Строки 1260-1700.
8. `worktree.py` — create_worktree, remove_worktree, _cleanup_worktrees. Строки 495-525.
9. `merge.py` — merge_to_develop (detached worktree + update-ref). Строки 550-700.
10. `utils.py` — run_cmd, _is_valid_path, _task_paths, heartbeat, slugify. Строки разные.
11. `cli.py` — main(), argparse. Строки 2650-2770.

### Фаза 2: Конфигурация
Заменить хардкод на `forgerace.toml`:
- PROJECT_CONTEXT, DISCUSS_CONTEXT → config
- BUILD_COMMANDS, BINARY_GLOBS → config
- AGENT_TIMEOUT, MAX_PARALLEL_TASKS, MAX_REVIEW_ROUNDS → config
- AGENT_RULES → config
- Список агентов (claude, gemini) → config

### Фаза 3: Обобщение
- Убрать специфику Rust/cargo — сделать BUILD_COMMANDS и CHECK_COMMANDS конфигурируемыми
- Убрать специфику EthOS — сделать TASKS.md формат документированным протоколом
- Поддержка произвольных моделей через конфиг (не хардкод claude/gemini)

### Фаза 4: Точка входа
- `forgerace.py` (или `python -m forgerace`) как CLI
- `examples/ethos.toml` — пример конфига для EthOS
- `examples/TASKS.md` — пример файла задач

## Соглашения

- Язык кода: Python 3.10+
- Комментарии: на русском (как в оригинале) или на английском — на твой выбор
- Тесты: pytest (если добавляешь)
- Не удалять монолит пока модули не заработают
- Каждый модуль должен быть импортируемым отдельно

## Известные баги/TODO из оркестратора

1. Gemini как ревьюер ненадёжен — ложные APPROVED. Решается через промпт + валидацию COMMENTS > 20 символов.
2. Декомпозиция должна обновлять зависимости в других задачах (частично реализовано).
3. `git checkout merge_sha -- .` может затереть файлы — используем поимённый checkout изменённых файлов.
4. stty sane после завершения — агенты ломают терминал.
5. Ревью прогона (review_run_log) — полезно но медленно. Можно сделать опциональным.
