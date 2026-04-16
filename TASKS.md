
 TASKS — forgerace

### TASK-080: Подключить validate_review + config consensus
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/review.py, forgerace/config.py
- **Интеграция**: —
- **Описание**: Вызывать validate_review() в single_review() для валидации структуры ответа ревьюера. Добавить в Config: review_consensus (bool, default false), min_reviewers (int, default 2). Парсить из [limits] в forgerace.toml.
- **Критерий готовности**: validate_review() вызывается при каждом ревью. Новые поля конфига читаются и доступны.
- **Дискуссия**: championship-ensemble-review
- **Команда**: Team Gemini

### TASK-070: Добавить функцию format_duration в utils.py
- **Статус**: done
- **Приоритет**: P2
- **Этап**: 1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/utils.py
- **Интеграция**: —
- **Описание**: Добавить функцию `format_duration(seconds: float) -> str` в `forgerace/utils.py`. Функция должна форматировать длительность в человекочитаемый вид: менее 60с → "42s", 60-3600с → "2m 15s", более 3600с → "1h 5m". Использовать в логах вместо raw секунд.
- **Критерий готовности**: Функция написана, корректно форматирует длительности для всех диапазонов.

### TASK-042: Реализация функции валидации и обновление схемы ревью
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/review.py
- **Интеграция**: —
- **Описание**: Обновить `REVIEW_SCHEMA` (добавить `is_terminal`, `confidence_range`). Реализовать функцию `validate_review(data: dict) -> tuple[bool, str]`. Функция должна приводить `confidence` к `int` (принимая `float` или `int`), переименовать `NEEDS_WORK` в `NEEDS_REWORK` (с сохранением алиаса `NEEDS_WORK` для обратной совместимости). Добавить парсинг списка `issues` из строк формата `[severity] текст` в нормализованный `list[dict]`.
- **Критерий готовности**: Функция `validate_review` написана, корректно обрабатывает входные данные, отклоняет неверный `confidence`, несовместимые статусы (APPROVED с критическими ошибками, REJECTED без ошибок). Схема `REVIEW_SCHEMA` обновлена.
- **Дискуссия**: 2-validatsiya-revyu-s-biznes-pravilami
- **Агент**: gemini
- **Ветка**: task/task-042-realizatsiya-funktsii-validatsii-i-obnov-gemini

### TASK-043: Покрытие логики ревью unit-тестами
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: TASK-042
- **Файлы (новые)**: tests/test_review.py
- **Файлы (modify)**: —
- **Интеграция**: —
- **Описание**: Написать 8-10 unit-тестов для проверки функции `validate_review` и логики парсинга `issues`. Тесты должны покрывать успешные сценарии (APPROVED без issues, NEEDS_REWORK с issues) и провальные (выход за пределы confidence, APPROVED с critical issues, REJECTED без issues).
- **Критерий готовности**: Написаны и успешно проходят 8-10 тестов, проверяющих все бизнес-правила валидации ревью.
- **Дискуссия**: 2-validatsiya-revyu-s-biznes-pravilami
- **Агент**: gemini
- **Ветка**: task/task-043-pokrytie-logiki-revyu-unit-testami-gemini

### TASK-047: Интеграция validate_review в forgerace/review.py
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/review.py
- **Интеграция**: Вызов функции `validate_review` в процессе формирования результата ревью.
- **Описание**: В функции `single_review` необходимо найти и полностью удалить старую проверку на минимальную длину комментария (костыль вида `COMMENTS > 20`). Вместо неё нужно импортировать и интегрировать вызов `validate_review` для проверки ответа агента-ревьюера. Убедиться, что возвращаемый вердикт корректно пробрасывается (включая статус и флаг `is_terminal`).
- **Критерий готовности**: Функция `single_review` использует `validate_review` для оценки результата, старый костыль удален.
- **Дискуссия**: 2-validatsiya-revyu-s-biznes-pravilami
- **Агент**: gemini
- **Ветка**: task/task-047-integratsiya-validate-review-v-forgerace-gemini

### TASK-050: Поддержка REJECTED и IS_TERMINAL в review.py
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/review.py
- **Интеграция**: Обновить парсер и промпт ревьюера.
- **Описание**: 1. Добавить `REJECTED` в список возможных вердиктов в промпте `single_review`. 2. Ввести поле `IS_TERMINAL: TRUE/FALSE` в формат ответа ревьюера. 3. Реализовать парсинг нового статуса и флага. 4. Обновить `code_review`, чтобы флаг `is_terminal` пробрасывался в общий результат, если он установлен для выбранного (best) результата.
- **Критерий готовности**: Функция `single_review` возвращает `verdict='REJECTED'` и `is_terminal=True` при соответствующих данных в ответе LLM.
- **Дискуссия**: 2-validatsiya-revyu-s-biznes-pravilami

### TASK-051: Обработка терминального отказа в конкурентном режиме
- **Статус**: review:gemini
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: TASK-050
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: Изменение логики `execute_task_competitive`.
- **Описание**: 1. В фазе "race" (первичное ревью после завершения одного из агентов) добавить проверку: если вердикт не `APPROVED` и установлен `is_terminal`, не вызывать `send_to_rework` для этого агента. 2. В основном цикле `review_round` добавить аналогичную проверку: при терминальном отказе немедленно прерывать цикл, устанавливать статус `blocked` и выходить.
- **Критерий готовности**: При получении терминального REJECTED в конкурентном режиме выполнение ветки/задачи прекращается без попыток retry.
- **Дискуссия**: 2-validatsiya-revyu-s-biznes-pravilami

