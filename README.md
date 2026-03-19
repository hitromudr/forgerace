# ForgeRace

Мультиагентный оркестратор разработки. Запускает несколько AI-агентов (Claude, Gemini и др.) параллельно на задачах кодирования, с кросс-ревью, авто-декомпозицией и race-to-merge.

> [English version](README.en.md)

## Как это работает

1. Описываешь задачи в `TASKS.md`
2. ForgeRace запускает несколько агентов на каждую задачу параллельно
3. Агенты работают в изолированных git worktree
4. Кросс-ревью: каждый агент проверяет код другого (не свой)
5. Первый прошедший ревью мержится — race-модель

Сложные задачи автоматически декомпозируются на подзадачи. Зацикливание ревью детектируется и эскалируется. Вся активность агентов стримится в терминал с MUD-раскраской — у каждого агента свой цвет, tool calls (Read, Write, Bash и т.д.) видны в реальном времени.

## Требования

- Python 3.10+
- Git
- Хотя бы один агентский CLI, установленный и авторизованный:
  - [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude` CLI)
  - [Gemini CLI](https://github.com/google-gemini/gemini-cli) (`gemini` CLI)

**Важно:** ForgeRace использует официальные CLI агентов (не API-ключи). Агенты авторизуются через браузер — запусти `claude` или `gemini` один раз для логина, дальше ForgeRace переиспользует сессию. Работает на твоей существующей подписке Claude/Gemini, отдельного API-биллинга нет.

## Быстрый старт

```bash
# 1. Клонируй ForgeRace
git clone https://github.com/hitromudr/forgerace.git ~/forgerace

# 2. Перейди в свой проект и инициализируй
cd ~/work/my-project
python3 ~/forgerace/forgerace.py init
# Создаст forgerace.toml и TASKS.md

# 3. Настрой конфиг и добавь задачи
vim forgerace.toml    # build-команды, dev_branch и т.д.
vim TASKS.md          # опиши задачи (примеры в examples/)

# 4. Запусти
python3 ~/forgerace/forgerace.py run

# Конфиг запоминается — дальше без --config:
python3 ~/forgerace/forgerace.py status
python3 ~/forgerace/forgerace.py run
```

Опционально — установить как пакет для короткой команды:
```bash
pip install -e ~/forgerace
forgerace run
```

### Система дискуссий

Перед реализацией можно провести архитектурное обсуждение между агентами:

```bash
python3 ~/forgerace/forgerace.py discuss new auth-design 'Как реализовать авторизацию?'
python3 ~/forgerace/forgerace.py discuss chat auth-design    # агенты обсуждают
python3 ~/forgerace/forgerace.py discuss show auth-design    # посмотреть дискуссию
python3 ~/forgerace/forgerace.py run                         # задачи из дискуссии получают контекст
```

## Возможности

- **Конкурентный режим** — несколько агентов решают одну задачу, лучший побеждает
- **Race-to-merge** — первый прошедший ревью мержится немедленно
- **Кросс-ревью** — агенты проверяют код друг друга, не свой
- **Авто-декомпозиция** — сложные задачи разбиваются на подзадачи через LLM-оценку
- **Детекция зацикливания** — если ревьюер повторяет одно замечание → эскалация
- **Система дискуссий** — структурированные архитектурные обсуждения до реализации
- **Стриминг** — цветной MUD-вывод в терминале с tool calls агентов в реальном времени
- **Изоляция через worktree** — каждый агент в своём git worktree, без конфликтов
- **Граф зависимостей** — `status` показывает дерево задач и бутылочные горлышки

## Формат задач (TASKS.md)

```markdown
### TASK-001: Название фичи
- **Статус**: open
- **Приоритет**: P1
- **Зависимости**: TASK-000 или —
- **Файлы (новые)**: src/path/file.py
- **Файлы (modify)**: — или path
- **Описание**: что реализовать
- **Критерий готовности**: что должно работать
- **Дискуссия**: имя-топика или —
- **Агент**: —
- **Ветка**: —
```

Полные примеры в `examples/TASKS.md`.

## Конфигурация (forgerace.toml)

```toml
[project]
name = "my-project"
context = "краткое описание проекта для контекста агентов"
root = "."
dev_branch = "develop"

[agents.claude]
command = "claude"
args = ["-p", "--allowedTools", "Read,Write,Edit,Bash,Grep,Glob,WebFetch,WebSearch",
        "--max-turns", "50", "--output-format", "stream-json", "--verbose"]
review_args = ["-p", "-", "--output-format", "text", "--permission-mode", "auto"]
inactivity_timeout = 300

[agents.gemini]
command = "gemini"
args = ["-p", "--approval-mode", "yolo", "--output-format", "stream-json"]
review_args = ["-p", "-"]
inactivity_timeout = 180

[build]
commands = [["make", "build"], ["make", "test"]]
check_command = "make check"

[limits]
max_parallel_tasks = 4
agent_timeout = 900
max_review_rounds = 3
```

Полный пример конфига: `examples/example.toml`.

## Архитектура

```
forgerace/
  config.py       — загрузка конфигурации (TOML)
  tasks.py        — парсер TASKS.md, модель задачи
  agents.py       — запуск агентов (Claude, Gemini, стриминг)
  review.py       — кросс-ревью, парсинг вердиктов
  pipeline.py     — основной пайплайн: конкурентное выполнение, race-модель
  decompose.py    — оценка сложности, авто-декомпозиция
  discuss.py      — система дискуссий (создание, чат, резолюция)
  worktree.py     — управление git worktree
  merge.py        — мерж в develop (detached worktree + update-ref)
  utils.py        — run_cmd, логирование, MUD-раскраска
  cli.py          — CLI точка входа (argparse)
```

## Лицензия

MIT — см. [LICENSE](LICENSE).
