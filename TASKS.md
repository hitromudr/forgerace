
 TASKS — forgerace

### TASK-026: Реализация бизнес-валидации ревью
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: —
- **Файлы (новые)**: tests/test_review.py
- **Файлы (modify)**: forgerace/review.py, forgerace/pipeline.py
- **Интеграция**: Интеграция вызова `validate_review` в `single_review` (вместо старой проверки `COMMENTS > 20`), обновление оркестратора для чтения `is_terminal` из схемы
- **Описание**: Реализовать `validate_review(data: dict) -> tuple[bool, str]`. Обновить `REVIEW_SCHEMA` (добавить `is_terminal`, `confidence_range`). Принимать `confidence` как `int` и `float` (приводить к `int`). Обновить парсинг `issues` из промпта (в формате `[severity] текст`) и нормализовать их в `list[dict]`. Переименовать `NEEDS_WORK` в `NEEDS_REWORK` (добавив алиас для обратной совместимости). Реализовать завершение ветки оркестратором при статусе `REJECTED` на основе флага `is_terminal`. Удалить проверку длины комментариев > 20 символов. Написать 8-10 unit-тестов.
- **Запрещено**: не хардкодить логику обработки REJECTED в оркестраторе (использовать `is_terminal` из схемы); не использовать строгую проверку `isinstance(confidence, int)` (обязательно поддерживать float); не использовать хардкод dict-схемы без возможности расширения.
- **Проверка**: pytest tests/test_review.py -v
- **Критерий готовности**: Функция валидации корректно отклоняет ревью, нарушающие бизнес-правила (APPROVED с critical issues, REJECTED/NEEDS_REWORK без issues, неверный confidence). Костыль проверки длины комментариев удален. При статусе REJECTED оркестратор немедленно завершает ветку агента без retry.
- **Дискуссия**: 2-validatsiya-revyu-s-biznes-pravilami
- **Агент**: —
- **Ветка**: —

### TASK-003: Rework counter и TaskState
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: TASK-002
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/tasks.py
- **Интеграция**: —
- **Описание**: Добавить dataclass `TaskState` с полями `rework_count: int`, `last_attempts: list[str]` (последние 3 diff summary). Реализовать `build_rework_prompt(task, state) -> str` — формирует блок промпта с историей предыдущих неудачных попыток. Добавить параметр `max_reworks` в `[limits]` (дефолт 3), с возможностью переопределения через `<!-- config: max_reworks=N -->` в теле задачи TASKS.md.
- **Критерий готовности**: `TaskState` хранит историю попыток, `build_rework_prompt` генерирует summary, `max_reworks` читается из конфига и задачи
- **Дискуссия**: future
- **Агент**: —
- **Ветка**: —

### TASK-004: Интеграция rework counter в pipeline
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: TASK-003
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: —
- **Описание**: При возврате на доработку (NEEDS_REWORK) инкрементировать `rework_count`, сохранять summary diff + ошибки в `last_attempts`, инжектить `build_rework_prompt` в промпт агента. При `rework_count >= max_reworks` — убивать агента и помечать задачу статусом STUCK.
- **Критерий готовности**: Агент получает историю ошибок при повторных попытках, задача помечается STUCK при превышении лимита
- **Дискуссия**: future
- **Агент**: —
- **Ветка**: —

### TASK-005: Граф зависимостей задач
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 3
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/tasks.py
- **Интеграция**: —
- **Описание**: Реализовать класс `DependencyGraph` с методами: построение графа из `depends:` полей TASKS.md, `get_transitive_dependents(task_id) -> set[str]`, проверка циклов (topological sort). Функция приоритета: `compute_priority(task_id, graph) -> int` — чем больше транзитивных зависимых, тем выше приоритет.
- **Критерий готовности**: Граф строится из TASKS.md, циклы детектируются, `compute_priority` возвращает корректные значения
- **Дискуссия**: future
- **Агент**: claude
- **Ветка**: task/task-005-graf-zavisimostej-zadach-claude

### TASK-012: TaskQueue с приоритетами на heapq
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 3
- **Зависимости**: —
- **Файлы (новые)**: forgerace/task_queue.py
- **Файлы (modify)**: —
- **Интеграция**: импортировать в pipeline.py
- **Описание**: Реализовать класс `TaskQueue` с использованием `heapq`. Методы: `push(task_id, priority)`, `pop()`, `empty()`. Кортеж в очереди: `(-priority, task_id)` для max-heap поведения.
- **Критерий готовности**: очередь корректно возвращает задачи по приоритету, тесты проходят
- **Дискуссия**: future
- **Агент**: qwen
- **Ветка**: task/task-012-taskqueue-s-prioritetami-na-heapq-qwen

