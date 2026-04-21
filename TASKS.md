 TASKS — forgerace

### TASK-001: Реализация команды `stats`
- **Статус**: open
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
- **Статус**: done
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
- **Агент**: pre-check
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
- **Статус**: done
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
- **Агент**: pre-check
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
- **Статус**: skip
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
- **Статус**: skip
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
- **Статус**: skip
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
- **Статус**: done
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
- **Агент**: pre-check
- **Ветка**: task/task-015-realizatsiya-otchyotnosti-i-vizualizatsi-aider-devstral
### TASK-016: Реализация обработки ошибок и восстановления
- **Статус**: done
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
- **Агент**: pre-check
- **Ветка**: task/task-016-realizatsiya-obrabotki-oshibok-i-vosstan-aider-devstral
### TASK-017: Реализация валидации формата задач
- **Статус**: skip
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

### TASK-020: Реализовать PatchProcessor для применения diff патчей
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: —
- **Файлы (новые)**: src/forgerace/tools/patch_processor.py
- **Файлы (modify)**: —
- **Интеграция**: импортировать PatchProcessor в APIAdapter
- **Описание**: Реализовать класс PatchProcessor, который будет принимать diff патчи и применять их к рабочим деревьям с использованием git apply.
- **Запрещено**: не использовать git apply для проверки патчей
- **Проверка**: pytest tests/test_patch_processor.py -v
- **Критерий готовности**: PatchProcessor корректно применяет diff патчи и возвращает результаты в виде структурированных объектов
- **Дискуссия**: api-agents-coding
- **Агент**: aider-qwen
- **Ветка**: task/task-020-realizovat-patchprocessor-dlya-primeneni-aider-qwen


### TASK-022: Обновить APIAdapter для использования ToolExecutor и PatchProcessor
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: TASK-020
- **Файлы (новые)**: —
- **Файлы (modify)**: src/forgerace/api_adapter.py
- **Интеграция**: использовать ToolExecutor и PatchProcessor в зависимости от поддержки function_calling модели
- **Описание**: Обновить APIAdapter для использования ToolExecutor и PatchProcessor в зависимости от поддержки function_calling модели.
- **Запрещено**: —
- **Проверка**: pytest tests/test_api_adapter.py -v
- **Критерий готовности**: APIAdapter корректно использует ToolExecutor и PatchProcessor в зависимости от поддержки function_calling модели
- **Дискуссия**: api-agents-coding
- **Агент**: —
- **Ветка**: —


### TASK-024: Реализовать интеграцию ToolExecutor с sandbox
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: —
- **Файлы (новые)**: src/forgerace/tools/sandbox_service.py
- **Файлы (modify)**: src/forgerace/tools/tool_executor.py
- **Интеграция**: использовать SandboxService в ToolExecutor для exec_bash
- **Описание**: Реализовать интеграцию ToolExecutor с sandbox, используя nsjail или docker для ограничения доступа.
- **Запрещено**: использовать произвольные bash команды
- **Проверка**: pytest tests/test_sandbox_service.py -v
- **Критерий готовности**: ToolExecutor корректно использует sandbox для exec_bash и ограничивает доступ
- **Дискуссия**: api-agents-coding
- **Агент**: aider-devstral
- **Ветка**: task/task-024-realizovat-integratsiyu-toolexecutor-s-s-aider-devstral

### TASK-025: Реализовать флаг fallback в APIAdapter
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 2
- **Зависимости**: TASK-022
- **Файлы (новые)**: —
- **Файлы (modify)**: src/forgerace/api_adapter.py
- **Интеграция**: добавить флаг fallback в APIAdapter для переключения на diff режим
- **Описание**: Реализовать флаг fallback в APIAdapter для переключения на diff режим в случае ошибок function_call.
- **Запрещено**: —
- **Проверка**: pytest tests/test_api_adapter_fallback.py -v
- **Критерий готовности**: APIAdapter корректно переключается на diff режим в случае ошибок function_call
- **Дискуссия**: api-agents-coding
- **Агент**: —
- **Ветка**: —

### TASK-027: Реализовать Ensemble Review с голосованием
- **Статус**: open
- **Приоритет**: P1
- **Этап**: 1
- **Зависимости**: —
- **Файлы (новые)**: —
- **Файлы (modify)**: review.py
- **Интеграция**: добавить функцию `run_ensemble_review(task)` в `review.py`
- **Описание**: Расширить `review.py` для параллельного запуска нескольких API-ревьюеров. Реализовать механизм голосования для принятия вердикта на основе `consensus_score`.
- **Запрещено**: —
- **Проверка**: `pytest tests/test_review.py -v`
- **Критерий готовности**: Вердикт принимается на основе голосования нескольких API-ревьюеров.
- **Дискуссия**: api-models-role
- **Агент**: —
- **Ветка**: —


### TASK-030: Добавить валидацию комментариев и логирование в `review.py`
- **Статус**: open
- **Приоритет**: P2
- **Этап**: 2
- **Зависимости**: TASK-027
- **Файлы (новые)**: —
- **Файлы (modify)**: review.py
- **Интеграция**: добавить валидацию `COMMENTS` и логирование в `run_ensemble_review(task)`
- **Описание**: Добавить валидацию комментариев в `run_ensemble_review(task)` для проверки наличия ключевых слов и релевантности. Добавить логирование для отладки голосования.
- **Запрещено**: —
- **Проверка**: `pytest tests/test_review.py -v`
- **Критерий готовности**: Комментарии валидируются и логируются детали голосования.
- **Дискуссия**: api-models-role
- **Агент**: —
- **Ветка**: —

### TASK-031: Добавить unit-тесты для Ensemble Review
- **Статус**: open
- **Приоритет**: P2
- **Этап**: 2
- **Зависимости**: TASK-027
- **Файлы (новые)**: tests/test_ensemble_review.py
- **Файлы (modify)**: —
- **Интеграция**: —
- **Описание**: Реализовать unit-тесты для проверки расчёта `consensus_score` и отбраковки коротких комментариев.
- **Запрещено**: —
- **Проверка**: `pytest tests/test_ensemble_review.py -v`
- **Критерий готовности**: Тесты покрывают основные сценарии голосования.
- **Дискуссия**: api-models-role
- **Агент**: —
- **Ветка**: —

### TASK-032: Реализация команды ./fr doctor
- **Статус**: done
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: —
- **Файлы (новые)**: forgerace/doctor.py
- **Файлы (modify)**: forgerace/pipeline.py, forgerace/cli.py
- **Интеграция**: добавить вызов `doctor()` в `cli.py` для команды `doctor`
- **Описание**: реализовать команду `./fr doctor` для диагностики и автолечения среды, включая проверку валидности TOML-файла, наличия и корректности `PROJECT_BRIEF.md`, наличия директорий `logs/` и `docs/discuss/`, проверку наличия файла, указанного в `discuss_context`, проверку агентов, LiteLLM и очистку зависших worktrees
- **Запрещено**: использовать внешние зависимости, менять сигнатуры существующих функций
- **Проверка**: `python -m forgerace.doctor`
- **Критерий готовности**: команда `./fr doctor` должна успешно запускаться и производить диагностику среды
- **Дискуссия**: doctor-cmd
- **Агент**: aider-devstral
- **Ветка**: task/task-032-realizatsiya-komandy-fr-doctor-aider-devstral
