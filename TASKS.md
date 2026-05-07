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
- **Статус**: open
- **Описание**:
  Добавить в `forgerace/utils.py` функцию `format_duration(seconds: float) -> str`,
  которая возвращает человекочитаемую длительность с **латинскими** суффиксами
  s / m / h. Дробные секунды округляются вниз (int(seconds)).

  **Точная семантика — assert ровно эти строки:**

  ```python
  format_duration(0)        == "0s"
  format_duration(1)        == "1s"
  format_duration(42)       == "42s"
  format_duration(59)       == "59s"
  format_duration(60)       == "1m 0s"
  format_duration(60.5)     == "1m 0s"      # 60.5 → int → 60
  format_duration(125)      == "2m 5s"
  format_duration(3599)     == "59m 59s"
  format_duration(3600)     == "1h 0m"      # без висящих нулей секунд в часах
  format_duration(3660)     == "1h 1m"
  format_duration(7200)     == "2h 0m"
  format_duration(86400)    == "24h 0m"     # 24h+ — те же часы и минуты
  format_duration(-1)       # → raises ValueError
  ```

  В файл `tests/test_utils.py` добавить ОДНУ функцию `test_format_duration`,
  которая проверяет **ровно** перечисленные выше пары. Не выдумывай дополнительных
  ассертов с другой логикой — они должны проходить именно так.

  **Реализацию (utils.py) и тесты (test_utils.py) добавляй в одном edit-batch.**
  Если функции ещё нет, а тесты её импортируют — pytest упадёт на ImportError.

- **Файлы (modify)**: forgerace/utils.py, tests/test_utils.py
- **Зависимости**: —
- **Проверка**:
  ```bash
  python3 -m pytest tests/test_utils.py::test_format_duration -v
  ```

### TASK-043: Утилита truncate_string + тесты
- **Статус**: done
- **Приоритет**: P2
- **Описание**:
  Добавить в `forgerace/utils.py` функцию
  `truncate_string(s: str, max_len: int) -> str`, которая укорачивает
  строку до `max_len` символов, добавляя суффикс `"..."` если строка
  была урезана. Длина результата НИКОГДА не превышает `max_len`.

  **Точная семантика — assert ровно эти строки:**

  ```python
  truncate_string("", 10)               == ""
  truncate_string("hello", 10)          == "hello"
  truncate_string("hello", 5)           == "hello"
  truncate_string("hello world", 8)     == "hello..."   # 5 + 3 = 8
  truncate_string("hello world", 5)     == "he..."      # 2 + 3 = 5
  truncate_string("hello world", 3)     == "..."        # только троеточие
  truncate_string("hello", 2)           == ".."         # < 3 → обрезается само троеточие
  truncate_string("hello", 0)           == ""           # 0 — пусто
  truncate_string("hello", -1)          # → raises ValueError
  ```

  Правила:
  - если `len(s) <= max_len` — возвращай `s` без изменений
  - иначе результат = `s[:max_len-3] + "..."`
  - при `max_len < 3` — возвращай `"..."[:max_len]` (без префикса)
  - при `max_len < 0` — `ValueError`

  В файл `tests/test_utils.py` добавить ОДНУ функцию `test_truncate_string`,
  которая проверяет **ровно** перечисленные выше пары. Не выдумывай
  дополнительных ассертов.

  **Реализацию (utils.py) и тесты (test_utils.py) добавляй в одном edit-batch.**

- **Файлы (modify)**: forgerace/utils.py, tests/test_utils.py
- **Зависимости**: —
- **Проверка**:
  ```bash
  python3 -m pytest tests/test_utils.py::test_truncate_string -v
  ```