### TASK-052: Обработка терминального отказа в одиночном режиме
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: TASK-050
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: Изменение логики `execute_task_single`.
- **Описание**: В цикле ревью `execute_task_single` добавить проверку: если `rv['verdict'] == 'REJECTED'` и `rv.get('is_terminal')`, немедленно прерывать выполнение, устанавливать статус задачи `blocked` и выходить из функции.
- **Критерий готовности**: В одиночном режиме терминальный отказ блокирует задачу без перехода к фазе доработки (send_to_rework).
- **Дискуссия**: 2-validatsiya-revyu-s-biznes-pravilami

### TASK-053: Верификация логики прерывания (тест-скрипт)
- **Статус**: open
- **Приоритет**: P2
- **Этап**: 1
- **Зависимости**: TASK-051, TASK-052
- **Файлы (новые)**: tests/repro_terminal_reject.py
- **Файлы (modify)**: —
- **Интеграция**: Автономный проверочный скрипт.
- **Описание**: Создать скрипт, который имитирует (через моки или подмену ответов) терминальный отказ ревьюера и проверяет, что оркестратор в `pipeline.py` корректно завершает работу, не делая повторных вызовов агента.
- **Критерий готовности**: Скрипт подтверждает, что при `is_terminal=True` количество попыток (retries) равно 0.
- **Дискуссия**: 2-validatsiya-revyu-s-biznes-pravilami

### TASK-003: Rework counter и TaskState
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: TASK-002
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/tasks.py
- **Интеграция**: —
- **Описание**: Добавить dataclass `TaskState` с полями `rework_count: int`, `last_attempts: list[str]` (последние 3 diff summary). Реализовать `build_rework_prompt(task, state) -> str` — формирует блок промпта с историей предыдущих неудачных попыток. Добавить параметр `max_reworks` в `[limits]` (дефолт 3), с возможностью переопределения через `<!-- config: max_reworks=N -->` в теле задачи TASKS.md.
- **Критерий готовности**: `TaskState` хранит историю попыток, `build_rework_prompt` генерирует summary, `max_reworks` читается из конфига и задачи
- **Дискуссия**: future
- **Агент**: gemini
- **Ветка**: task/task-003-rework-counter-i-taskstate-gemini

### TASK-054: Расширение Config и модели Task
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/config.py, forgerace/tasks.py
- **Интеграция**: —
- **Описание**: Добавить параметр `max_reworks` (по умолчанию 3) в класс `Config` и обновить `load_config` для его загрузки из TOML. В dataclass `Task` добавить поля `rework_count: int = 0` и `last_attempts: list[dict] = field(default_factory=list)`. Обновить регулярные выражения в `parse_tasks` и логику в `update_task_status`, чтобы эти поля корректно читались из `TASKS.md` и записывались в него (поле `last_attempts` рекомендуется хранить в виде JSON-строки для упрощения парсинга).
- **Критерий готовности**: Поля `rework_count` и `last_attempts` корректно парсятся из `TASKS.md` и сохраняются при вызове `update_task_status`.

). I must first implement the missing foundations: updating `Config` and `Task`, fixing the `TASKS.md` parser/updater, and implementing `build_rework_prompt`. TASK-055 specifically requires handling `NEEDS_REWORK`, incrementing counters, managing `stuck` status, and saving attempts. I'll need to check `forgerace/review.py` for returned verdicts to correctly integrate this logic into `pipeline.py`.
I'll check the verdict parsing in `review.py`.
`review.py` defaults to `NEEDS_WORK` and doesn't explicitly check for `NEEDS_REWORK`, despite TASK-042's requirements. TASK-055 requires implementing `NEEDS_REWORK` handling, incrementing `rework_count`, checking `max_reworks`, transitioning to `stuck`, and updating `last_attempts`. Given that several "done" tasks (003, 042, 054, 056) are missing or incomplete, I'm rating this COMPLEXITY: 4. I'll break it down into three subtasks: 1) extending `Config` and `Task` (fixing TASK-054), 2) implementing the `stuck` and counter logic in `pipeline.py` (TASK-055 core), and 3) injecting attempt history into prompts (fixing TASK-056). I also need to verify if the `stuck` status exists anywhere in the current codebase.
In `pipeline.py`, `stuck` isn't a terminal task status yet, just a local variable. TASK-055 requires implementing it as a terminal status, incrementing `rework_count`, and saving attempt history. Since several prerequisite tasks (003, 054, 056) are missing from the codebase, I'm rating this COMPLEXITY: 4 and breaking it down into three subtasks: 1) extending the `Config` and `Task` models (fixing TASK-054), 2) implementing the `stuck` logic and counter in `pipeline.py` (core TASK-055), and 3) generating attempt history and updating prompts (fixing TASK-056). I'll use `list[dict]` for `last_attempts` for flexibility.

COMPLEXITY: 4

