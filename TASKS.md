 TASKS — forgerace

### TASK-001: Реализация команды `stats`
- **Статус**: blocked
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: —
- **Файлы (новые)**: forgerace/cli.py
- **Файлы (modify)**: —
- **Интеграция**: добавить вызов `_cmd_stats()` в `main()`
- **Описание**: реализовать функцию `_cmd_stats()` для вывода статистики задач
- **Запрещено**: —
- **Проверка**: make check
- **Критерий готовности**: команда `./fr stats` выводит статистику задач
- **Дискуссия**: feature-stats
- **Агент**: aider-devstral
- **Ветка**: task/task-001-realizatsiya-komandy-stats-aider-devstral

### TASK-003: Реализация Checkpoints
- **Статус**: done
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: —
- **Файлы (новые)**: src/checkpoint.py
- **Файлы (modify)**: pipeline.py
- **Интеграция**: добавить вызов `checkpoint.snapshot()` после каждой задачи и реализовать `./fr rollback {task_id}`
- **Описание**: реализовать снимки состояния после каждой задачи и возможность восстановления из последнего снимка
- **Запрещено**: не использовать git-независимое хранение снимков
- **Проверка**: `python -m unittest test_checkpoint.py`
- **Критерий готовности**: успешный запуск `./fr rollback {task_id}` без ошибок
- **Дискуссия**: stability-v1
- **Агент**: pre-check
- **Ветка**: task/task-003-realizatsiya-checkpoints-aider-devstral

### TASK-004: Реализация LiteLLM Watchdog
- **Статус**: done
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: —
- **Файлы (новые)**: src/health_monitor.py
- **Файлы (modify)**: agent.py
- **Интеграция**: добавить поток `health_monitor` в `agent.py` и вызов `LiteLLM.health_check()` каждые 30с
- **Описание**: реализовать мониторинг LiteLLM и перезапуск при неудаче
- **Запрещено**: не использовать конфигурируемый таймаут
- **Проверка**: `python -m unittest test_health_monitor.py`
- **Критерий готовности**: успешный запуск `agent.py` с watchdog без ошибок
- **Дискуссия**: stability-v1
- **Агент**: pre-check
- **Ветка**: task/task-004-realizatsiya-litellm-watchdog-aider-gptoss

### TASK-005: Реализация Stale Task Cleaner
- **Статус**: open
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: —
- **Файлы (новые)**: src/stale_task_cleaner.py
- **Файлы (modify)**: pipeline.py
- **Интеграция**: добавить вызов `clean_stale_tasks()` в `pipeline.py`
- **Описание**: реализовать очистку зависших задач
- **Запрещено**: не использовать транзакции для обеспечения консистентности данных
- **Проверка**: `python -m unittest test_stale_task_cleaner.py`
- **Критерий готовности**: успешный запуск `clean_stale_tasks()` без ошибок
- **Дискуссия**: stability-v1
- **Агент**: aider-devstral
- **Ветка**: task/task-005-realizatsiya-stale-task-cleaner-aider-devstral

### TASK-006: Реализация Flat Dependencies
- **Статус**: done
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: —
- **Файлы (новые)**: src/flat_dependencies.py
- **Файлы (modify)**: graph.py
- **Интеграция**: добавить вызов `flatten_deps()` в `graph.py`
- **Описание**: реализовать автоматическое выравнивание цепочек зависимостей
- **Запрещено**: не использовать ограничение глубины цепочек зависимостей
- **Проверка**: `python -m unittest test_flat_dependencies.py`
- **Критерий готовности**: успешный запуск `fr generate --flatten` без ошибок
- **Дискуссия**: stability-v1
- **Агент**: pre-check
- **Ветка**: task/task-006-realizatsiya-flat-dependencies-aider-devstral

### TASK-008: Реализовать WebServer с SSE
- **Статус**: in_progress:aider-devstral
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: - **Файлы (новые)**: src/web_server.py
- **Файлы (modify)**: —
- **Интеграция**: импортировать DiagnoseEngine и использовать для отправки событий через SSE
- **Описание**: Создать WebServer, который использует SSE для потоковой передачи событий
- **Запрещено**: использовать фреймворки, не реализовывать обработку ошибок
- **Проверка**: curl -N http://localhost:8080/events
- **Критерий готовности**: WebServer должен корректно отправлять события через SSE
- **Дискуссия**: observability-v1
- **Агент**: aider-devstral
- **Ветка**: task/task-008-realizovat-webserver-s-sse-aider-devstral

### TASK-009: Реализовать DashboardRenderer
- **Статус**: done
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: - **Файлы (новые)**: src/dashboard_renderer.py
- **Файлы (modify)**: —
- **Интеграция**: импортировать DiagnoseEngine и использовать для формирования HTML/JSON для эндпоинтов
- **Описание**: Создать DashboardRenderer, который формирует HTML/JSON для эндпоинтов
- **Запрещено**: использовать шаблонизаторы, не реализовывать минимализм
- **Проверка**: python -m unittest test_dashboard_renderer.py
- **Критерий готовности**: DashboardRenderer должен корректно формировать HTML/JSON для эндпоинтов
- **Дискуссия**: observability-v1
- **Агент**: pre-check
- **Ветка**: task/task-009-realizovat-dashboardrenderer-aider-devstral

