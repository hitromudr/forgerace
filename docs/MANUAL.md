# ForgeRace — Руководство пользователя

## Что это

ForgeRace запускает несколько AI-агентов параллельно на одной задаче. Агенты соревнуются: первый, чей код прошёл кросс-ревью другими агентами, мержится. Остальные убиваются.

Полный цикл:

```
обсуждение → задачи → кодинг (агенты) → ревью (агенты) → мерж
```

## Быстрый старт

### 1. Установка

```bash
# Зависимости: Python 3.10+, git
pip install tomli  # только для Python < 3.11

# Агенты (любые из):
# CLI: claude, gemini, qwen
# Aider + LiteLLM: для бесплатных моделей через nvidia API
pip install aider-chat litellm
```

### 2. Инициализация в проекте

```bash
cd my-project
python3 /path/to/forgerace.py init
```

Создаст:
- `forgerace.toml` — конфигурация (агенты, лимиты, фреймы)
- `TASKS.md` — файл задач
- `PROJECT_BRIEF.md` — описание проекта для контекста агентов
- `./fr` — обёртка для запуска

### 3. Первый запуск

```bash
# Проверить что всё работает
./fr doctor

# Создать дискуссию
./fr discuss new auth 'Как реализовать JWT авторизацию?'

# Агенты обсуждают
./fr discuss round auth

# Закрыть дискуссию → генерация задач
./fr discuss resolve auth 'Используем PyJWT, middleware в auth.py'

# Запустить задачи
./fr run
```

---

## Конфигурация (forgerace.toml)

### Минимальный конфиг

```toml
[project]
name = "my-project"

[agents.claude]
command = "claude"
args = ["-p", "--output-format", "stream-json", "--verbose"]
review_args = ["-p", "-", "--output-format", "text"]
inactivity_timeout = 300
enabled = true

[build]
commands = [["python3", "-m", "pytest", "tests/"]]
```

### Бесплатные модели через nvidia API

```toml
# API-агенты (дискуссии + ревью)
[agents.devstral]
protocol = "openai"
base_url = "https://integrate.api.nvidia.com/v1"
api_key = "nvapi-YOUR-KEY"
model = "mistralai/devstral-2-123b-instruct-2512"
tier = "medium"
enabled = true

# Aider-агенты (кодинг) — через LiteLLM proxy
[agents.aider-devstral]
command = "aider"
args = ["--openai-api-base", "http://127.0.0.1:4000/v1",
        "--openai-api-key", "fr-local-dev",
        "--model", "openai/devstral-123b",
        "--edit-format", "diff",
        "--message-file", "/dev/stdin",
        "--yes-always", "--no-auto-commits", "--no-pretty", "--no-stream",
        "--no-show-model-warnings", "--no-gitignore",
        "--no-browser", "--no-analytics", "--no-check-update"]
protocol = "text"
prompt_stdin = true
inactivity_timeout = 300
enabled = true
[agents.aider-devstral.env]
HTTP_PROXY = ""
HTTPS_PROXY = ""
```

### Tier модели

```toml
tier = "weak"    # строгий промпт, 300 слов, 3 вопроса
tier = "medium"  # структурированный, 500 слов, анти-overengineering
tier = "strong"  # свободный формат (claude, gemini)
```

Tier влияет на промпты в дискуссиях и ревью. Weak-модели получают жёсткую структуру чтобы не растекаться.

### Build-команды

```toml
[build]
commands = [["python3", "-m", "pytest", "tests/", "-x"]]
check_command = "python3 -c 'import myproject'"
lint_fix = "ruff check --fix ."  # авто-фикс перед build
```

`commands` запускается после каждого агента. Если фейлит — агент получает ошибку и пробует исправить.

### Лимиты

```toml
[limits]
max_parallel_tasks = 4       # макс задач одновременно
agent_timeout = 300           # секунд на задачу
max_review_rounds = 2         # раундов ревью
max_task_complexity = 5       # декомпозиция при сложности 5+/5
review_frame = "evidence"     # фрейм для ревьюеров
```

---

## Дискуссии

Дискуссии — это способ обсудить архитектуру до кодинга. Агенты с разными когнитивными фреймами предлагают решения, спорят, находят консенсус.

### Создание

```bash
./fr discuss new retry-logic 'Как реализовать retry для HTTP запросов? Экспоненциальный backoff или фиксированный интервал?'
```

Создаёт `docs/discuss/retry-logic.md`.

### Пакетный режим (рекомендуемый)

```bash
# Все агенты отвечают последовательно (medium → weak)
./fr discuss round retry-logic

# Добавить своё сообщение
./fr discuss msg retry-logic 'Хорошо, но учтите что у нас rate limit 100 req/s'

# Ещё раунд
./fr discuss round retry-logic

# Закрыть и сгенерировать задачи
./fr discuss resolve retry-logic 'Экспоненциальный backoff с jitter, максимум 3 попытки'
```

### Интерактивный чат

```bash
./fr discuss chat retry-logic
```

Внутри чата:

