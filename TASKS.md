
 TASKS — forgerace

### TASK-042: Реализация функции валидации и обновление схемы ревью
- **Статус**: in_progress:both
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/review.py
- **Интеграция**: —
- **Описание**: Обновить `REVIEW_SCHEMA` (добавить `is_terminal`, `confidence_range`). Реализовать функцию `validate_review(data: dict) -> tuple[bool, str]`. Функция должна приводить `confidence` к `int` (принимая `float` или `int`), переименовать `NEEDS_WORK` в `NEEDS_REWORK` (с сохранением алиаса `NEEDS_WORK` для обратной совместимости). Добавить парсинг списка `issues` из строк формата `[severity] текст` в нормализованный `list[dict]`.
- **Критерий готовности**: Функция `validate_review` написана, корректно обрабатывает входные данные, отклоняет неверный `confidence`, несовместимые статусы (APPROVED с критическими ошибками, REJECTED без ошибок). Схема `REVIEW_SCHEMA` обновлена.
- **Дискуссия**: 2-validatsiya-revyu-s-biznes-pravilami
- **Агент**: —
- **Ветка**: —

### TASK-043: Покрытие логики ревью unit-тестами
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: TASK-042
- **Файлы (новые)**: tests/test_review.py
- **Файлы (modify)**: —
- **Интеграция**: —
- **Описание**: Написать 8-10 unit-тестов для проверки функции `validate_review` и логики парсинга `issues`. Тесты должны покрывать успешные сценарии (APPROVED без issues, NEEDS_REWORK с issues) и провальные (выход за пределы confidence, APPROVED с critical issues, REJECTED без issues).
- **Критерий готовности**: Написаны и успешно проходят 8-10 тестов, проверяющих все бизнес-правила валидации ревью.
- **Дискуссия**: 2-validatsiya-revyu-s-biznes-pravilami
- **Агент**: —
- **Ветка**: —

### TASK-044: Интеграция валидации в процесс ревью и оркестратор
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: TASK-042
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/review.py, forgerace/pipeline.py
- **Интеграция**: Использование `validate_review` в процессе выполнения задач
- **Описание**: Интегрировать вызов `validate_review` в функцию `single_review` (в `forgerace/review.py`), полностью заменив старую проверку длины комментариев (`COMMENTS > 20`). В `forgerace/pipeline.py` обновить логику оркестратора: если от ревьюера получен статус `REJECTED` с установленным флагом `is_terminal`, оркестратор должен немедленно завершать ветку агента без попыток retry.
- **Критерий готовности**: Костыль проверки длины комментариев удален. При получении терминального REJECTED оркестратор корректно обрывает исполнение агента для данной ветки без повторных запусков.
- **Дискуссия**: 2-validatsiya-revyu-s-biznes-pravilami
- **Агент**: —
- **Ветка**: —

### TASK-003: Rework counter и TaskState
- **Статус**: blocked
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

### TASK-045: Создание блокировки и безопасной обертки в pipeline.py
- **Статус**: in_progress:both
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: добавить `tasks_md_lock = threading.Lock()` и `safe_update_task_status`
- **Описание**: Создать `tasks_md_lock = threading.Lock()` на уровне модуля `pipeline.py`. Реализовать функцию `safe_update_task_status(*args, **kwargs)`, которая вызывает оригинальный `update_task_status` строго внутри блока `with tasks_md_lock:`. Использование функции-обертки позволит избежать массового изменения отступов в 30+ местах (что было причиной предыдущих провалов).
- **Критерий готовности**: В файле объявлен лок и присутствует функция `safe_update_task_status`, синтаксис файла корректен.
- **Дискуссия**: 15-integratsiya-taskqueue-v-pipelinepy
- **Агент**: —
- **Ветка**: —

### TASK-046: Перевод обновлений TASKS.md на потокобезопасную функцию
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: TASK-045
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: заменить вызовы `update_task_status` на `safe_update_task_status`
- **Описание**: Найти все места записи статусов задач в `pipeline.py` (более 30 вызовов `update_task_status`) и переименовать вызовы на `safe_update_task_status`. 
- **Критерий готовности**: Внутри `pipeline.py` не осталось незащищенных вызовов обновления `TASKS.md`, одновременные апдейты из нескольких потоков не приводят к повреждению файла.
- **Дискуссия**: 15-integratsiya-taskqueue-v-pipelinepy
- **Агент**: —
- **Ветка**: —