### TASK-013: ConcurrencyLimiter на ThreadPoolExecutor
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 3
- **Зависимости**: TASK-012
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/task_queue.py
- **Интеграция**: добавить в TaskQueue
- **Описание**: Добавить в `TaskQueue` интеграцию с `ThreadPoolExecutor(max_workers=N)`. Параметр `max_concurrent` читается из `[limits]` (дефолт 3). Метод `submit(fn, *args)` ставит задачу в очередь, запуск — по мере освобождения слотов.
- **Критерий готовности**: не более `max_concurrent` задач выполняются одновременно
- **Дискуссия**: future
- **Агент**: claude
- **Ветка**: task/task-013-concurrencylimiter-na-threadpoolexecutor-claude

### TASK-014A: Добавить поле `depends` в класс `Task`
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 3
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: `forgerace/tasks.py`
- **Интеграция**: поле `depends: list[str]` в класс `Task`, парсинг из TOML
- **Описание**: Добавить атрибут `depends` (список task_id) в класс `Task`. Обновить парсинг задач из TOML-файлов для извлечения поля `depends`. Добавить валидацию (опционально).
- **Критерий готовности**: `Task.depends` доступно, зависимости парсятся из `forgerace.toml`
- **Дискуссия**: future
- **Агент**: —
- **Ветка**: —

### TASK-014B: Метод `mark_complete` и хранение завершённых задач в `TaskQueue`
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 3
- **Зависимости**: TASK-014A
- **Файлы (новые)**: —
- **Файлы (modify)**: `forgerace/task_queue.py`
- **Интеграция**: метод `mark_complete(task_id)`, множество завершённых задач
- **Описание**: Добавить `_completed: set[str]` в `TaskQueue`. Реализовать `mark_complete(task_id)` для отметки завершённых задач. Добавить метод `is_completed(task_id)` для проверки.
- **Критерий готовности**: `mark_complete()` и `is_completed()` работают, состояние сохраняется
- **Дискуссия**: future
- **Агент**: —
- **Ветка**: —

### TASK-014C: Проверка готовности задачи перед запуском
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 3
- **Зависимости**: TASK-014A, TASK-014B
- **Файлы (новые)**: —
- **Файлы (modify)**: `forgerace/task_queue.py`
- **Интеграция**: метод `is_ready(task_id, depends)` для проверки зависимостей
- **Описание**: Добавить метод `is_ready(task_id: str, depends: list[str]) -> bool`, который проверяет, все ли задачи из `depends` завершены (через `is_completed`). Обновить `pop()` или добавить отдельный метод для извлечения только готовых задач.
- **Критерий готовности**: задача с зависимостями не извлекается, пока не завершены все зависимости
- **Дискуссия**: future
- **Агент**: —
- **Ветка**: —

### TASK-014D: Интеграция в `pipeline.py` — запуск через очередь с зависимостями
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 3
- **Зависимости**: TASK-014A, TASK-014B, TASK-014C
- **Файлы (новые)**: —
- **Файлы (modify)**: `forgerace/pipeline.py`
- **Интеграция**: замена текущего запуска на очередь с проверкой зависимостей, вызов `mark_complete` после завершения агента
- **Описание**: Обновить запуск задач в `pipeline.py`: использовать `TaskQueue.is_ready()` перед запуском агента. После успешного завершения задачи вызывать `task_queue.mark_complete(task_id)`. Обработать цикл: проверка готовых → запуск → ожидание → повторная проверка.
- **Критерий готовности**: задачи запускаются только после завершения зависимых, `mark_complete` вызывается после завершения агента
- **Дискуссия**: future
- **Агент**: —
- **Ветка**: —

### TASK-028: Механизм блокировки записи в TASKS.md
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: добавить `tasks_md_lock = threading.Lock()` в модуль pipeline.py; обернуть все функции записи в TASKS.md в `with tasks_md_lock:`
- **Описание**: Реализовать потокобезопасную запись в TASKS.md через threading.Lock. Найти все места записи в TASKS.md внутри pipeline.py (обновление статусов задач) и обернуть их в lock. Это предотвратит гонку при одновременном обновлении файла из нескольких future.
- **Запрещено**: использовать file-level lock (только threading.Lock); блокировать чтение TASKS.md (только запись); создавать новые глобальные состояния вне lock
- **Проверка**: ruff check forgerace/pipeline.py && python -c "from forgerace.pipeline import tasks_md_lock; print('OK')"
- **Критерий готовности**: все записи в TASKS.md внутри pipeline.py защищены threading.Lock, одновременные обновления не приводят к повреждению файла
- **Дискуссия**: 15-integratsiya-taskqueue-v-pipelinepy
- **Агент**: —
- **Ветка**: —

