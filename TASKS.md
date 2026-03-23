
 TASKS — forgerace

### TASK-002: Валидация ревью с бизнес-правилами
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: TASK-001
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/review.py
- **Интеграция**: —
- **Описание**: Реализовать функцию `validate_review(data: dict) -> tuple[bool, str]`. Проверки: соответствие `REVIEW_SCHEMA`, APPROVED + critical issues → невалидно, NEEDS_REWORK без issues → невалидно, REJECTED без issues → невалидно, confidence вне диапазона → невалидно. Убрать старый костыль с проверкой длины COMMENTS > 20 символов.
- **Критерий готовности**: `validate_review` корректно отклоняет невалидные комбинации вердикт/issues, старый парсинг `VERDICT:`/`COMMENTS:` удалён
- **Дискуссия**: future
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

### TASK-014: Учёт зависимостей (depends) в TaskQueue
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 3
- **Зависимости**: TASK-013
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/task_queue.py, forgerace/pipeline.py
- **Интеграция**: заменить текущий запуск всех задач на очередь
- **Описание**: Добавить отслеживание зависимостей: задача не запускается, пока не завершены все задачи из `depends:`. Метод `mark_complete(task_id)` для отметки завершения. Проверка готовности задачи перед запуском.
- **Критерий готовности**: задача с зависимостями запускается только после завершения всех зависимых задач
- **Дискуссия**: future
- **Агент**: —
- **Ветка**: —

### TASK-015: Интеграция TaskQueue в pipeline.py
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 3
- **Зависимости**: TASK-014
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: заменить текущий запуск всех задач разом на TaskQueue
- **Описание**: Заменить существующий код запуска задач (ThreadPoolExecutor + as_completed) на новый TaskQueue. Передача задач из `find_ready_tasks()` в очередь с приоритетами.
- **Критерий готовности**: пайплайн работает с новой очередью, задачи запускаются по приоритету с учётом лимита и зависимостей
- **Дискуссия**: future
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
- **Статус**: blocked
- **Приоритет**: P1
- **Этап**: 4
- **Зависимости**: TASK-016
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: —
- **Описание**: В pipeline (где запускается агент) добавить проверку `estimated_usd` против `budget_per_task_usd` из конфига. При превышении — убивать процесс агента (subprocess.terminate/kill), помечать задачу статусом BUDGET_EXCEEDED через `update_task_status`.
- **Критерий готовности**: Агент останавливается при превышении бюджета, задача помечается BUDGET_EXCEEDED
- **Дискуссия**: future
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