### TASK-010: Реализовать Notifier
- **Статус**: done
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: - **Файлы (новые)**: src/notifier.py
- **Файлы (modify)**: —
- **Интеграция**: импортировать DiagnoseEngine и использовать для отправки уведомлений
- **Описание**: Создать Notifier, который отправляет уведомления через webhook/telegram
- **Запрещено**: использовать внешние зависимости, не реализовывать обработку ошибок
- **Проверка**: python -m unittest test_notifier.py
- **Критерий готовности**: Notifier должен корректно отправлять уведомления
- **Дискуссия**: observability-v1
- **Агент**: pre-check
- **Ветка**: task/task-010-realizovat-notifier-aider-devstral

### TASK-011: Реализовать синхронизацию доступа к состоянию
- **Статус**: open
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: - **Файлы (новые)**: —
- **Файлы (modify)**: src/diagnose_engine.py
- **Интеграция**: добавить lock для синхронизации доступа к состоянию
- **Описание**: Реализовать синхронизацию доступа к состоянию для предотвращения race conditions
- **Запрещено**: не использовать lock, не реализовывать атомарный доступ
- **Проверка**: python -m unittest test_diagnose_engine.py
- **Критерий готовности**: DiagnoseEngine должен корректно собирать снимок состояния системы без race conditions
- **Дискуссия**: observability-v1
- **Агент**: aider-devstral
- **Ветка**: task/task-011-realizovat-sinhronizatsiyu-dostupa-k-sos-aider-devstral

### TASK-012: Реализовать persistence timeline
- **Статус**: in_progress:aider-devstral
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: - **Файлы (новые)**: src/timeline.py
- **Файлы (modify)**: —
- **Интеграция**: импортировать DiagnoseEngine и использовать для сохранения событий в timeline
- **Описание**: Создать persistence timeline для сохранения событий
- **Запрещено**: не реализовывать сохранение событий, не использовать SQLite
- **Проверка**: python -m unittest test_timeline.py
- **Критерий готовности**: Timeline должен корректно сохранять события
- **Дискуссия**: observability-v1
- **Агент**: aider-devstral
- **Ветка**: task/task-012-realizovat-persistence-timeline-aider-devstral

### TASK-014: Реализация параллельного выполнения задач
- **Статус**: blocked
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: - **Файлы (новые)**: src/benchmark/core/runner.py
- **Файлы (modify)**: src/benchmark/core.py
- **Интеграция**: использование параллельного выполнения в бенчмарке
- **Описание**: Добавить параллельное выполнение задач в бенчмарке
- **Запрещено**: не использовать механизмы синхронизации
- **Проверка**: make check
- **Критерий готовности**: успешный запуск бенчмарка с параллельным выполнением
- **Дискуссия**: benchmark-public
- **Агент**: aider-qwen
- **Ветка**: task/task-014-realizatsiya-parallelnogo-vypolneniya-za-aider-qwen
### TASK-015: Реализация отчётности и визуализации
- **Статус**: open
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: - **Файлы (новые)**: src/benchmark/reporting/reporter.py
- **Файлы (modify)**: src/benchmark/core.py
- **Интеграция**: использование отчётности и визуализации в бенчмарке
- **Описание**: Добавить отчётность и визуализацию результатов в бенчмарке
- **Запрещено**: не использовать matplotlib или ASCII art
- **Проверка**: make check
- **Критерий готовности**: успешный запуск бенчмарка с отчётностью и визуализацией
- **Дискуссия**: benchmark-public
- **Агент**: aider-devstral
- **Ветка**: task/task-015-realizatsiya-otchyotnosti-i-vizualizatsi-aider-devstral
### TASK-016: Реализация обработки ошибок и восстановления
- **Статус**: open
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: - **Файлы (новые)**: src/benchmark/core/error_handler.py
- **Файлы (modify)**: src/benchmark/core.py
- **Интеграция**: использование обработки ошибок и восстановления в бенчмарке
- **Описание**: Добавить обработку ошибок и восстановление в бенчмарке
- **Запрещено**: не использовать механизмы восстановления после ошибок
- **Проверка**: make check
- **Критерий готовности**: успешный запуск бенчмарка с обработкой ошибок и восстановлением
- **Дискуссия**: benchmark-public
- **Агент**: aider-devstral
- **Ветка**: task/task-016-realizatsiya-obrabotki-oshibok-i-vosstan-aider-devstral
### TASK-017: Реализация валидации формата задач
- **Статус**: blocked
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: - **Файлы (новые)**: src/benchmark/tasks/validator.py
- **Файлы (modify)**: src/benchmark/tasks.py
- **Интеграция**: использование валидации формата задач в бенчмарке
- **Описание**: Добавить валидацию формата задач в бенчмарке
- **Запрещено**: не использовать Pydantic для валидации
- **Проверка**: make check
- **Критерий готовности**: успешный запуск бенчмарка с валидацией формата задач
- **Дискуссия**: benchmark-public
- **Агент**: aider-qwen
- **Ветка**: task/task-017-realizatsiya-validatsii-formata-zadach-aider-qwen
### TASK-018: Реализация тестирования ошибочных путей
- **Статус**: done
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: - **Файлы (новые)**: src/benchmark/core/test_error_paths.py
- **Файлы (modify)**: src/benchmark/core.py
- **Интеграция**: использование тестирования ошибочных путей в бенчмарке
- **Описание**: Добавить тестирование ошибочных путей в бенчмарке
- **Запрещено**: не использовать unit-тесты
- **Проверка**: make check
- **Критерий готовности**: успешный запуск бенчмарка с тестированием ошибочных путей
- **Дискуссия**: benchmark-public
- **Агент**: pre-check
- **Ветка**: task/task-018-realizatsiya-testirovaniya-oshibochnyh-p-aider-gptoss
