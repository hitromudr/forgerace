"""Система дискуссий: создание, ответы агентов, интерактивный чат, резолюции."""

import re
import readline
import select
import subprocess
from datetime import datetime
from pathlib import Path

from .config import cfg
from .decompose import insert_tasks_into_tasksmd
from .tasks import Task, parse_tasks, link_task_discussion
from .utils import log, run_cmd


# --- CRUD ---

def discuss_create(topic: str, question: str, author: str = "techlead"):
    """Создаёт новую дискуссию."""
    cfg.discuss_dir.mkdir(parents=True, exist_ok=True)
    filepath = cfg.discuss_dir / f"{topic}.md"

    if filepath.exists():
        log.error(f"Дискуссия {topic} уже существует")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    content = f"# {topic}\n\n## @{author} ({now})\n\n{question}\n"
    filepath.write_text(content, encoding="utf-8")
    log.info(f"Дискуссия создана: {filepath}")


def discuss_reply(topic: str, agent_type: str):
    """Запускает агента чтобы он ответил в дискуссии."""
    filepath = cfg.discuss_dir / f"{topic}.md"
    if not filepath.exists():
        log.error(f"Дискуссия {topic} не найдена")
        return

    discussion = filepath.read_text(encoding="utf-8")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    prompt = f"""Ты участник архитектурной дискуссии {cfg.discuss_context}.
Твоя роль: @{agent_type}.

Прочитай дискуссию ниже и напиши свой ответ. Будь конкретен: предлагай структуры,
трейты, алгоритмы. Если не согласен с предыдущим участником — аргументируй.

Отвечай ТОЛЬКО текстом своего сообщения (без заголовка, без форматирования секции).
Пиши на русском.
{cfg.confidence_instruction}

--- ДИСКУССИЯ ---
{discussion}
--- КОНЕЦ ---
"""

    if agent_type == "gemini":
        result = run_cmd(
            ["gemini", "-p", prompt],
            cwd=cfg.root_dir, timeout=cfg.agent_timeout, check=False,
        )
    else:
        result = run_cmd(
            ["claude", "-p", prompt, "--output-format", "text"],
            cwd=cfg.root_dir, timeout=cfg.agent_timeout, check=False,
        )
    reply_text = (result.stdout or "").strip() or "(пустой ответ)"

    _print_confidence(reply_text, agent_type)
    reply_text = re.sub(r"\n?CONFIDENCE:\s*\d+\s*%\s*$", "", reply_text).rstrip()

    with open(filepath, "a", encoding="utf-8") as f:
        f.write(f"\n## @{agent_type} ({now})\n\n{reply_text}\n")

    log.info(f"@{agent_type} ответил в {topic}")


def discuss_list():
    """Показывает открытые дискуссии."""
    if not cfg.discuss_dir.exists():
        log.info("Нет дискуссий")
        return

    for f in sorted(cfg.discuss_dir.glob("*.md")):
        if f.name == "README.md":
            continue
        text = f.read_text(encoding="utf-8")
        closed = "ЗАКРЫТО" in text or "РЕЗОЛЮЦИЯ" in text
        status = "ЗАКРЫТО" if closed else "ОТКРЫТО"
        participants = set(re.findall(r"## @(\w+)", text))
        print(f"  [{status}] {f.stem}  участники: {', '.join(sorted(participants))}")


def discuss_show(topic: str):
    """Показывает содержимое дискуссии."""
    filepath = cfg.discuss_dir / f"{topic}.md"
    if not filepath.exists():
        log.error(f"Дискуссия {topic} не найдена")
        return
    print(_format_discussion(filepath.read_text(encoding="utf-8")))


# --- Интерактивный чат ---