### TASK-060: Расширение моделей данных Config и Task
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/config.py, forgerace/tasks.py
- **Интеграция**: —
- **Описание**: Добавить параметр `max_reworks: int = 3` в класс `Config`. В классе `Task` добавить поля `rework_count: int = 0` и `last_attempts: list[dict] = field(default_factory=list)`. 
- **Критерий готовности**: Код компилируется, объекты `Config` и `Task` инициализируются с новыми полями по умолчанию.

### TASK-061: Обновление парсера parse_tasks
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: TASK-060
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/tasks.py
- **Интеграция**: —
- **Описание**: Обновить функцию `parse_tasks` и вспомогательный метод `_field` для извлечения `rework_count` и `last_attempts` из Markdown-блоков. Поле `last_attempts` должно десериализоваться из JSON-строки. Обработать возможные ошибки парсинга JSON, чтобы не ломать чтение всего файла.
- **Критерий готовности**: Функция `parse_tasks` корректно заполняет новые поля при наличии их в `TASKS.md`.

### TASK-062: Обновление механизма сохранения update_task_status
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: TASK-061
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/tasks.py
- **Интеграция**: —
- **Описание**: Обновить логику записи в `TASKS.md` (вероятно, в `update_task_status` или аналогичном методе сериализации). Новые поля должны записываться в формате `**Переделки**: 0` и `**Последние попытки**: [...]` (JSON в одну строку). Необходимо гарантировать, что JSON-строка не содержит символов, ломающих регулярные выражения парсера.
- **Критерий готовности**: После вызова обновления статуса, в файле `TASKS.md` появляются новые поля, которые затем успешно считываются парсером из TASK-061.

### TASK-058: Интеграция логики STUCK и rework counter в pipeline.py
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: TASK-062
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: —
- **Описание**: В функциях `execute_task_competitive` и `execute_task_single` реализовать обработку вердикта `NEEDS_REWORK` (или любого не-APPROVED). При каждой итерации: 1) Инкрементировать `task.rework_count`. 2) Если `rework_count >= cfg.max_reworks`, выставлять статус `stuck` и прерывать выполнение. 3) Перед вызовом `send_to_rework` сохранять `git diff --stat` и комментарии ревьюера в `task.last_attempts`.
- **Критерий готовности**: При достижении лимита правок задача помечается как `stuck`, история попыток сохраняется в `TASKS.md`.

### TASK-089: Реализация функции build_rework_prompt
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/agents.py
- **Интеграция**: Добавить вызов функции в `build_prompt`
- **Описание**: Реализовать функцию `build_rework_prompt(attempts: list[dict]) -> str`, формирующую текстовый блок с историей предыдущих ошибок
- **Критерий готовности**: Функция должна корректно формировать текстовый блок с историей предыдущих ошибок
- **Дискуссия**: —

### TASK-091: Расширение моделей Task и Config
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/tasks.py, forgerace/config.py
- **Интеграция**: Добавить поля `rework_count: int = 0` и `last_attempts: list[dict] = field(default_factory=list)` в класс `Task`. Добавить `max_reworks: int = 3` в `Config`.
- **Описание**: Реализовать поддержку новых полей в датаклассах и обновить парсер `parse_tasks` и метод `update_task_status` в `tasks.py`, чтобы история попыток корректно сохранялась в `TASKS.md` (рекомендуется формат JSON для `last_attempts`).
- **Критерий готовности**: Поля `rework_count` и `last_attempts` успешно читаются из файла и записываются в него без потери данных.

### TASK-092: Реализация build_rework_prompt и интеграция в build_prompt
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: TASK-091
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/agents.py
- **Интеграция**: Реализовать `build_rework_prompt(attempts: list[dict]) -> str` и вызвать её внутри `build_prompt`.
- **Описание**: Функция должна генерировать структурированный текст (Markdown), содержащий номер итерации, комментарии ревьюера и (опционально) краткий diff или список измененных файлов из предыдущих попыток. Интегрировать этот блок в основной системный промпт.
- **Критерий готовности**: В системном промпте агента появляется блок "ИСТОРИЯ ПРЕДЫДУЩИХ ПОПЫТОК" с детальным описанием замечаний, если список `attempts` не пуст.

### TASK-094: Инкрементирование счетчика доработок
- **Статус**: in_progress:both
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: Добавить инкрементирование `task.rework_count` в функциях `execute_task_competitive` и `execute_task_single`.
- **Описание**: Реализовать инкрементирование счетчика доработок при получении вердикта `NEEDS_REWORK`.
- **Критерий готовности**: Счетчик доработок должен корректно обновляться после каждого раунда доработки.

### TASK-095: Сбор комментариев ревьюеров
- **Статус**: in_progress:both
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: Добавить сбор комментариев всех ревьюеров в функциях `execute_task_competitive` и `execute_task_single`.
- **Описание**: Реализовать сбор комментариев ревьюеров при получении вердикта `NEEDS_REWORK`.
- **Критерий готовности**: Комментарии ревьюеров должны корректно собираться и сохраняться после каждого раунда доработки.

### TASK-096: Сохранение git diff --stat
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: Добавить сохранение `git diff --stat` в функциях `execute_task_competitive` и `execute_task_single`.
- **Описание**: Реализовать сохранение `git diff --stat` при получении вердикта `NEEDS_REWORK`.
- **Критерий готовности**: `git diff --stat` должен корректно сохраняться после каждого раунда доработки.

