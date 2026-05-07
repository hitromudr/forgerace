TASKS — forgerace

### TASK-038: Интеграция Distributed Mode в pipeline.py
- **Статус**: done
- **Описание**:
  1. Модифицировать функцию `execute_task_competitive()` в `forgerace/pipeline.py` для поддержки distributed mode:
     - Добавить проверку `config.mode == "distributed"`.
     - Если `task.agent` не задан, реализовать round-robin распределение между агентами из `config.available_agents`.
     - Использовать существующий логгер для записи назначения агента (например, `logger.info(f"Assigned {agent} to task {task.id} (round-robin)")`).
  2. Добавить потокобезопасный счётчик `round_robin_index` с использованием `threading.Lock`.
  3. Обеспечить обратную совместимость: если `config.mode` не указан или равен `"competitive"`, использовать существующую логику.
- **Файлы (modify)**: forgerace/pipeline.py
- **Зависимости**: TASK-039
- **Проверка**:
  ```bash
  python -m pytest tests/test_pipeline.py::test_distributed_mode -v
  ```

- **Дискуссия**: pilot-distributed
### TASK-039: Валидация конфигурации для Distributed Mode
- **Статус**: done
- **Описание**:
  1. Добавить в `forgerace/config.py` класс `Config` с методами:
     - `__init__(path: Path)` — загрузка и парсинг `forgerace.toml`.
     - `_validate()` — проверка корректности `mode` (разрешенные значения: `"competitive"`, `"distributed"`) и наличия `available_agents` для distributed mode.
  2. Поднять `ValueError` с понятным сообщением при неверных значениях.
  3. Обеспечить совместимость: если `mode` не указан, использовать `"competitive"` по умолчанию.
- **Файлы (modify)**: forgerace/config.py
- **Проверка**:
  ```bash
  python -m pytest tests/test_config.py::test_validate_distributed_mode -v
  ```

- **Дискуссия**: pilot-distributed
### TASK-040: Тесты для Distributed Mode
- **Статус**: done
- **Описание**:
  1. Создать тесты в `tests/test_pipeline.py`:
     - `test_distributed_mode_explicit_agent` — проверка явного назначения агента.
     - `test_distributed_mode_round_robin` — проверка round-robin распределения.
     - `test_competitive_mode_unchanged` — проверка, что competitive mode работает как раньше.
  2. Добавить тесты в `tests/test_config.py`:
     - `test_validate_distributed_mode` — проверка валидации конфигурации.
     - `test_validate_competitive_mode` — проверка дефолтного режима.
- **Файлы (новые)**: tests/test_pipeline.py, tests/test_config.py
- **Проверка**:
  ```bash
  python -m pytest tests/test_pipeline.py tests/test_config.py -v
  ```
```
- **Дискуссия**: pilot-distributed

### TASK-041: Утилита format_duration + тесты
- **Статус**: blocked
- **Описание**:
  1. Добавить в `forgerace/utils.py` функцию `format_duration(seconds: float) -> str`,
     возвращающую человекочитаемую длительность:
     - `< 60` секунд → `"42s"` (целые секунды).
     - `< 3600` → `"5m 12s"` (минуты + секунды).
     - `>= 3600` → `"1h 23m"` (часы + минуты, без висящих нулей: 3600 → "1h", 3660 → "1h 1m").
     - Отрицательное значение → `ValueError`.
     - Дробные секунды округлять вниз.
  2. Расширить `tests/test_utils.py` блоком тестов на все ветки выше,
     включая edge cases: 0 секунд, ровно 60, ровно 3600, очень большие
     значения (24h+), отрицательные → ValueError.
- **Файлы (modify)**: forgerace/utils.py, tests/test_utils.py
- **Зависимости**: —
- **Проверка**:
  ```bash
  python3 -m pytest tests/test_utils.py -v
  ```