def discuss_chat(topic: str):
    """Интерактивный REPL-чат по теме."""
    cfg.discuss_dir.mkdir(parents=True, exist_ok=True)
    filepath = cfg.discuss_dir / f"{topic}.md"

    if not filepath.exists():
        filepath.write_text(f"# {topic}\n", encoding="utf-8")
        print(f"Создана новая дискуссия: {topic}")

    # Автокомплит
    _chat_commands = ["/claude", "/gemini", "/both", "/show", "/ok", "/resolve", "/help", "/exit"]

    def _completer(text, state):
        buf = readline.get_line_buffer().lstrip()
        options = [c + " " for c in _chat_commands if c.startswith(buf)]
        if state < len(options):
            return options[state]
        return None

    readline.set_completer(_completer)
    readline.set_completer_delims("")
    readline.parse_and_bind("tab: complete")

    text = filepath.read_text(encoding="utf-8")
    if text.strip() != f"# {topic}":
        print(_format_discussion(text))
        print("─" * 60)

    _print_chat_help()
    print()

    while True:
        try:
            text = input("TechLead> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            break

        if not text:
            continue

        parts = text.split(None, 1)
        cmd = parts[0].lower()
        extra = parts[1] if len(parts) > 1 else ""

        if cmd == "/exit":
            break
        elif cmd == "/help":
            _print_chat_help()
            continue
        elif cmd == "/show":
            print(filepath.read_text(encoding="utf-8"))
            continue
        elif cmd in ("/claude", "/gemini", "/both"):
            if extra:
                _chat_append(filepath, "techlead", extra)
            if cmd in ("/claude", "/both"):
                _chat_agent_reply(filepath, "claude")
            if cmd in ("/gemini", "/both"):
                _chat_agent_reply(filepath, "gemini")
            print("─" * 60)
            print("  Введите текст — добавить свой комментарий в дискуссию")
            print("  /both — пусть оба прокомментируют   /ok — одобрить и закрыть")
            print("  /help — все команды")
            print("─" * 60)
            continue
        elif cmd == "/ok":
            comment = extra or ""
            if comment:
                _chat_append(filepath, "techlead", comment)
            _chat_append(filepath, "techlead",
                         "Я готов утвердить. Ваши финальные замечания или возражения? "
                         "Если согласны — напишите 'согласен'. Если нет — аргументируйте.")
            print("[Финальный раунд — оба агента высказываются перед закрытием]\n")
            _chat_agent_reply(filepath, "claude")
            _chat_agent_reply(filepath, "gemini")
            _chat_auto_resolve(filepath)
            _post_resolve(filepath)
            _auto_link_discussion(topic)
            break
        elif cmd == "/resolve":
            resolve_msg = extra or input("Резолюция> ").strip()
            if resolve_msg:
                _chat_append(filepath, "techlead", f"**РЕЗОЛЮЦИЯ:** {resolve_msg}")
                print("Дискуссия закрыта.")
            _post_resolve(filepath)
            _auto_link_discussion(topic)
            break
        else:
            _chat_append(filepath, "techlead", text)


# --- Вспомогательные ---

def _auto_link_discussion(topic: str):
    """Автолинковка: находит задачу по номеру в топике."""
    m = re.match(r"(\d+)", topic)
    if not m:
        return
    task_id = f"TASK-{int(m.group(1)):03d}"
    tasks = parse_tasks()
    for t in tasks:
        if t.id == task_id:
            link_task_discussion(task_id, topic)
            log.info(f"✓ Дискуссия '{topic}' привязана к {task_id}")
            return


def _post_resolve(filepath: Path):
    """После закрытия — генерирует задачи из дискуссии."""
    discussion = filepath.read_text(encoding="utf-8")
    topic = filepath.stem

    tasks = parse_tasks()
    max_num = max((int(re.match(r"TASK-(\d+)", t.id).group(1))
                   for t in tasks if re.match(r"TASK-(\d+)", t.id)), default=0)
    next_task_num = max_num + 1

    linked_task_id = ""
    m = re.match(r"(\d+)", topic)
    if m:
        linked_task_id = f"TASK-{int(m.group(1)):03d}"

    prompt = f"""Прочитай закрытую дискуссию и сгенерируй блок задач для TASKS.md.

Формат каждой задачи — строго такой:

### TASK-XXX: Название
- **Статус**: open
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: TASK-YYY или —
- **Файлы (новые)**: src/path/file.rs
- **Файлы (modify)**: — или путь
- **Интеграция**: что добавить в lib.rs/main.rs при мерже
- **Описание**: что именно реализовать
- **Критерий готовности**: что должно работать
- **Дискуссия**: {topic}
- **Агент**: —
- **Ветка**: —

Правила:
- Нумерация задач начинается с TASK-{next_task_num:03d}
- Если дискуссия привязана к задаче {linked_task_id} — первая подзадача ЗАМЕНЯЕТ её
- Задачи должны быть атомарными (одна задача = один файл/модуль)
- Зависимости между задачами указать
- Пиши на русском

Выведи ТОЛЬКО блок задач в формате markdown, без пояснений.

--- ДИСКУССИЯ ---
{discussion}
--- КОНЕЦ ---
"""

    print("\n[Генерирую задачи из дискуссии...]")
    result = subprocess.run(
        ["claude", "-p", "-", "--output-format", "text", "--permission-mode", "auto"],
        cwd=cfg.root_dir, input=prompt, capture_output=True, text=True, timeout=cfg.agent_timeout,
    )
    tasks_block = result.stdout.strip() if result.stdout else ""

    if not tasks_block or tasks_block.startswith("Error:"):
        log.error(f"Не удалось сгенерировать задачи: {tasks_block or '(пустой ответ)'}")
        return

    tasks_file = filepath.parent / f"{topic}-tasks.md"
    tasks_file.write_text(tasks_block + "\n", encoding="utf-8")

    insert_tasks_into_tasksmd(tasks_block, linked_task_id)

    print(f"\n  ✓ Задачи сгенерированы и вставлены в TASKS.md")
    print(f"  ✓ Копия: {tasks_file}")
    log.info(f"{linked_task_id or topic}: подзадачи вставлены в TASKS.md")
    print(f"\n    → python3 forgerace.py run\n")


def _chat_auto_resolve(filepath: Path):
    """Техлид одобрил — агент суммирует дискуссию."""
    discussion = filepath.read_text(encoding="utf-8")

    prompt = f"""Прочитай дискуссию и напиши КРАТКУЮ резолюцию (3-5 строк):
- Какое решение принято по каждому вопросу
- Порядок реализации (пронумерованный список)

Пиши на русском. Только текст резолюции, без заголовков.

--- ДИСКУССИЯ ---
{discussion}
--- КОНЕЦ ---
"""

    print("[Генерирую резолюцию...]")
    result = subprocess.run(
        ["claude", "-p", "-", "--output-format", "text", "--permission-mode", "auto"],
        cwd=cfg.root_dir, input=prompt, capture_output=True, text=True, timeout=cfg.agent_timeout,
    )
    summary = result.stdout.strip() if result.stdout else "(не удалось сгенерировать)"
    print(f"\n{summary}\n")

    _chat_append(filepath, "techlead", f"**РЕЗОЛЮЦИЯ (одобрено):**\n\n{summary}")
    print("Дискуссия закрыта.")


def _chat_append(filepath: Path, role: str, message: str):
    """Дописывает сообщение в файл дискуссии."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(f"\n## @{role} ({now})\n\n{message}\n")


def _chat_agent_reply(filepath: Path, agent_type: str):
    """Вызывает агента со стримингом вывода."""
    discussion = filepath.read_text(encoding="utf-8")

    prompt = f"""Ты участник архитектурной дискуссии {cfg.discuss_context}.
Твоя роль: @{agent_type}.

Прочитай дискуссию и напиши свой ответ. Будь конкретен: предлагай структуры,
трейты, алгоритмы. Если не согласен — аргументируй. Отвечай кратко и по делу.
Пиши на русском. Выведи ТОЛЬКО текст ответа, без заголовков и метаданных.
ВАЖНО: ты НЕ принимаешь решений. Только @techlead утверждает план и выносит резолюции.
Ты можешь предлагать и рекомендовать, но НИКОГДА не пиши "план утверждён", "решено", "фиксируем" и т.п.
{cfg.confidence_instruction}

--- ДИСКУССИЯ ---
{discussion}
--- КОНЕЦ ---
"""

    acfg = cfg.agents.get(agent_type)
    if acfg is None:
        print(f"\n[ОШИБКА: агент '{agent_type}' не найден в конфиге]")
        return

    if agent_type == "gemini":
        cmd = [acfg.command, "-p", prompt]
    else:
        cmd = [acfg.command, "-p", "-", "--output-format", "text", "--permission-mode", "auto"]

    reply_lines = []
    spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    try:
        proc = subprocess.Popen(
            cmd, cwd=cfg.root_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.PIPE, text=True, bufsize=1,
        )
        if agent_type == "claude":
            proc.stdin.write(prompt)
        proc.stdin.close()

        spin_idx = 0
        print(f"{agent_type.capitalize()}> ", end="", flush=True)
        got_output = False
        while True:
            ready, _, _ = select.select([proc.stdout], [], [], 0.15)
            if ready:
                line = proc.stdout.readline()
                if not line:
                    break
                if not got_output:
                    print("\r" + " " * 40 + f"\r{agent_type.capitalize()}> ", end="", flush=True)
                    got_output = True
                print(line, end="", flush=True)
                reply_lines.append(line)
            else:
                if proc.poll() is not None:
                    for line in proc.stdout:
                        if not got_output:
                            print("\r" + " " * 40 + f"\r{agent_type.capitalize()}> ", end="", flush=True)
                            got_output = True
                        print(line, end="", flush=True)
                        reply_lines.append(line)
                    break
                if not got_output:
                    ch = spinner_chars[spin_idx % len(spinner_chars)]
                    print(f"\r{agent_type.capitalize()}> {ch} думает...", end="", flush=True)
                    spin_idx += 1
        proc.wait(timeout=cfg.agent_timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        print("\n[ТАЙМАУТ]")
    except KeyboardInterrupt:
        proc.kill()
        proc.wait()
        print("\n[Прервано]")
        return
    except FileNotFoundError:
        print(f"\n[ОШИБКА: команда '{cmd[0]}' не найдена]")
        return

    reply = "".join(reply_lines).strip()
    if not reply:
        stderr = proc.stderr.read() if proc.stderr else ""
        if stderr:
            print(f"\n[stderr: {stderr[:500]}]")
        reply = "(пустой ответ)"
    print()

    _print_confidence(reply, agent_type)
    reply = re.sub(r"\n?CONFIDENCE:\s*\d+\s*%\s*$", "", reply).rstrip()

    _chat_append(filepath, agent_type, reply)


def _format_discussion(text: str) -> str:
    """Форматирует markdown дискуссии для терминала."""
    lines = text.splitlines()
    result = []
    for line in lines:
        m = re.match(r"^## @(\w+)\s*(.*)$", line)
        if m:
            agent = m.group(1)
            meta = m.group(2)
            result.append(f"\n{'═' * 60}")
            result.append(f"  @{agent} {meta}")
            result.append(f"{'═' * 60}")
        elif line.startswith("# ") and not line.startswith("## "):
            result.append(f"\n{'━' * 60}")
            result.append(f"  {line[2:]}")
            result.append(f"{'━' * 60}")
        elif re.match(r"^CONFIDENCE:\s*\d+\s*%", line.strip()):
            continue
        else:
            result.append(line)
    return "\n".join(result)


def _print_chat_help():
    """Справка по командам чата."""
    print("Команды:")
    print("  (текст)   — ваш комментарий, сохраняется в дискуссию (агенты не вызываются)")
    print("  /claude   — запросить ответ Claude")
    print("  /gemini   — запросить ответ Gemini")
    print("  /both     — оба последовательно (Claude → Gemini)")
    print("  /claude (текст) — записать ваш комментарий, затем вызвать Claude")
    print("  /both (текст)   — записать комментарий, затем вызвать обоих")
    print("  /show     — показать всю дискуссию")
    print("  /ok       — одобрить и закрыть (резолюция генерируется автоматически)")
    print("  /resolve  — написать резолюцию вручную")
    print("  /help     — показать эту справку")
    print("  /exit     — выйти без резолюции")


def _print_confidence(text: str, agent_type: str):
    """Парсит CONFIDENCE: XX% и печатает футер."""
    m = re.search(r"CONFIDENCE:\s*(\d+)\s*%", text)
    if m:
        pct = int(m.group(1))
        label = f"  {agent_type} confidence: {pct}%  "
        width = max(len(label) + 4, 40)
        bar = "═" * width
        pad = (width - len(label)) // 2
        print(f"\n{bar}")
        print(f"{'═' * pad}{label}{'═' * (width - pad - len(label))}")
        print(f"{bar}\n")