### TASK-097: Сохранение истории задачи
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: TASK-094, TASK-095, TASK-096
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: Добавить сохранение истории задачи в функциях `execute_task_competitive` и `execute_task_single`.
- **Описание**: Реализовать сохранение истории задачи, включая счетчик доработок, комментарии ревьюеров и `git diff --stat`.
- **Критерий готовности**: История задачи должна корректно обновляться после каждого раунда доработки.

### TASK-056: Реализация build_rework_prompt и инжекция истории в промпт
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: TASK-054
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/agents.py
- **Интеграция**: —
- **Описание**: Реализовать функцию `build_rework_prompt(attempts: list[dict]) -> str`, которая превращает историю попыток в структурированный текст для агента. Интегрировать вызов этой функции в `build_prompt`. Новый блок промпта должен сообщать агенту номер текущей попытки и предоставлять контекст предыдущих неудач (какой diff был предложен и какие именно претензии возникли у ревьюера), чтобы избежать повторения ошибок.
- **Критерий готовности**: Агент получает в системном промпте детальную историю предыдущих итераций ревью и правок.

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
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: добавить `tasks_md_lock = threading.Lock()` и `safe_update_task_status`
- **Описание**: Создать `tasks_md_lock = threading.Lock()` на уровне модуля `pipeline.py`. Реализовать функцию `safe_update_task_status(*args, **kwargs)`, которая вызывает оригинальный `update_task_status` строго внутри блока `with tasks_md_lock:`. Использование функции-обертки позволит избежать массового изменения отступов в 30+ местах (что было причиной предыдущих провалов).
- **Критерий готовности**: В файле объявлен лок и присутствует функция `safe_update_task_status`, синтаксис файла корректен.
- **Дискуссия**: 15-integratsiya-taskqueue-v-pipelinepy
- **Агент**: gemini
- **Ветка**: task/task-045-sozdanie-blokirovki-i-bezopasnoj-obertki-gemini

### TASK-046: Перевод обновлений TASKS.md на потокобезопасную функцию
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: TASK-045
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: заменить вызовы `update_task_status` на `safe_update_task_status`
- **Описание**: Найти все места записи статусов задач в `pipeline.py` (более 30 вызовов `update_task_status`) и переименовать вызовы на `safe_update_task_status`. 
- **Критерий готовности**: Внутри `pipeline.py` не осталось незащищенных вызовов обновления `TASKS.md`, одновременные апдейты из нескольких потоков не приводят к повреждению файла.
- **Дискуссия**: 15-integratsiya-taskqueue-v-pipelinepy
- **Агент**: gemini
- **Ветка**: task/task-046-perevod-obnovlenij-tasksmd-na-potokobezo-gemini

### TASK-047: Подготовка и инициализация TaskQueue в pipeline.py
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: импорт TaskQueue, инициализация в run_pipeline
- **Описание**: Добавить импорт `TaskQueue` из `forgerace.task_queue`. В основном блоке оркестрации инициализировать `queue = TaskQueue(max_concurrent=cfg.limits_max_concurrent)`. Создать словарь-маппинг `task_id -> Task` для всех готовых задач, чтобы иметь доступ к объектам `Task` при извлечении из приоритетной очереди. Заполнить очередь начальными задачами через `queue.push(task.id, task.priority)`.
- **Критерий готовности**: Код корректно инициализирует очередь с лимитом из конфигурации, начальные задачи добавлены в очередь.
- **Дискуссия**: 15-integratsiya-taskqueue-v-pipelinepy

### TASK-048: Потокобезопасность методов TaskQueue
- **Статус**: done
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/task_queue.py
- **Интеграция**: добавить threading.Lock в TaskQueue
- **Описание**: Добавить `threading.Lock` в класс `TaskQueue` и использовать его во всех методах, манипулирующих внутренним состоянием (`_heap` и `_counter`): `push`, `pop`, `empty`, `__len__`. Это критично для будущего динамического пополнения очереди из callback-потоков.
- **Критерий готовности**: Методы `TaskQueue` потокобезопасны; тесты на конкурентный доступ (если есть) проходят.
- **Дискуссия**: 15-integratsiya-taskqueue-v-pipelinepy

### TASK-049: Реализация основного цикла оркестрации
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: TASK-047, TASK-053
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: замена блока ThreadPoolExecutor + as_completed
- **Описание**: Заменить старый блок запуска батча на цикл `while queue or active_futures`. Внутри цикла: 1) Если есть свободные слоты (`active_count < max_concurrent`) и очередь не пуста, делать `pop()` и `submit()` через `execute_task_competitive` или `execute_task_single`. 2) Использовать `concurrent.futures.wait(active_futures, return_when=FIRST_COMPLETED)` для ожидания завершения хотя бы одной задачи. 3) Обрабатывать результаты завершенных задач, обновлять статусы и логировать итоги.
- **Критерий готовности**: Задачи запускаются строго по приоритету, соблюдается лимит параллельности, цикл корректно завершается при пустой очереди и отсутствии активных задач.
- **Дискуссия**: 15-integratsiya-taskqueue-v-pipelinepy

### TASK-030: Callback завершения задачи с обновлением статусов
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 3
- **Зависимости**: TASK-049
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
- **Зависимости**: TASK-049
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
- **Статус**: done
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
- **Агент**: gemini
- **Ветка**: task/task-034-vozvrat-agentprocessresult-so-statusom-b-gemini