### TASK-029: Основной цикл оркестрации на TaskQueue
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: TASK-027
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: заменить блок ThreadPoolExecutor + as_completed (строки ~1142-1154) на цикл с TaskQueue; добавить импорт TaskQueue из forgerace.task_queue
- **Описание**: Заменить существующий код запуска батча задач на TaskQueue с динамическим пополнением. Инициализировать TaskQueue(max_concurrent=cfg.limits_max_concurrent). Заполнить очередь начальными готовыми задачами с приоритетами. В цикле: pop задачи → submit через execute_task → при освобождении слота подхватить новую готовую задачу. Использовать `max_concurrent` из секции [limits] конфига (дефолт 3).
- **Запрещено**: использовать time.sleep(1) для ожидания; удалять уровни 2-4 (race, review, rework); хардкодить max_concurrent; использовать active polling вместо callback
- **Проверка**: ruff check forgerace/pipeline.py && pytest tests/test_pipeline_orchestration.py -v
- **Критерий готовности**: задачи запускаются через TaskQueue с приоритетами, при завершении одной задачи новая готовая задача подхватывается автоматически без ожидания следующего батча
- **Дискуссия**: 15-integratsiya-taskqueue-v-pipelinepy
- **Агент**: —
- **Ветка**: —

### TASK-030: Callback завершения задачи с обновлением статусов
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 3
- **Зависимости**: TASK-029
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: добавить функцию `_on_task_complete(future, task, task_idx)` и зарегистрировать через `future.add_done_callback()` в основном цикле
- **Описание**: Реализовать callback, который вызывается при завершении каждого future. Callback: логирует результат (успех/ошибка), обновляет статус задачи в TASKS.md (через tasks_md_lock), вызывает `find_ready_tasks()` для поиска новых разблокированных задач и добавляет их в очередь с приоритетами. Использовать `add_done_callback` вместо polling — это исключит холостой поллинг и позволит запускать разблокированные подзадачи моментально.
- **Запрещено**: использовать polling/time.sleep для проверки завершения; обновлять TASKS.md без lock; блокировать очередь на время чтения TASKS.md; падать при ошибке в callback
- **Проверка**: ruff check forgerace/pipeline.py && pytest tests/test_pipeline_callback.py -v
- **Критерий готовности**: при завершении задачи статус обновляется мгновенно, зависимые задачи автоматически добавляются в очередь без задержки
- **Дискуссия**: 15-integratsiya-taskqueue-v-pipelinepy
- **Агент**: —
- **Ветка**: —

### TASK-031: Graceful shutdown очереди
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 3
- **Зависимости**: TASK-029
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: добавить вызов `queue.shutdown(wait=True)` после завершения основного цикла; обработать KeyboardInterrupt и SIGTERM
- **Описание**: Реализовать корректное завершение работы TaskQueue. После завершения основного цикла (очередь пуста + нет pending future) вызвать `queue.shutdown(wait=True)` для ожидания всех активных задач. Обработать сигналы прерывания (KeyboardInterrupt, SIGTERM) — остановить приём новых задач, дождаться завершения активных, обновить статусы незавершённых задач в TASKS.md.
- **Запрещено**: использовать queue.shutdown(wait=False) — это потеряет активные задачи; игнорировать SIGTERM; оставлять задачи в подвешенном состоянии без обновления статуса
- **Проверка**: ruff check forgerace/pipeline.py && pytest tests/test_pipeline_shutdown.py -v
- **Критерий готовности**: при Ctrl+C или завершении цикла все активные задачи корректно завершаются, статусы обновляются в TASKS.md, процесс выходит с кодом 0
- **Дискуссия**: 15-integratsiya-taskqueue-v-pipelinepy
- **Агент**: —
- **Ветка**: —

### TASK-007: Модуль cost tracking
- **Статус**: done
- **Приоритет**: P2
- **Этап**: 4
- **Зависимости**: —
- **Файлы (новые)**: forgerace/cost.py
- **Файлы (modify)**: —
- **Интеграция**: добавить `from . import cost` в `forgerace/__init__.py`
- **Описание**: Создать модуль `cost.py` с dataclass `TokenUsage` (input_tokens, output_tokens, estimated_usd). Реализовать парсинг usage из стрима: для Claude — поле `usage` в событиях, для Gemini — `usageMetadata`. Метод `estimated_usd` считает стоимость по ценам из конфига.
- **Критерий готовности**: `TokenUsage` аккумулирует токены из стрима обоих провайдеров, `estimated_usd` корректно считает стоимость
- **Дискуссия**: future
- **Агент**: qwen
- **Ветка**: task/task-007-modul-cost-tracking-qwen