### TASK-029: Основной цикл оркестрации на TaskQueue
- **Статус**: blocked
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

### TASK-033: Graceful shutdown для прерывания subprocess
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/agents.py
- **Интеграция**: —
- **Описание**: Реализовать функцию `_terminate_agent_process(proc)`, которая выполняет graceful shutdown: сначала `proc.terminate()`, затем `proc.wait(timeout=2)`, и только при зависании — `proc.kill()`. Функция должна логировать каждый этап и обрабатывать `subprocess.TimeoutExpired`.
- **Запрещено**: Использовать `proc.kill()` без предварительного `proc.terminate()`; игнорировать `TimeoutExpired`; не логировать причину прерывания
- **Проверка**: ruff check forgerace/agents.py && pytest tests/test_agents.py -v -k graceful_shutdown
- **Критерий готовности**: Процесс агента корректно завершается с сохранением логов и состояния, fallback на kill только при зависании
- **Дискуссия**: 17-proverka-byudzheta-v-pipeline
- **Агент**: qwen
- **Ветка**: task/task-033-graceful-shutdown-dlya-preryvaniya-subpr-qwen

### TASK-034: Возврат AgentProcessResult со статусом BUDGET_EXCEEDED
- **Статус**: blocked
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: TASK-032, TASK-033
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/agents.py
- **Интеграция**: `_run_agent_streaming` возвращает `AgentProcessResult(stderr="BUDGET_EXCEEDED")` при превышении бюджета
- **Описание**: После выхода из цикла по причине превышения бюджета вызвать `_terminate_agent_process(proc)` и вернуть `AgentProcessResult` с `stderr="BUDGET_EXCEEDED"`. Это должен быть терминальный результат без retry-логики.
- **Запрещено**: Возвращать стандартный error-код вместо "BUDGET_EXCEEDED"; добавлять retry-логику для этого статуса; смешивать с обработкой таймаутов
- **Проверка**: ruff check forgerace/agents.py && pytest tests/test_agents.py -v -k budget_exceeded
- **Критерий готовности**: `_run_agent_streaming` возвращает различимый результат BUDGET_EXCEEDED, который можно обработать на уровне pipeline
- **Дискуссия**: 17-proverka-byudzheta-v-pipeline
- **Агент**: —
- **Ветка**: —

### TASK-035: Обработка BUDGET_EXCEEDED в pipeline.py
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 3
- **Зависимости**: TASK-034
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: В `run_single_agent` добавить ветку обработки `AgentProcessResult.stderr == "BUDGET_EXCEEDED"` с вызовом `update_task_status(task.id, "BUDGET_EXCEEDED")` и прерыванием выполнения задачи
- **Описание**: В функции `run_single_agent` (`pipeline.py`) после получения результата от `_run_agent_streaming` проверить `stderr == "BUDGET_EXCEEDED"`. При совпадении — вызвать `update_task_status(task.id, "BUDGET_EXCEEDED")`, залогировать предупреждение и прервать выполнение (без retry). Статус должен быть терминальным.
- **Запрещено**: Автоматический retry для статуса BUDGET_EXCEEDED; молчаливое игнорирование (обязателен log.warning); изменение статуса на другой (FAILED, ERROR и т.д.)
- **Проверка**: ruff check forgerace/pipeline.py && pytest tests/test_pipeline.py -v -k budget_exceeded
- **Критерий готовности**: Задача с превышенным бюджетом получает статус BUDGET_EXCEEDED в системе, выполнение прерывается без повторных попыток
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
- **Статус**: done
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
- **Агент**: qwen
- **Ветка**: task/task-025-signal-handling-setpgrp-do-handlers-getp-qwen