### TASK-035: Обработка BUDGET_EXCEEDED в pipeline.py
- **Статус**: done
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
- **Агент**: gemini
- **Ветка**: task/task-035-obrabotka-budget-exceeded-v-pipelinepy-gemini

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
- **Статус**: done
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
- **Агент**: gemini
- **Ветка**: task/task-038-validatsiya-komand-agentov-cherez-shlex--gemini

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
- **Статус**: done
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
- **Агент**: gemini
- **Ветка**: task/task-040-perehvat-oshibok-zagruzki-konfiga-v-clip-gemini

### TASK-041: Юнит-тесты валидации конфига
- **Статус**: done
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
- **Агент**: gemini
- **Ветка**: task/task-041-yunit-testy-validatsii-konfiga-gemini

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
- **Статус**: done
- **Приоритет**: P2
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py, forgerace/cli.py
- **Описание**: Сейчас при отсутствии агентов или задач pipeline молча завершается пустым прогоном. Fix: 1) в начале run_pipeline проверить cfg.agent_names — если пуст, log.error("Нет активных агентов. Включите хотя бы одного в forgerace.toml") и return, 2) FileNotFoundError на TASKS.md — перехватить в cli.py, вывести "TASKS.md не найден. Запустите forgerace init", 3) TOMLDecodeError — перехватить, вывести файл и ошибку.
- **Критерий готовности**: пустой agents → понятное сообщение, нет TASKS.md → подсказка про init, кривой TOML → файл + ошибка
- **Дискуссия**: —
- **Агент**: gemini
- **Ветка**: task/task-023-yavnaya-oshibka-pri-pustom-spiske-agento-gemini

### TASK-024: verify_build — фиксировать base SHA до начала задачи
- **Статус**: done
- **Приоритет**: P1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Описание**: verify_build делает `git diff --stat cfg.dev_branch` в worktree. Но merge_to_develop двигает указатель dev_branch (через update-ref). После мержа одной задачи у всех остальных агентов diff base сдвигается → has_changes=True даже если агент ничего не написал. Fix: при создании worktree сохранить base_sha (SHA коммита от которого создана ветка), и в verify_build использовать base_sha вместо cfg.dev_branch.
- **Критерий готовности**: verify_build использует фиксированный base SHA, агент-пустышка не проходит валидацию после мержа другой задачи
- **Дискуссия**: —
- **Агент**: gemini
- **Ветка**: task/task-024-verify-build-fiksirovat-base-sha-do-gemini

### TASK-117: Расширение конфигурации и логики статусов ревью
- **Статус**: blocked
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/config.py, forgerace/review.py
- **Интеграция**: —
- **Описание**: 1. Добавить в `Config` (в `forgerace/config.py`) поля `review_consensus: bool = False` и `min_reviewers: int = 2` (с поддержкой загрузки из TOML). 2. В `forgerace/review.py` обновить функцию `single_review`: если ответ от LLM пустой, не содержит `VERDICT` или содержит битый JSON (который не прошел парсинг), возвращать словарь с `verdict: "FAILED"`. Сейчас такие ошибки часто маскируются под `NEEDS_WORK` или `error`.
- **Критерий готовности**: Новые поля конфигурации доступны через `cfg`, а `single_review` корректно классифицирует технические сбои как `FAILED`.
- **Дискуссия**: championship-ensemble-review
- **Агент**: gemini
- **Ветка**: task/task-117-realizatsiya-funktsii-detektsii-tupikov-gemini

### TASK-118: Реализация алгоритма ConsensusEngine
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: TASK-117
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/review.py
- **Интеграция**: —
- **Описание**: Реализовать функцию `calculate_consensus(verdicts: list[dict]) -> dict`. 
Логика:
1. Исключить из обработки все вердикты со статусом `FAILED`.
2. Если количество оставшихся (валидных) вердиктов меньше `cfg.min_reviewers` — вернуть итоговый вердикт `NEEDS_WORK` с комментарием "Insufficient review coverage".
3. Если среди валидных вердиктов есть хотя бы один `NEEDS_WORK` — вернуть итоговый `NEEDS_WORK` (строгое вето).
4. Если все валидные вердикты — `APPROVED` — вернуть итоговый `APPROVED`.
- **Критерий готовности**: Функция корректно вычисляет консенсус для всех сценариев (единогласие, вето, нехватка голосов, технические ошибки ревьюеров).
- **Дискуссия**: championship-ensemble-review
- **Агент**: —
- **Ветка**: —

### TASK-119: Модульное тестирование логики консенсуса
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: TASK-118
- **Файлы (новые)**: tests/test_consensus.py
- **Файлы (modify)**: —
- **Интеграция**: —
- **Описание**: Написать набор unit-тестов для `calculate_consensus`. Тесты должны покрывать:
- Успешное одобрение при выполнении `min_reviewers`.
- Вето при наличии одного `NEEDS_WORK`.
- Отклонение (Insufficient coverage) при наличии `FAILED` ответов, из-за которых число валидных падает ниже `min_reviewers`.
- Обработку пустого списка входных данных.
- **Критерий готовности**: Тесты проходят и подтверждают корректность алгоритма вето и подсчета покрытия.
- **Дискуссия**: championship-ensemble-review
- **Агент**: —
- **Ветка**: —