### TASK-008: Секция pricing в конфиге
- **Статус**: done
- **Приоритет**: P2
- **Этап**: 4
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/config.py
- **Интеграция**: —
- **Описание**: Добавить секцию `[pricing]` в `Config` с дефолтными ценами за токен для каждого провайдера (claude-input, claude-output, gemini-input, gemini-output). Добавить параметр `budget_per_task_usd` в `[limits]` (дефолт None — без лимита). Парсинг из TOML.
- **Критерий готовности**: Цены читаются из `[pricing]`, `budget_per_task_usd` доступен в конфиге
- **Дискуссия**: future
- **Агент**: claude
- **Ветка**: task/task-008-sektsiya-pricing-v-konfige-claude

### TASK-016: Добавить поле estimated_usd в TokenUsage
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 4
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/agents.py
- **Интеграция**: —
- **Описание**: Изучить класс TokenUsage (или dataclass), добавить поле `estimated_usd` для аккумулирования стоимости. Обновить `_log_claude_event` и `_log_gemini_event` для извлечения `total_cost_usd` из событий и добавления в TokenUsage.
- **Критерий готовности**: TokenUsage содержит accumulating стоимость, оба логгера обновляют поле
- **Дискуссия**: future
- **Агент**: gemini
- **Ветка**: task/task-016-dobavit-pole-estimated-usd-v-tokenusage-gemini

### TASK-017: Проверка бюджета в pipeline
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 4
- **Зависимости**: TASK-016
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: —
- **Описание**: В pipeline (где запускается агент) добавить проверку `estimated_usd` против `budget_per_task_usd` из конфига. При превышении — убивать процесс агента (subprocess.terminate/kill), помечать задачу статусом BUDGET_EXCEEDED через `update_task_status`.
- **Критерий готовности**: Агент останавливается при превышении бюджета, задача помечается BUDGET_EXCEEDED
- **Дискуссия**: 17-proverka-byudzheta-v-pipeline
- **Агент**: —
- **Ветка**: —

### TASK-018: Вывод итоговой стоимости в лог
- **Статус**: done
- **Приоритет**: P2
- **Этап**: 4
- **Зависимости**: TASK-016
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: —
- **Описание**: После завершения агента (успех/провал/бюджет) выводить итоговую стоимость задачи в лог через `log.info` с форматом `$X.XX`.
- **Критерий готовности**: В логе отображается финальная стоимость каждой задачи
- **Дискуссия**: future
- **Агент**: gemini
- **Ветка**: task/task-018-vyvod-itogovoj-stoimosti-v-log-gemini

### TASK-010: Хуки on_complete в конфиге
- **Статус**: done
- **Приоритет**: P2
- **Этап**: 5
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/config.py, forgerace/pipeline.py
- **Интеграция**: —
- **Описание**: Добавить секцию `[hooks]` в конфиг с параметром `on_complete` (строка shell-команды). При завершении задачи (любой статус) выполнять команду через `subprocess.run`, передавая переменные окружения: `FORGERACE_TASK_ID`, `FORGERACE_STATUS`, `FORGERACE_AGENT`. Пример: `on_complete = "curl -X POST https://hooks.slack.com/... -d '{\"text\": \"$FORGERACE_TASK_ID: $FORGERACE_STATUS\"}'"`
- **Критерий готовности**: Хук вызывается при завершении задачи с корректными переменными окружения
- **Дискуссия**: future
- **Агент**: qwen
- **Ветка**: task/task-010-huki-on-complete-v-konfige-qwen

### TASK-011: Поле protocol в AgentConfig
- **Статус**: done
- **Приоритет**: P3
- **Этап**: 5
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/config.py
- **Интеграция**: —
- **Описание**: Добавить поле `protocol: str = "cli"` в `AgentConfig` (или аналогичную структуру конфига агента). Допустимые значения: `"cli"`, `"mcp"`. Дефолт `"cli"`. На данном этапе поле только читается и сохраняется — логика для `"mcp"` будет реализована позже.
- **Критерий готовности**: Поле `protocol` парсится из `[agents.*]` секции TOML, дефолт `"cli"` работает без изменений поведения
- **Дискуссия**: future
- **Агент**: qwen
- **Ветка**: task/task-011-pole-protocol-v-agentconfig-qwen