### TASK-036: ConfigValidationError — кастомное исключение
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: —
- **Файлы (новые)**: forgerace/config_errors.py
- **Файлы (modify)**: —
- **Интеграция**: добавить `from .config_errors import ConfigValidationError` в forgerace/__init__.py
- **Описание**: Создать кастомное исключение `ConfigValidationError(Exception)` с полями `message: str` и `source: str` (файл конфига или "validation"). Добавить фабричную функцию `raise_config_error(message, source="")`, которая бросает `ConfigValidationError`. Исключение должно быть dataclass-совместимым для удобного тестирования.
- **Запрещено**: использовать `sys.exit()` внутри модуля исключений; наследовать от `SystemExit` или `BaseException`; хардкодить сообщения об ошибках
- **Проверка**: ruff check forgerace/config_errors.py && python -c "from forgerace.config_errors import ConfigValidationError; raise ConfigValidationError('test')"
- **Критерий готовности**: `ConfigValidationError` импортируется, бросается и перехватывается как обычное исключение, содержит читаемое сообщение
- **Дискуссия**: 21-config-validation-tipy-diapazony-path-ch
- **Агент**: qwen
- **Ветка**: task/task-036-configvalidationerror-kastomnoe-isklyuch-qwen

### TASK-037: Валидация числовых полей конфига
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: TASK-036
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/config.py
- **Интеграция**: добавить функцию `validate_numeric_fields(cfg: Config) -> None` в config.py; вызвать её в конце `load_config()` перед `return cfg`
- **Описание**: Реализовать `validate_numeric_fields(cfg: Config)`, которая проверяет: 1) `agent_timeout` — `isinstance(val, (int, float))` и `> 0`, привести к `float`; 2) `progress_timeout` — `isinstance(val, (int, float))` и `> 0`, привести к `float`; 3) `max_parallel_tasks` — строго `isinstance(val, int)` и `> 0`; 4) `max_retries` — `isinstance(val, int)` и `>= 0` (ноль допустим); 5) `max_concurrent` — `isinstance(val, int)` и `> 0`; 6) соотношение таймаутов: если `progress_timeout >= agent_timeout` — `log.warning` (не error). При нарушении — бросать `ConfigValidationError` с конкретным сообщением (имя поля, ожидаемый/фактический тип, значение).
- **Запрещено**: использовать `sys.exit()` внутри валидации; проверять `max_retries > 0` (ноль — валидное значение); принимать float для `max_parallel_tasks` и `max_concurrent`; использовать `isinstance(val, int)` без проверки `bool` (bool — подкласс int в Python)
- **Проверка**: ruff check forgerace/config.py && python -c "from forgerace.config import Config, validate_numeric_fields; from forgerace.config_errors import ConfigValidationError; c = Config(); c.agent_timeout = -1; validate_numeric_fields(c)" 2>&1 | grep -q ConfigValidationError && echo "PASS"
- **Критерий готовности**: некорректные числовые значения (отрицательные таймауты, float для max_parallel_tasks, progress_timeout >= agent_timeout) детектируются и бросают `ConfigValidationError` с понятным сообщением
- **Дискуссия**: 21-config-validation-tipy-diapazony-path-ch
- **Агент**: gemini
- **Ветка**: task/task-037-validatsiya-chislovyh-polej-konfiga-gemini

### TASK-038: Валидация команд агентов через shlex + shutil.which
- **Статус**: in_progress:both
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: TASK-036
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/config.py
- **Интеграция**: добавить функцию `validate_agent_commands(cfg: Config) -> None` в config.py; вызвать её в конце `load_config()` перед `return cfg`
- **Описание**: Реализовать `validate_agent_commands(cfg: Config)`, которая для каждого включённого агента: 1) парсит команду через `shlex.split(command)[0]` (берёт только бинарник, игнорируя аргументы); 2) проверяет наличие через `shutil.which(binary)`; 3) если бинарник не найден — `log.warning("Агент '{name}': команда '{cmd}' не найдена в PATH")`, НЕ ошибка; 4) если команда пустая — `ConfigValidationError`. Функция должна принимать dict распарсенных агентов, не работать с файловой системой напрямую.
- **Запрещено**: вызывать `shutil.which` на всей строке команды (включая аргументы); использовать `os.system` для проверки; делать `sys.exit` при отсутствии команды (только warning); хардкодить список агентов
- **Проверка**: ruff check forgerace/config.py && python -c "from forgerace.config import Config, validate_agent_commands; c = Config(); c.agents['test'] = type('A',(),{'command':'nonexistent_binary_xyz','enabled':True,'args':[],'review_args':[],'inactivity_timeout':300,'protocol':'cli','cognitive_frame':''})(); validate_agent_commands(c)" 2>&1 | grep -qi warning && echo "PASS"
- **Критерий готовности**: команды агентов с аргументами (например "claude-cli --model sonnet") корректно проверяются по бинарнику; отсутствующие команды дают warning, пустые — ConfigValidationError
- **Дискуссия**: 21-config-validation-tipy-diapazony-path-ch
- **Агент**: —
- **Ветка**: —

