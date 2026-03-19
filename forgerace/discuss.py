"""Система дискуссий: создание, ответы агентов, интерактивный чат, резолюции."""

import json
import re
import readline
import select
import subprocess
import time
from datetime import datetime
from pathlib import Path

from .config import cfg, run_hint
from .decompose import insert_tasks_into_tasksmd
from .tasks import Task, parse_tasks, link_task_discussion
from .utils import log, run_cmd, C, R, agent_color

_C = C  # alias для совместимости
_agent_color = agent_color


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
        elif cmd in ("/claude", "/gemini", "/qwen", "/both", "/all"):
            if extra:
                _chat_append(filepath, "techlead", extra)
            agents_to_call = []
            if cmd == "/all":
                agents_to_call = [n for n in cfg.agent_names]
            elif cmd == "/both":
                agents_to_call = [n for n in ("claude", "gemini") if n in cfg.agent_names]
            else:
                name = cmd.lstrip("/")
                if name in cfg.agent_names:
                    agents_to_call = [name]
                else:
                    print(f"  {_C['red']}Агент '{name}' не найден в конфиге{_C['reset']}")
                    continue
            for name in agents_to_call:
                _chat_agent_reply(filepath, name)
            print(f"{_C['dim']}{'─' * 60}{_C['reset']}")
            print(f"  Введите текст — добавить свой комментарий в дискуссию")
            print(f"  {_C['yellow']}/all{_C['reset']} — все агенты   {_C['green']}/ok{_C['reset']} — одобрить и закрыть   {_C['yellow']}/help{_C['reset']} — все команды")
            print(f"{_C['dim']}{'─' * 60}{_C['reset']}")
            continue
        elif cmd == "/ok":
            comment = extra or ""
            if comment:
                _chat_append(filepath, "techlead", comment)
            _chat_append(filepath, "techlead",
                         "Я готов утвердить. Ваши финальные замечания или возражения? "
                         "Если согласны — напишите 'согласен'. Если нет — аргументируйте.")
            print(f"[Финальный раунд — все агенты высказываются перед закрытием]\n")
            for name in cfg.agent_names:
                _chat_agent_reply(filepath, name)
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
    print(f"\n    → {run_hint()}\n")


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


def _extract_text_from_claude_event(event: dict) -> str:
    """Извлекает текст из stream-json события Claude."""
    etype = event.get("type", "")
    if etype == "assistant":
        parts = []
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    if etype == "content_block_delta":
        delta = event.get("delta", {})
        if delta.get("type") == "text_delta":
            return delta.get("text", "")
    return ""


def _extract_text_from_gemini_event(event: dict) -> str:
    """Извлекает текст из stream-json события Gemini."""
    etype = event.get("type", "")
    if etype in ("text", "content"):
        return event.get("text", event.get("content", ""))
    if etype == "message":
        parts = []
        for block in event.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