### TASK-099: Параллельная оркестрация ревью в Pipeline
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: TASK-119
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py, forgerace/review.py
- **Интеграция**: —
- **Описание**: В code_review() при review_consensus=true запускать min_reviewers ревьюеров параллельно через ThreadPoolExecutor. Каждый ревьюер — изолированный вызов single_review(). Результаты собираются после завершения всех (или таймаута). Передаются в calculate_consensus().
- **Критерий готовности**: Параллельный запуск 2+ ревьюеров, результаты агрегируются через consensus.
- **Дискуссия**: championship-ensemble-review
- **Команда**: Team Gemini

### TASK-100: Механизмы Fallback и обработка отказов
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: TASK-119
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/review.py, forgerace/pipeline.py
- **Интеграция**: —
- **Описание**: Fallback на single review если consensus review падает (все API ошибки). Если review_consensus=false — текущий single-review mode без изменений. Валидация: min_reviewers не больше доступных моделей. Retry невалидных ответов (макс 1 раз).
- **Критерий готовности**: Pipeline работает и с consensus=true, и с consensus=false. Fallback при ошибках API.
- **Дискуссия**: championship-ensemble-review
- **Команда**: Team Gemini

### TASK-101: Логирование вердиктов и Observability
- **Статус**: open
- **Приоритет**: P2
- **Этап**: 1
- **Зависимости**: TASK-099
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/review.py, forgerace/utils.py
- **Интеграция**: —
- **Описание**: Логировать вердикт каждого ревьюера: модель, verdict, ключевые замечания. Итоговый consensus: APPROVED/NEEDS_WORK + количество голосов. Цветная таблица в консоли. Добавить ANSI-паттерны для consensus в utils.py.
- **Критерий готовности**: В логах видны вердикты всех ревьюеров и итоговый consensus.
- **Дискуссия**: championship-ensemble-review
- **Команда**: Team Gemini

### TASK-102: Unit и Chaos тесты для consensus
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: TASK-119
- **Файлы (новые)**: tests/test_consensus.py
- **Файлы (modify)**: —
- **Интеграция**: —
- **Описание**: Тесты для calculate_consensus: единогласный APPROVED, вето одним NEEDS_WORK, split vote, все FAILED (insufficient coverage), битый JSON от модели, таймаут одной модели. Chaos-тест: 1 модель → 404, 2-я → битый JSON, 3-я → APPROVED → итог NEEDS_WORK.
- **Критерий готовности**: 8+ тестов проходят, покрывают все граничные сценарии.
- **Дискуссия**: championship-ensemble-review
- **Команда**: Team Gemini

### TASK-104: Логирование попыток
- **Статус**: done
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py, forgerace/utils.py
- **Интеграция**: добавить форматированную строку в лог при каждой попытке
- **Описание**: Реализовать логирование в формате "Attempt X/Y, prev: STATUS (reason)" с использованием существующей системы логов.
- **Запрещено**: использовать сторонние логировочные библиотеки
- **Проверка**: `make check`
- **Критерий готовности**: корректное логирование попыток
- **Дискуссия**: championship-smart-rework
- **Агент**: aider-devstral
- **Ветка**: task/task-104-logirovanie-popytok-aider-devstral

### TASK-117: Реализация функции детекции тупиков
- **Статус**: blocked
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: —
- **Файлы (новые)**: forgerace/stuck_detection.py
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: добавить вызов `detect_stuck` после каждой попытки
- **Описание**: реализовать функцию `detect_stuck`, которая использует простой строковый match (difflib.SequenceMatcher) для детекции тупиков на основе 3+ попыток с похожими замечаниями
- **Критерий готовности**: корректная детекция тупиков
- **Дискуссия**: championship-smart-rework
- **Агент**: gemini
- **Ветка**: task/task-117-realizatsiya-funktsii-detektsii-tupikov-gemini

### TASK-118: Интеграция детекции тупиков в пайплайн
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: TASK-117
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: добавить вызов `detect_stuck` после каждой попытки
- **Описание**: интегрировать функцию `detect_stuck` в пайплайн, чтобы она вызывалась после каждой попытки
- **Критерий готовности**: корректная детекция тупиков в пайплайне
- **Дискуссия**: championship-smart-rework
- **Агент**: —
- **Ветка**: —

### TASK-119: Тестирование детекции тупиков
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 3
- **Зависимости**: TASK-117, TASK-118
- **Файлы (новые)**: tests/test_stuck_detection.py
- **Файлы (modify)**: —
- **Интеграция**: добавить тесты для функции `detect_stuck`
- **Описание**: написать тесты для функции `detect_stuck`, чтобы проверить ее корректность
- **Критерий готовности**: прохождение всех тестов
- **Дискуссия**: championship-smart-rework
- **Агент**: —
- **Ветка**: —

### TASK-106: Post-mortem Report
- **Статус**: open
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: TASK-119
- **Файлы (новые)**: forgerace/postmortem.py
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: добавить вызов `generate_postmortem` при детекции тупика
- **Описание**: Создать модуль `postmortem.py` с функцией `generate_postmortem()`, которая принимает историю попыток и генерирует диагноз (паттерн, причина, рекомендация) в markdown.
- **Запрещено**: использовать сторонние API без кэширования результатов
- **Проверка**: `make check`
- **Критерий готовности**: корректная генерация диагноза
- **Дискуссия**: championship-smart-rework
- **Агент**: gemini
- **Ветка**: — 