```
> Как обработать timeout?                    # вопрос всем агентам
> /devstral Предложи структуру классов       # конкретному агенту
> /devstral+architect Оцени coupling          # агент + фрейм
> /solo devstral Напиши код retry             # чистый контекст (без дискуссии)
> /fresh devstral                             # вводные без истории
> /compact                                    # сжать контекст (при >80K символов)
> /undo                                       # откатить последнее сообщение
> /show 3                                     # показать последние 3 сообщения
> /stats                                      # статистика участников
> /summary                                    # LLM-сводка дискуссии
> /tasks                                      # сгенерировать задачи из дискуссии
> /resolve Решение такое-то                   # закрыть + задачи
> /reopen                                     # переоткрыть
> /cd /path/to/dir                            # сменить рабочую директорию агентов
> /help                                       # полная справка
```

### Когнитивные фреймы

Фрейм — это инструкция, задающая стиль мышления агента.

```bash
# Один агент, разные перспективы:
./fr discuss reply retry-logic --agent devstral+audit       # 8 осей выживаемости
./fr discuss reply retry-logic --agent devstral+adversarial # red-team: что сломается?
./fr discuss reply retry-logic --agent devstral+price       # trade-off анализ
```

Встроенные фреймы:

| Фрейм | Назначение |
|--------|------------|
| `reviewer` | Строгий код-ревьюер |
| `audit` | 8 осей выживаемости решения |
| `wild` | Дикие стратегии, инверсия |
| `price` | Trade-off матрицы, стоимость выбора |
| `theory` | От принципов к реализации |
| `evidence` | От фактов к обобщению |
| `adversarial` | Red-team: баги, race conditions |
| `architect` | Coupling, cohesion, data flow |
| `user` | UX: что путает, бесит |

Свои фреймы — в `forgerace.toml`:

```toml
[frames.security]
description = "OWASP Top 10 review"
content = "Review code for OWASP Top 10 vulnerabilities..."

[frames.ddd]
description = "Domain-Driven Design analysis"
file = "frames/ddd.md"
```

---

## Задачи (TASKS.md)

### Формат

```markdown
### TASK-001: Реализовать JWT middleware
- **Статус**: open
- **Приоритет**: P1
- **Зависимости**: TASK-002
- **Файлы (новые)**: auth/middleware.py
- **Файлы (modify)**: app/main.py
- **Описание**: Создать middleware для проверки JWT токенов...
- **Дискуссия**: auth
```

Статусы: `open` → `done` / `blocked` / `skip`

Задачи генерируются автоматически из `discuss resolve` или `discuss regen`. Можно создавать вручную:

### CLI управление

```bash
# Список
./fr task list

# Создать
./fr task add 'Добавить rate limiting' --priority P1 --description 'Ограничить API до 100 req/s'

# Редактировать
./fr task edit TASK-042 --status blocked
./fr task edit TASK-042 --priority P0

# Архивировать выполненные
./fr task archive
# → done/TASKS_2026-04-23.md
```

---

## Запуск задач

### Competitive mode (по умолчанию)

Все включённые coding-агенты получают одну задачу. Первый прошедший ревью мержится.

```bash
# Все ready-задачи
./fr run

# Конкретная задача
./fr run --task TASK-042

# Dry run — показать что запустится
./fr run --dry-run
```

### Что происходит внутри

1. **Pre-check**: валидация задачи, проверка зависимостей
2. **Worktree**: для каждого агента создаётся отдельный git worktree
3. **Prompt**: агенту отправляется промпт с описанием задачи, контекстом проекта, списком файлов
4. **Coding**: агент пишет код в своём worktree
5. **Build**: запуск `build.commands` (тесты, линтер)
6. **Review**: другие агенты делают кросс-ревью (majority vote)
7. **Merge**: если APPROVED — мерж в develop через detached worktree
8. **Cleanup**: worktrees удаляются (failed сохраняются для отладки)

### Перезапуск

```bash
# Анализ причины фейла + смена агента + повторный запуск
./fr retry TASK-042

# Все blocked задачи
./fr retry --all
```

Retry анализирует лог: timeout, build error, merge conflict, quota — и выбирает другого агента.

### Мониторинг

```bash
# Статус задач
./fr status

# Live dashboard
./fr monitor

# Логи
./fr logs                        # список
./fr logs show TASK-042          # последние 50 строк
./fr logs show TASK-042 --tail 200
./fr logs follow TASK-042        # live tail
```

---

## Feature branches

Для изоляции работы по фичам:

```bash
./fr feature list                # все feature branches
./fr feature score               # скорборд по командам
./fr feature merge my-feature    # мерж в develop
```

---

## Обслуживание

### Doctor

```bash
./fr doctor
```

Проверяет и автоматически чинит:
- TOML валидность
- PROJECT_BRIEF.md наличие
- Директории (logs/, docs/discuss/)
- CLI-агенты в PATH
- LiteLLM proxy доступность
- Повисшие worktrees

### Модели

```bash
./fr models          # список доступных моделей
./fr models --test   # проверить каждую (отправить тестовый запрос)
```