### TASK-039: Валидация root_dir — существование директории
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: TASK-036
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/config.py
- **Интеграция**: добавить функцию `validate_paths(cfg: Config) -> None` в config.py; вызвать её в конце `load_config()` перед `return cfg`
- **Описание**: Реализовать `validate_paths(cfg: Config)`, которая проверяет: 1) `cfg.root_dir` — директория существует (`Path.is_dir()`); если нет — `ConfigValidationError("root_dir '{path}' не существует")`. 2) `cfg.discuss_dir` — если указан явно, проверить существование; если нет — `log.warning` (не error, директория может быть создана позже). 3) `cfg.agents_dir` — аналогично. Fail-fast только для `root_dir`, для вспомогательных директорий — warning.
- **Запрещено**: автоматически создавать директории (только проверка); использовать `sys.exit` внутри функции; проверять существование файлов вместо директорий; fail-fast для discuss_dir/agents_dir
- **Проверка**: ruff check forgerace/config.py && python -c "from forgerace.config import Config, validate_paths; from forgerace.config_errors import ConfigValidationError; from pathlib import Path; c = Config(); c.root_dir = Path('/nonexistent_dir_xyz'); validate_paths(c)" 2>&1 | grep -q ConfigValidationError && echo "PASS"
- **Критерий готовности**: несуществующий root_dir вызывает ConfigValidationError; отсутствующие discuss_dir/agents_dir — warning с подсказкой
- **Дискуссия**: 21-config-validation-tipy-diapazony-path-ch
- **Агент**: gemini
- **Ветка**: task/task-039-validatsiya-root-dir-sushchestvovanie-di-gemini

### TASK-040: Перехват ошибок загрузки конфига в cli.py
- **Статус**: in_progress:both
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: TASK-036
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/cli.py
- **Интеграция**: обернуть вызов `init_config()` в `main()` (строка ~728) в try/except; добавить `from .config_errors import ConfigValidationError`
- **Описание**: В `main()` обернуть `init_config(config_path=args.config, root_dir=args.root)` в try/except с обработкой: 1) `ConfigValidationError` — `log.error("Ошибка конфигурации: {e.message}")` + `sys.exit(1)`; 2) `tomllib.TOMLDecodeError` — `log.error("Ошибка парсинга TOML в {config_path}: {e}")` с указанием строки/колонки из исключения + `sys.exit(1)`; 3) `FileNotFoundError` — `log.error("Файл конфига не найден: {path}. Запустите 'forgerace init' или укажите --config")` + `sys.exit(1)`. Обработка должна быть в точке входа (cli.py), не в config.py.
- **Запрещено**: использовать `sys.exit` внутри config.py; перехватывать все исключения через bare `except`; молча игнорировать ошибки; дублировать логику валидации из config.py
- **Проверка**: ruff check forgerace/cli.py && python -c "from forgerace.cli import main; import sys; sys.argv = ['forgerace', '--config', '/nonexistent.toml', 'run']; main()" 2>&1 | grep -qi "не найден\|init" && echo "PASS"
- **Критерий готовности**: отсутствующий файл конфига — понятное сообщение с подсказкой про init; кривой TOML — сообщение с файлом и позицией ошибки; ошибка валидации — конкретное сообщение о проблемном поле
- **Дискуссия**: 21-config-validation-tipy-diapazony-path-ch
- **Агент**: —
- **Ветка**: —