### TASK-107: Тесты для stuck detection
- **Статус**: open
- **Приоритет**: P2
- **Этап**: N
- **Зависимости**: TASK-119
- **Файлы (новые)**: tests/test_stuck_detection.py
- **Файлы (modify)**: —
- **Интеграция**: добавить тесты для детекции тупиков
- **Описание**: Написать тесты для детекции тупиков, включая edge-cases.
- **Запрещено**: использовать mock-объекты без явной настройки
- **Проверка**: `make check`
- **Критерий готовности**: успешное прохождение тестов
- **Дискуссия**: championship-smart-rework
- **Агент**: qwen-api
- **Ветка**: — 

### TASK-117: Реализация парсера append-only лога в TASKS.md
- **Статус**: blocked
- **Приоритет**: P2
- **Этап**: 2
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/tasks.py
- **Интеграция**: Добавить функцию для извлечения истории попыток из `raw_section` задачи в `tasks.py`
- **Описание**: Написать регулярное выражение и логику для парсинга нового формата хранения `last_attempts` (append-only лог) внутри блока задачи в `TASKS.md`. Реализовать функцию, которая возвращает список попыток (дата, результат, агент) вместо одного поля. Убедиться, что парсер корректно обрабатывает отсутствие лога (пустой список).
- **Критерий готовности**: Функция парсинга успешно извлекает список попыток из примера `TASKS.md` и возвращает пустой список, если логов нет.
- **Дискуссия**: championship-smart-rework
- **Агент**: gemini
- **Ветка**: task/task-117-realizatsiya-funktsii-detektsii-tupikov-gemini

### TASK-118: Рефакторинг записи истории попыток (Append-Only)
- **Статус**: open
- **Приоритет**: P2
- **Этап**: 2
- **Зависимости**: TASK-117
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/tasks.py
- **Интеграция**: Обновить функцию обновления статуса задачи для записи новой попытки в конец лога вместо перезаписи поля
- **Описание**: Модифицировать логику обновления `TASKS.md`. Вместо замены значения поля `last_attempts` или `rework_count`, реализовать функцию, которая находит блок задачи, находит конец существующего лога попыток и добавляет новую запись в формате `YYYY-MM-DD HH:MM:SS | agent | result`. Сохранить глобальный `_tasks_file_lock` для атомарности записи.
- **Критерий готовности**: При обновлении статуса задачи в `TASKS.md` в блоке задачи появляется новая строка в логе попыток, старые записи сохраняются.
- **Дискуссия**: championship-smart-rework
- **Агент**: —
- **Ветка**: —

### TASK-119: Динамический расчет `rework_count` и удаление поля
- **Статус**: open
- **Приоритет**: P2
- **Этап**: 2
- **Зависимости**: TASK-117
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/tasks.py, forgerace/pipeline.py
- **Интеграция**: Заменить все обращения к `task.rework_count` на вызов функции-расчетчика
- **Описание**: Удалить поле `rework_count` из датакласса `Task` (если оно там есть) и логику его сохранения. Создать свойство или вспомогательную функцию `get_rework_count(task: Task)`, которая считает количество записей в парсируемом логе попыток. Обновить `pipeline.py` и другие места, где используется счетчик переделок, чтобы они использовали новую логику.
- **Критерий готовности**: Код не содержит ссылок на поле `rework_count`. Функция подсчета возвращает корректное число на основе лога в `TASKS.md`.
- **Дискуссия**: championship-smart-rework
- **Агент**: —
- **Ветка**: —

### TASK-110: Реализация функции pre-flight анализа
- **Статус**: blocked
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: TASK-109
- **Файлы (новые)**: forgerace/preflight.py
- **Файлы (modify)**: возможные вспомогательные функции в forgerace/utils.py
- **Интеграция**: Функция `run_preflight` должна вызывать LLM-агент и валидировать ответ
- **Описание**: Добавить в `forgerace/preflight.py` функцию `run_preflight(task: Task) -> PreflightResult`. Формировать промпт (описание задачи + дерево файлов + содержимое явно указанных файлов), вызывать LLM-агент, указанный в конфиге (`preflight_agent`). Обеспечить таймаут ≤ 15 сек, обработку ошибок и fallback-режим (при `confidence < 0.8` или ошибке – вернуть `None`). После получения ответа – валидировать через `validator`.
- **Запрещено**: Не обрабатывать ошибки и таймауты
- **Проверка**: `pytest forgerace/tests/test_preflight.py -v`
- **Критерий готовности**: Функция `run_preflight` работает корректно и возвращает результат анализа
- **Дискуссия**: championship-preflight
- **Агент**: @qwen-api+theory
- **Ветка**: — 