### Агенты (вкл/выкл на лету)

```bash
./fr agents                  # список и статус
./fr agents off aider-llama  # отключить
./fr agents on aider-llama   # включить
```

---

## Типичные сценарии

### Сценарий 1: Новая фича от нуля

```bash
# 1. Проверка среды
./fr doctor

# 2. Дискуссия
./fr discuss new caching 'Нужен кеш для API ответов. Redis или in-memory?'
./fr discuss round caching
./fr discuss msg caching 'In-memory, без внешних зависимостей'
./fr discuss round caching
./fr discuss resolve caching 'TTL-кеш на dict + threading.Lock, модуль cache.py'

# 3. Проверить сгенерированные задачи
./fr task list

# 4. Запуск
./fr run

# 5. Мониторинг
./fr monitor        # в другом терминале
./fr logs follow    # или live tail

# 6. Результат
./fr status
```

### Сценарий 2: Баг-фикс

```bash
# Быстрая задача без дискуссии
./fr task add 'Исправить race condition в cache.py' \
  --priority P0 \
  --description 'Lock не захватывается при TTL cleanup. Добавить with self._lock в _cleanup()' \
  --files-modify cache.py

./fr run --task TASK-043
```

### Сценарий 3: Перезапуск после фейла

```bash
# Посмотреть что упало
./fr status

# Посмотреть лог
./fr logs show TASK-043

# Перезапустить с анализом
./fr retry TASK-043
# → "Причина: BUILD_ERROR. Выбран агент: aider-devstral (вместо aider-llama)"
```

### Сценарий 4: Глубокий анализ перед реализацией

```bash
./fr discuss new refactor-db 'Стоит ли мигрировать с SQLite на PostgreSQL?'

# Разные перспективы:
./fr discuss reply refactor-db --agent devstral+architect   # структура
./fr discuss reply refactor-db --agent gpt-oss+price        # стоимость
./fr discuss reply refactor-db --agent devstral+adversarial # что сломается

# Своё мнение
./fr discuss msg refactor-db 'Пока SQLite, но абстрагировать через Repository pattern'

# Финальный раунд
./fr discuss round refactor-db

./fr discuss resolve refactor-db 'Repository pattern, SQLite сейчас, миграция позже'
```

---

## Настройка LiteLLM proxy

Для aider/goose агентов с бесплатными nvidia моделями:

```bash
pip install litellm

# Конфиг litellm (litellm_config.yaml):
cat > litellm_config.yaml << 'EOF'
model_list:
  - model_name: devstral-123b
    litellm_params:
      model: nvidia_nim/mistralai/devstral-2-123b-instruct-2512
      api_key: nvapi-YOUR-KEY

  - model_name: llama-70b
    litellm_params:
      model: nvidia_nim/meta/llama-3.3-70b-instruct
      api_key: nvapi-YOUR-KEY

  - model_name: qwen-122b
    litellm_params:
      model: nvidia_nim/qwen/qwen2.5-coder-32b-instruct
      api_key: nvapi-YOUR-KEY
EOF

# Запуск
litellm --config litellm_config.yaml --port 4000
```

Nvidia API ключ: https://build.nvidia.com (бесплатный tier — 1000 запросов/день).

---

## Справочник команд

| Команда | Описание |
|---------|----------|
| `./fr init` | Инициализация проекта |
| `./fr doctor` | Диагностика + автолечение |
| `./fr discuss new <тема> '<вопрос>'` | Создать дискуссию |
| `./fr discuss chat <тема>` | Интерактивный чат |
| `./fr discuss round <тема>` | Все агенты отвечают |
| `./fr discuss msg <тема> '<текст>'` | Сообщение от techlead |
| `./fr discuss resolve <тема> '<решение>'` | Закрыть + задачи |
| `./fr discuss regen <тема>` | Перегенерировать задачи |
| `./fr discuss list` | Список дискуссий |
| `./fr discuss show <тема>` | Показать дискуссию |
| `./fr task list` | Список задач |
| `./fr task add '<название>'` | Создать задачу |
| `./fr task edit TASK-N --field value` | Редактировать задачу |
| `./fr task archive` | Архивировать done/skip |
| `./fr run` | Запустить задачи |
| `./fr run --task TASK-N` | Конкретная задача |
| `./fr run --dry-run` | Показать план без запуска |
| `./fr retry TASK-N` | Перезапуск с анализом |
| `./fr retry --all` | Перезапуск всех blocked |
| `./fr status` | Статус + граф зависимостей |
| `./fr monitor` | Live dashboard |
| `./fr logs` | Список логов |
| `./fr logs show TASK-N` | Показать лог |
| `./fr logs follow` | Live tail |
| `./fr agents` | Список агентов |
| `./fr agents on/off <name>` | Вкл/выкл агента |
| `./fr models --test` | Проверить модели |
| `./fr merge-pending` | Мерж одобренных задач |
| `./fr feature list` | Feature branches |
| `./fr feature score` | Скорборд команд |