### TASK-041: Юнит-тесты валидации конфига
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: TASK-037, TASK-038, TASK-039, TASK-040
- **Файлы (новые)**: tests/test_config_validation.py
- **Файлы (modify)**: —
- **Интеграция**: —
- **Описание**: Написать 12-15 юнит-тестов для функций валидации без работы с файловой системой. Тест-кейсы: 1) валидный конфиг — проходит; 2) agent_timeout = -1 — ConfigValidationError; 3) agent_timeout = "string" — ConfigValidationError; 4) agent_timeout = 0.5 (float) — проходит, приводится к float; 5) max_retries = 0 — проходит; 6) max_retries = -1 — ConfigValidationError; 7) max_parallel_tasks = 2.5 — ConfigValidationError; 8) progress_timeout >= agent_timeout — warning; 9) команда агента = "" — ConfigValidationError; 10) команда с аргументами — проверяется только бинарник; 11) root_dir не существует — ConfigValidationError; 12) discuss_dir не существует — warning. Использовать pytest, мокировать `shutil.which` и `Path.is_dir`.
- **Запрещено**: использовать реальную файловую систему в тестах (только моки); тестировать через CLI (только unit-уровень); дублировать логику валидации в тестах; использовать `unittest.TestCase` вместо pytest
- **Проверка**: ruff check tests/test_config_validation.py && pytest tests/test_config_validation.py -v
- **Критерий готовности**: все 12-15 тестов проходят, покрывая валидные/невалидные кейсы для числовых полей, команд и путей; тесты не зависят от файловой системы
- **Дискуссия**: 21-config-validation-tipy-diapazony-path-ch
- **Агент**: —
- **Ветка**: —

Блок задач добавлен в TASKS.md. Структура:

- **TASK-036** — `ConfigValidationError` (базовое исключение, без зависимостей)
- **TASK-037** — валидация числовых полей (зависит от TASK-036)
- **TASK-038** — валидация команд агентов (зависит от TASK-036)
- **TASK-039** — валидация путей (зависит от TASK-036)
- **TASK-040** — перехват ошибок в cli.py (зависит от TASK-036)
- **TASK-041** — юнит-тесты (зависит от TASK-037, 038, 039, 040)

**Параллелизм**: TASK-037, 038, 039, 040 могут выполняться параллельно — все зависят только от TASK-036 и работают с разными функциями/файлами.

### TASK-022: merge.py — убрать checkout в основной repo
- **Статус**: done
- **Приоритет**: P1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/merge.py
- **Описание**: В merge_to_develop после update-ref выполняется `git checkout merge_sha -- fname` в cfg.root_dir. Это: 1) модифицирует index и рабочее дерево пользователя без ведома, 2) перезатирает незакоммиченные изменения, 3) staged changes которые пользователь не делал. Fix: убрать блок синхронизации файлов (строки с checkout). Вместо этого — после update-ref вызвать `git read-tree` или оставить только update-ref. Пользователь сам синхронизирует рабочее дерево через `git checkout -- .` если нужно.
- **Критерий готовности**: merge_to_develop не модифицирует рабочее дерево cfg.root_dir, только обновляет ref
- **Дискуссия**: —
- **Агент**: qwen
- **Ветка**: task/task-022-mergepy-ubrat-checkout-v-osnovnoj-repo-qwen

### TASK-023: Явная ошибка при пустом списке агентов/задач
- **Статус**: in_progress:both
- **Приоритет**: P2
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py, forgerace/cli.py
- **Описание**: Сейчас при отсутствии агентов или задач pipeline молча завершается пустым прогоном. Fix: 1) в начале run_pipeline проверить cfg.agent_names — если пуст, log.error("Нет активных агентов. Включите хотя бы одного в forgerace.toml") и return, 2) FileNotFoundError на TASKS.md — перехватить в cli.py, вывести "TASKS.md не найден. Запустите forgerace init", 3) TOMLDecodeError — перехватить, вывести файл и ошибку.
- **Критерий готовности**: пустой agents → понятное сообщение, нет TASKS.md → подсказка про init, кривой TOML → файл + ошибка
- **Дискуссия**: —
- **Агент**: —
- **Ветка**: —

### TASK-024: verify_build — фиксировать base SHA до начала задачи
- **Статус**: in_progress:both
- **Приоритет**: P1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Описание**: verify_build делает `git diff --stat cfg.dev_branch` в worktree. Но merge_to_develop двигает указатель dev_branch (через update-ref). После мержа одной задачи у всех остальных агентов diff base сдвигается → has_changes=True даже если агент ничего не написал. Fix: при создании worktree сохранить base_sha (SHA коммита от которого создана ветка), и в verify_build использовать base_sha вместо cfg.dev_branch.
- **Критерий готовности**: verify_build использует фиксированный base SHA, агент-пустышка не проходит валидацию после мержа другой задачи
- **Дискуссия**: —
- **Агент**: —
- **Ветка**: —