### TASK-025: Signal handling — setpgrp до handlers + getpgrp
- **Статус**: open
- **Приоритет**: P0
- **Этап**: 1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: `forgerace/cli.py`
- **Интеграция**: —
- **Описание**: В `main_with_signal_handling` (строки 813–829) устранить race condition: 1) перенести `os.setpgrp()` ДО вызовов `signal.signal()`, 2) в `_force_exit` заменить `os.getpgid(os.getpid())` на `os.getpgrp()`, 3) удалить/обновить комментарий `# после handler — чтобы SIGINT между ними не потерялся`. Итоговый порядок: `os.setpgrp()` → `signal.signal(SIGINT, _force_exit)` → `signal.signal(SIGTERM, _force_exit)`.
- **Запрещено**: устанавливать signal handlers до `os.setpgrp()`, использовать `os.getpgid(os.getpid())` вместо `os.getpgrp()`, оставлять старый комментарий без обновления
- **Проверка**: `ruff check forgerace/cli.py && python -c "from forgerace.cli import main_with_signal_handling; print('import OK')"`
- **Критерий готовности**: `os.setpgrp()` вызывается до `signal.signal()`, `_force_exit` использует `os.getpgrp()`, Ctrl+C из скрипта/Makefile не убивает родительскую группу процессов
- **Дискуссия**: 20-signal-handling-setpgrp-do-handlers
- **Агент**: —
- **Ветка**: —

### TASK-021: Config validation — типы, диапазоны, PATH check
- **Статус**: open
- **Приоритет**: P1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/config.py
- **Описание**: После загрузки конфига добавить валидацию: 1) числовые поля (agent_timeout, max_parallel_tasks, max_retries, progress_timeout) — isinstance(int) и > 0, 2) команды агентов — shutil.which(command) при загрузке, warning если не найден, 3) пути (root_dir) — существование директории. При ошибках — log.error с конкретным сообщением и sys.exit(1). Обернуть tomllib.load в try/except TOMLDecodeError — вывести human-readable сообщение с указанием файла.
- **Критерий готовности**: кривой TOML даёт понятное сообщение, agent_timeout="five" ловится при загрузке, несуществующая команда агента — warning
- **Дискуссия**: code-audit
- **Агент**: —
- **Ветка**: —

### TASK-022: merge.py — убрать checkout в основной repo
- **Статус**: open
- **Приоритет**: P1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/merge.py
- **Описание**: В merge_to_develop после update-ref выполняется `git checkout merge_sha -- fname` в cfg.root_dir. Это: 1) модифицирует index и рабочее дерево пользователя без ведома, 2) перезатирает незакоммиченные изменения, 3) staged changes которые пользователь не делал. Fix: убрать блок синхронизации файлов (строки с checkout). Вместо этого — после update-ref вызвать `git read-tree` или оставить только update-ref. Пользователь сам синхронизирует рабочее дерево через `git checkout -- .` если нужно.
- **Критерий готовности**: merge_to_develop не модифицирует рабочее дерево cfg.root_dir, только обновляет ref
- **Дискуссия**: code-audit
- **Агент**: —
- **Ветка**: —

### TASK-023: Явная ошибка при пустом списке агентов/задач
- **Статус**: open
- **Приоритет**: P2
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py, forgerace/cli.py
- **Описание**: Сейчас при отсутствии агентов или задач pipeline молча завершается пустым прогоном. Fix: 1) в начале run_pipeline проверить cfg.agent_names — если пуст, log.error("Нет активных агентов. Включите хотя бы одного в forgerace.toml") и return, 2) FileNotFoundError на TASKS.md — перехватить в cli.py, вывести "TASKS.md не найден. Запустите forgerace init", 3) TOMLDecodeError — перехватить, вывести файл и ошибку.
- **Критерий готовности**: пустой agents → понятное сообщение, нет TASKS.md → подсказка про init, кривой TOML → файл + ошибка
- **Дискуссия**: code-audit
- **Агент**: —
- **Ветка**: —

### TASK-024: verify_build — фиксировать base SHA до начала задачи
- **Статус**: open
- **Приоритет**: P1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Описание**: verify_build делает `git diff --stat cfg.dev_branch` в worktree. Но merge_to_develop двигает указатель dev_branch (через update-ref). После мержа одной задачи у всех остальных агентов diff base сдвигается → has_changes=True даже если агент ничего не написал. Fix: при создании worktree сохранить base_sha (SHA коммита от которого создана ветка), и в verify_build использовать base_sha вместо cfg.dev_branch.
- **Критерий готовности**: verify_build использует фиксированный base SHA, агент-пустышка не проходит валидацию после мержа другой задачи
- **Дискуссия**: code-audit
- **Агент**: —
- **Ветка**: —