### TASK-111: Механизм кэширования результатов pre-flight
- **Статус**: open
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: TASK-110
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/preflight.py, forgerace/utils.py
- **Интеграция**: Кэш должен храниться в `.forgerace/preflight_cache.json`
- **Описание**: Реализовать дисковый кэш (например, в `.forgerace/preflight_cache.json`). Ключ кэша = `hash(task_text + hash_tree_structure + hash_content(relevant_files))`. При повторном запуске задачи проверять кэш и использовать сохранённый результат, если ключ совпадает. Добавить простую политику очистки (TTL = 7 дней) и функции `cache_get` / `cache_set`.
- **Запрещено**: Не использовать хэш содержимого файлов в ключе кэша
- **Проверка**: `pytest forgerace/tests/test_preflight.py -v`
- **Критерий готовности**: Кэш работает корректно и результаты анализа сохраняются
- **Дискуссия**: championship-preflight
- **Агент**: @devstral+architect
- **Ветка**: — 

### TASK-112: Расширение конфигурации и логирование pre-flight
- **Статус**: blocked
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/config.py, forgerace/utils.py
- **Интеграция**: Должна быть готова до интеграции в pipeline
- **Описание**: В `forgerace/config.py` добавить секцию `[limits]` с полями `preflight = false` (по-умолчанию) и `preflight_agent = "qwen-api"`. Реализовать валидацию имени агента (список разрешённых: `qwen-api`, `gemini`, `gpt-oss` и т.п.). В `forgerace/utils.py` добавить функцию `log_preflight(summary: str)` и использовать её в `run_preflight`.
- **Запрещено**: Не валидировать имя агента
- **Проверка**: `pytest forgerace/tests/test_preflight.py -v`
- **Критерий готовности**: Конфигурация и логирование работают корректно
- **Дискуссия**: championship-preflight
- **Агент**: @devstral+architect
- **Ветка**: — 

### TASK-113: Интеграция pre-flight в pipeline
- **Статус**: open
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: TASK-110, TASK-111, TASK-112
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/pipeline.py
- **Интеграция**: Должна вызывать `run_preflight` перед запуском coding-агента
- **Описание**: В `forgerace/pipeline.py` перед запуском coding-агента вызвать `run_preflight`. Если результат `None` (ошибка или low confidence) – выполнить обычный `_pre_check` и запустить агент без дополнительного контекста (fallback). При успешном результате – передать его в `build_prompt`. Обеспечить корректный порядок: конфиг → кэш → аналитика → fallback.
- **Запрещено**: Не обрабатывать ошибки и низкую уверенность
- **Проверка**: `pytest forgerace/tests/test_preflight.py -v`
- **Критерий готовности**: Интеграция в pipeline работает корректно
- **Дискуссия**: championship-preflight
- **Агент**: @qwen-api+theory
- **Ветка**: — 

### TASK-114: Инъекция результата pre-flight в промпт coding-агента
- **Статус**: open
- **Приоритет**: P2
- **Этап**: N
- **Зависимости**: TASK-109, TASK-110
- **Файлы (новые)**: —
- **Файлы (modify)**: forgerace/agents.py
- **Интеграция**: Должна добавлять результат в начало промпта
- **Описание**: Модифицировать `forgerace/agents.py::build_prompt` так, чтобы, если передан объект `PreflightResult`, он добавлялся в начало промпта в блоке `<preflight_research>\n{json.dumps(result, indent=2)}\n</preflight_research>`. При этом передавать только пути файлов и краткие описания (из `PROJECT_BRIEF.md`), а не полные содержимые, чтобы не превышать лимит контекста.
- **Запрещено**: Передавать полные содержимые файлов
- **Проверка**: `pytest forgerace/tests/test_preflight.py -v`
- **Критерий готовности**: Инъекция результата работает корректно
- **Дискуссия**: championship-preflight
- **Агент**: @llama+architect
- **Ветка**: — 

### TASK-115: Тесты pre-flight и интеграции
- **Статус**: open
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: TASK-109, TASK-110, TASK-111, TASK-112, TASK-113, TASK-114
- **Файлы (новые)**: forgerace/tests/test_preflight.py
- **Файлы (modify)**: —
- **Интеграция**: Должны проверять все аспекты pre-flight и интеграции
- **Описание**: Добавить модульные тесты в `forgerace/tests/test_preflight.py`: 1. Проверка соответствия ответа схеме (валидный и невалидный JSON). 2. Корректность формирования кэш-ключа и возврат из кэша. 3. Поведение pipeline при `confidence < 0.8` (fallback). 4. Инъекция в `build_prompt` (корректный блок `<preflight_research>`).
- **Запрещено**: Не проверять все аспекты
- **Проверка**: `pytest forgerace/tests/test_preflight.py -v`
- **Критерий готовности**: Тесты проходят и покрывают все аспекты
- **Дискуссия**: championship-preflight
- **Агент**: @llama+architect
- **Ветка**: — 

### TASK-116: Документация и примеры использования
- **Статус**: open
- **Приоритет**: P2
- **Этап**: N
- **Зависимости**: TASK-109, TASK-110, TASK-111, TASK-112, TASK-113, TASK-114, TASK-115
- **Файлы (новые)**: —
- **Файлы (modify)**: README.md, FORGERACE.md
- **Интеграция**: Должна описывать конфигурацию и пример команды
- **Описание**: Обновить `README.md`/`FORGERACE.md` разделом «Pre-flight analysis», описать конфигурацию, схему результата и пример команды `fr run --task TASK-001`.
- **Запрещено**: Не описывать конфигурацию
- **Проверка**: `make check`
- **Критерий готовности**: Документация обновлена и описывает все аспекты
- **Дискуссия**: championship-preflight
- **Агент**: @gemini
- **Ветка**: —