def _chat_agent_reply(filepath: Path, agent_type: str):
    """Вызывает агента со стримингом текста по токенам."""
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

    # Используем stream-json для реального стриминга текста
    if agent_type == "claude":
        cmd = [acfg.command, "-p", "-", "--output-format", "stream-json",
               "--verbose", "--permission-mode", "auto"]
        extract_fn = _extract_text_from_claude_event
    else:
        cmd = [acfg.command, "-p", prompt, "--output-format", "stream-json"]
        extract_fn = _extract_text_from_gemini_event

    reply_parts = []
    spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    start_time = time.time()
    R = _C["reset"]
    color = _agent_color(agent_type)
    label = f"{color}{_C['bold']}{agent_type.capitalize()}{R}"
    raw_label = agent_type.capitalize()
    try:
        proc = subprocess.Popen(
            cmd, cwd=cfg.root_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.PIPE, text=True, bufsize=1,
        )
        if agent_type == "claude":
            proc.stdin.write(prompt)
        proc.stdin.close()

        spin_idx = 0
        got_text = False
        print(f"{label}> ", end="", flush=True)

        while True:
            ready, _, _ = select.select([proc.stdout], [], [], 0.15)
            if ready:
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    # Gemini иногда пишет plain text
                    if not got_text:
                        print(f"\r{label}> ", end="", flush=True)
                        got_text = True
                    print(line, flush=True)
                    reply_parts.append(line)
                    continue

                text = extract_fn(event)
                if text:
                    if not got_text:
                        # Очистить спиннер
                        print(f"\r{' ' * 50}\r", end="", flush=True)
                        got_text = True
                    print(text, end="", flush=True)
                    reply_parts.append(text)
            else:
                if proc.poll() is not None:
                    # Дочитать остатки
                    for line in proc.stdout:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                            text = extract_fn(event)
                            if text:
                                if not got_text:
                                    print(f"\r{' ' * 50}\r", end="", flush=True)
                                    got_text = True
                                print(text, end="", flush=True)
                                reply_parts.append(text)
                        except json.JSONDecodeError:
                            pass
                    break
                if not got_text:
                    ch = spinner_chars[spin_idx % len(spinner_chars)]
                    elapsed = int(time.time() - start_time)
                    print(f"\r{label}> {color}{ch}{R} {_C['dim']}думает... {elapsed}s{R}  ", end="", flush=True)
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

    reply = "".join(reply_parts).strip()
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
    """Форматирует markdown дискуссии для терминала с цветами."""
    R = _C["reset"]
    DIM = _C["dim"]
    BOLD = _C["bold"]
    lines = text.splitlines()
    result = []
    for line in lines:
        m = re.match(r"^## @(\w+)\s*(.*)$", line)
        if m:
            agent = m.group(1)
            meta = m.group(2)
            color = _agent_color(agent)
            result.append(f"\n{DIM}{'═' * 60}{R}")
            result.append(f"  {color}{BOLD}@{agent}{R} {DIM}{meta}{R}")
            result.append(f"{DIM}{'═' * 60}{R}")
        elif line.startswith("# ") and not line.startswith("## "):
            result.append(f"\n{_C['yellow']}{'━' * 60}{R}")
            result.append(f"  {_C['yellow']}{BOLD}{line[2:]}{R}")
            result.append(f"{_C['yellow']}{'━' * 60}{R}")
        elif re.match(r"^CONFIDENCE:\s*\d+\s*%", line.strip()):
            continue
        else:
            result.append(line)
    return "\n".join(result)


def _print_chat_help():
    """Справка по командам чата."""
    R = _C["reset"]
    DIM = _C["dim"]
    Y = _C["yellow"]
    G = _C["green"]
    print(f"{DIM}Команды:{R}")
    print(f"  {DIM}(текст){R}   — ваш комментарий, сохраняется в дискуссию (агенты не вызываются)")
    print(f"  {Y}/claude{R}   — запросить ответ {_C['cyan']}Claude{R}")
    print(f"  {Y}/gemini{R}   — запросить ответ {_C['magenta']}Gemini{R}")
    print(f"  {Y}/qwen{R}     — запросить ответ {_C['blue']}Qwen{R}")
    print(f"  {Y}/both{R}     — Claude + Gemini")
    print(f"  {Y}/all{R}      — все агенты последовательно")
    print(f"  {Y}/claude{R} {DIM}(текст){R} — записать ваш комментарий, затем вызвать Claude")
    print(f"  {Y}/all{R} {DIM}(текст){R}    — записать комментарий, затем вызвать всех")
    print(f"  {Y}/show{R}     — показать всю дискуссию")
    print(f"  {G}/ok{R}       — одобрить и закрыть (резолюция генерируется автоматически)")
    print(f"  {Y}/resolve{R}  — написать резолюцию вручную")
    print(f"  {Y}/help{R}     — показать эту справку")
    print(f"  {_C['red']}/exit{R}     — выйти без резолюции")


def _print_confidence(text: str, agent_type: str):
    """Парсит CONFIDENCE: XX% и печатает цветной футер."""
    R = _C["reset"]
    m = re.search(r"CONFIDENCE:\s*(\d+)\s*%", text)
    if m:
        pct = int(m.group(1))
        color = _agent_color(agent_type)
        # Цвет процента по значению
        if pct >= 80:
            pct_color = _C["green"]
        elif pct >= 50:
            pct_color = _C["yellow"]
        else:
            pct_color = _C["red"]
        label = f"{agent_type} confidence: {pct_color}{pct}%{R}"
        raw_label = f"{agent_type} confidence: {pct}%"
        width = max(len(raw_label) + 8, 40)
        bar = f"{_C['dim']}{'═' * width}{R}"
        pad = (width - len(raw_label) - 4) // 2
        print(f"\n{bar}")
        print(f"{_C['dim']}{'═' * pad}{R}  {color}{label}  {_C['dim']}{'═' * (width - pad - len(raw_label) - 4)}{R}")
        print(f"{bar}\n")
