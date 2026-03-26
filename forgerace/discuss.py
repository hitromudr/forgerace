"""Система дискуссий: создание, ответы агентов, интерактивный чат, резолюции."""

import json
import os
import re
import select
import shutil
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

    from .agents import run_reviewer
    reply_text = run_reviewer(agent_type, prompt) or "(пустой ответ)"

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
        has_resolution = "ЗАКРЫТО" in text or "РЕЗОЛЮЦИЯ" in text
        reopened = "ДИСКУССИЯ ПЕРЕОТКРЫТА" in text
        if reopened and has_resolution:
            # переоткрыта после последней резолюции?
            last_resolve = max(text.rfind("ЗАКРЫТО"), text.rfind("РЕЗОЛЮЦИЯ"))
            last_reopen = text.rfind("ДИСКУССИЯ ПЕРЕОТКРЫТА")
            closed = last_resolve > last_reopen
        else:
            closed = has_resolution
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

    # readline: история ввода + автокомплит команд по Tab
    try:
        import readline
        _chat_commands = [
            "/claude", "/gemini", "/qwen", "/both", "/all",
            "/solo", "/fresh",
            "/show", "/stats", "/summary", "/compact", "/undo",
            "/tasks", "/ok", "/resolve", "/reopen",
            "/help", "/exit",
        ]
        def _completer(text, state):
            if text.startswith("/"):
                matches = [c for c in _chat_commands if c.startswith(text)]
            else:
                matches = []
            return matches[state] if state < len(matches) else None
        readline.set_completer(_completer)
        readline.set_completer_delims(" ")
        readline.parse_and_bind("tab: complete")
    except ImportError:
        pass

    text = filepath.read_text(encoding="utf-8")
    if text.strip() != f"# {topic}":
        print(_format_discussion(text))
        print("─" * 60)

    _print_chat_help()
    print()

    while True:
        try:
            text = input(f"{_C['green']}{_C['bold']}TechLead>{_C['reset']} ").strip()
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
            text_content = filepath.read_text(encoding="utf-8")
            if extra.isdigit():
                n = int(extra)
                msgs = _parse_messages(text_content)
                tail = msgs[-n:] if n < len(msgs) else msgs
                tail_text = "".join(m["raw"] for m in tail)
                print(_format_discussion(tail_text))
            else:
                _pager(_format_discussion(text_content))
            continue
        elif cmd == "/stats":
            _chat_stats(filepath)
            continue
        elif cmd == "/summary":
            _chat_summary(filepath)
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
        elif cmd == "/solo":
            if not extra:
                print(f"  {_C['red']}Формат: /solo <agent[,agent]> <промпт>{_C['reset']}")
                continue
            solo_parts = extra.split(None, 1)
            if len(solo_parts) < 2:
                print(f"  {_C['red']}Формат: /solo <agent[,agent]> <промпт>{_C['reset']}")
                continue
            solo_agents_str, solo_prompt = solo_parts
            solo_agents = [a.strip() for a in solo_agents_str.split(",") if a.strip()]
            bad = [a for a in solo_agents if a not in cfg.agent_names]
            if bad:
                print(f"  {_C['red']}Агенты не найдены: {', '.join(bad)}{_C['reset']}")
                continue
            if len(solo_agents) > 1:
                _chat_solo_parallel(filepath, solo_agents, solo_prompt)
            else:
                _chat_solo_reply(filepath, solo_agents[0], solo_prompt)
            print(f"{_C['dim']}{'─' * 60}{_C['reset']}")
            continue
        elif cmd == "/fresh":
            if not extra:
                print(f"  {_C['red']}Формат: /fresh <agent[,agent]> <промпт>{_C['reset']}")
                continue
            fresh_parts = extra.split(None, 1)
            if len(fresh_parts) < 2:
                print(f"  {_C['red']}Формат: /fresh <agent[,agent]> <промпт>{_C['reset']}")
                continue
            fresh_agents_str, fresh_prompt = fresh_parts
            fresh_agents = [a.strip() for a in fresh_agents_str.split(",") if a.strip()]
            bad = [a for a in fresh_agents if a not in cfg.agent_names]
            if bad:
                print(f"  {_C['red']}Агенты не найдены: {', '.join(bad)}{_C['reset']}")
                continue
            if len(fresh_agents) > 1:
                messages = _parse_messages(filepath.read_text(encoding="utf-8"))
                intro = messages[1]["body"] if len(messages) > 1 else ""
                full_fresh = f"Контекст дискуссии (только вводные, без хода обсуждения):\n\n{intro}\n\n---\n\nВопрос: {fresh_prompt}\n\nОтвечай на русском. Дай свежий взгляд — ты не видишь что обсуждали другие участники."
                _chat_solo_parallel(filepath, fresh_agents, full_fresh, tag="fresh")
            else:
                _chat_fresh_reply(filepath, fresh_agents[0], fresh_prompt)
            print(f"{_C['dim']}{'─' * 60}{_C['reset']}")
            continue
        elif cmd == "/reopen":
            disc_text = filepath.read_text(encoding="utf-8")
            has_resolution = "РЕЗОЛЮЦИЯ" in disc_text or "ЗАКРЫТО" in disc_text
            last_reopen = disc_text.rfind("ДИСКУССИЯ ПЕРЕОТКРЫТА")
            last_resolve = max(disc_text.rfind("РЕЗОЛЮЦИЯ"), disc_text.rfind("ЗАКРЫТО"))
            already_reopened = last_reopen > last_resolve if has_resolution else False
            if not has_resolution:
                print(f"  {_C['yellow']}Дискуссия не закрыта — нечего переоткрывать{_C['reset']}")
                continue
            if already_reopened:
                print(f"  {_C['yellow']}Дискуссия уже переоткрыта — продолжайте обсуждение{_C['reset']}")
                continue
            reason = extra or "Техлид считает, что дискуссия закрыта преждевременно."
            _chat_append(filepath, "techlead",
                         f"**ДИСКУССИЯ ПЕРЕОТКРЫТА.**\n\n"
                         f"Причина: {reason}\n\n"
                         f"Агенты: критически пересмотрите резолюцию. "
                         f"Что в ней слабого, недосказанного или ошибочного? "
                         f"Не соглашайтесь по инерции — ищите проблемы.")
            print(f"[Дискуссия переоткрыта — вызываю всех агентов]\n")
            for name in cfg.agent_names:
                _chat_agent_reply(filepath, name)
            print(f"{_C['dim']}{'─' * 60}{_C['reset']}")
            continue
        elif cmd == "/tasks":
            _chat_review_tasks(filepath)
            continue
        elif cmd == "/undo":
            backup = filepath.with_suffix(".md.bak")
            if not backup.exists():
                print(f"  {_C['red']}Нет бэкапа ({backup.name}){_C['reset']}")
                continue
            filepath.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
            backup.unlink()
            print(f"  ✓ Восстановлено из {backup.name}")
            continue
        elif cmd == "/compact":
            keep = 4
            if extra.isdigit():
                keep = int(extra)
            _chat_compact(filepath, keep_last=keep)
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
- Зависимости: указывай ТОЛЬКО реальные — когда задача B использует код/файлы из задачи A. Если задачи работают с разными файлами и не зависят друг от друга — ставь "—". НЕ ставь линейную цепочку "каждая ждёт предыдущую" — это блокирует параллельное выполнение.
- Максимизируй параллелизм: чем больше задач на этапе 1 без зависимостей, тем лучше
- Пиши на русском

Выведи ТОЛЬКО блок задач в формате markdown, без пояснений.

--- ДИСКУССИЯ ---
{discussion}
--- КОНЕЦ ---
"""

    print("\n[Генерирую задачи из дискуссии...]")
    from .agents import run_text_agent
    tasks_block = run_text_agent(prompt, timeout=cfg.agent_timeout)

    if not tasks_block or tasks_block.startswith("Error:"):
        log.error(f"Не удалось сгенерировать задачи: {tasks_block or '(пустой ответ)'}")
        return

    tasks_file = filepath.parent / f"{topic}-tasks.md"
    tasks_file.write_text(tasks_block + "\n", encoding="utf-8")

    # Очистка мусора из ответа Claude (может добавить пояснения перед задачами)
    clean_block = re.sub(r"^.*?(?=### TASK-)", "", tasks_block, flags=re.DOTALL)
    if not clean_block.strip():
        clean_block = tasks_block  # fallback если regex не нашёл

    insert_tasks_into_tasksmd(clean_block, linked_task_id)

    print(f"\n  ✓ Задачи сгенерированы и вставлены в TASKS.md")
    print(f"  ✓ Копия: {tasks_file}")
    log.info(f"{linked_task_id or topic}: подзадачи вставлены в TASKS.md")
    print(f"\n    → {run_hint()}\n")


def _pager(text: str):
    """Выводит текст через пейджер (less) если он длиннее терминала."""
    lines = text.count("\n") + 1
    term_rows = shutil.get_terminal_size().lines
    if lines <= term_rows - 2:
        print(text)
        return
    try:
        proc = subprocess.Popen(
            ["less", "-R", "-X", "-F"],  # -R цвета, -X не чистит экран, -F выход если < экрана
            stdin=subprocess.PIPE, text=True,
        )
        proc.communicate(input=text)
    except FileNotFoundError:
        print(text)


def _chat_stats(filepath: Path):
    """Показывает статистику дискуссии."""
    text = filepath.read_text(encoding="utf-8")
    messages = _parse_messages(text)
    msgs = [m for m in messages if m["role"] != "__header__"]
    participants = set(m["role"] for m in msgs if m["role"] not in ("__unknown__", "compact"))
    size = filepath.stat().st_size
    if size < 1024:
        size_str = f"{size} B"
    elif size < 1024 * 1024:
        size_str = f"{size / 1024:.1f} KB"
    else:
        size_str = f"{size / (1024 * 1024):.1f} MB"
    R = _C["reset"]
    DIM = _C["dim"]
    Y = _C["yellow"]
    # Оценка токенов: кириллица ~1 токен на 2 символа, латиница ~1 на 4
    chars = len(text)
    tokens_est = int(chars / 2.5)  # грубая оценка для смешанного текста
    if tokens_est < 1000:
        tokens_str = f"~{tokens_est}"
    else:
        tokens_str = f"~{tokens_est / 1000:.1f}K"
    print(f"  {Y}Сообщений:{R}  {len(msgs)}")
    print(f"  {Y}Размер:{R}     {size_str} ({tokens_str} токенов)")
    print(f"  {Y}Участники:{R}  {', '.join(sorted(participants))}")
    # Подсчёт по участникам
    from collections import Counter
    counts = Counter(m["role"] for m in msgs)
    for role, cnt in counts.most_common():
        color = _agent_color(role)
        print(f"    {color}{role}{R}: {cnt}")


def _chat_summary(filepath: Path):
    """Генерирует саммари без закрытия дискуссии."""
    discussion = filepath.read_text(encoding="utf-8")
    prompt = f"""Прочитай дискуссию и напиши КРАТКОЕ саммари (5-10 строк):
- Ключевые тезисы каждого участника
- Точки согласия и разногласий
- Открытые вопросы (если есть)

Пиши на русском. Только текст саммари, без заголовков.

--- ДИСКУССИЯ ---
{discussion}
--- КОНЕЦ ---
"""
    print("[Генерирую саммари...]")
    from .agents import run_text_agent
    summary = run_text_agent(prompt, timeout=cfg.agent_timeout)
    if not summary or summary.startswith("Error:"):
        print(f"  {_C['red']}Не удалось: {summary or '(пустой ответ)'}{_C['reset']}")
        return
    print()
    print(summary)
    print()


def _chat_review_tasks(filepath: Path):
    """Анализ задач в TASKS.md на соответствие дискуссии. Интерактивный: анализ → правки → применение."""
    discussion = filepath.read_text(encoding="utf-8")
    topic = filepath.stem

    tasks = parse_tasks()
    # Задачи привязанные к этой дискуссии
    topic_tasks = [t for t in tasks if getattr(t, 'discussion', '') == topic]
    all_tasks = [t for t in tasks]

    if not all_tasks:
        print(f"  {_C['yellow']}TASKS.md пуст — используйте /ok или /resolve для генерации задач{_C['reset']}")
        return

    tasks_text = Path(cfg.root_dir / "TASKS.md").read_text(encoding="utf-8")

    review_prompt = f"""Проанализируй задачи из TASKS.md на соответствие текущему состоянию дискуссии.

Для каждой задачи определи:
- Релевантна ли она текущему состоянию дискуссии (дискуссия могла уйти дальше)
- Нужна ли корректировка описания/зависимостей
- Устарела ли (тема закрыта или переосмыслена)

Также определи:
- Каких задач не хватает (темы обсуждались, но задач нет)
- Какие задачи лишние (не вытекают из дискуссии)

Формат ответа — краткий, по пунктам:
✓ TASK-XXX: ок (или краткая причина почему ок)
✏ TASK-XXX: нужна правка — что именно
✗ TASK-XXX: устарела/лишняя — почему
+ Не хватает: краткое описание недостающей задачи

В конце — итог одной строкой: сколько ок, сколько нужна правка, сколько лишних, сколько не хватает.

Пиши на русском. Будь конкретен, без воды.

--- ДИСКУССИЯ ---
{discussion}
--- КОНЕЦ ДИСКУССИИ ---

--- TASKS.MD ---
{tasks_text}
--- КОНЕЦ TASKS.MD ---
"""

    print("[Анализирую задачи vs дискуссию...]")
    from .agents import run_text_agent
    review = run_text_agent(review_prompt, timeout=cfg.agent_timeout)
    if not review or review.startswith("Error:"):
        print(f"  {_C['red']}Не удалось: {review or '(пустой ответ)'}{_C['reset']}")
        return

    R = _C["reset"]
    print(f"\n{review}\n")
    print(f"{_C['dim']}{'─' * 60}{R}")
    print(f"  Варианты:")
    print(f"  {_C['green']}ок{R}              — применить рекомендации как есть")
    print(f"  {_C['yellow']}(текст){R}         — применить с вашими правками")
    print(f"  {_C['red']}нет{R}             — отменить, ничего не менять")
    print(f"{_C['dim']}{'─' * 60}{R}")

    try:
        answer = input(f"{_C['green']}{_C['bold']}TechLead>{R} ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Отменено.")
        return

    if not answer or answer.lower() in ("нет", "no", "отмена", "cancel"):
        print("  Отменено.")
        return

    # Генерируем обновлённые задачи с учётом ревью и правок техлида
    techlead_instruction = ""
    if answer.lower() not in ("ок", "ok", "да", "yes"):
        techlead_instruction = f"\nДополнительные указания техлида: {answer}\n"

    max_num = max((int(re.match(r"TASK-(\d+)", t.id).group(1))
                   for t in all_tasks if re.match(r"TASK-(\d+)", t.id)), default=0)
    next_task_num = max_num + 1

    regen_prompt = f"""На основе анализа задач и дискуссии, сгенерируй ПОЛНЫЙ обновлённый блок задач для TASKS.md.

Вот результат ревью (что оставить, что исправить, что добавить, что убрать):
{review}
{techlead_instruction}
Формат каждой задачи — строго такой:

### TASK-XXX: Название
- **Статус**: open
- **Приоритет**: P1
- **Этап**: N
- **Зависимости**: TASK-YYY или —
- **Файлы (новые)**: path/file
- **Файлы (modify)**: — или путь
- **Интеграция**: —
- **Описание**: что именно реализовать
- **Критерий готовности**: что должно работать
- **Дискуссия**: {topic}
- **Агент**: —
- **Ветка**: —

Правила:
- Сохрани нумерацию существующих задач которые остаются (не перенумеровывай!)
- Новые задачи нумеруй начиная с TASK-{next_task_num:03d}
- Задачи помеченные ✗ — НЕ включай
- Задачи помеченные ✏ — включи с исправлениями
- Задачи помеченные + — добавь как новые
- Максимизируй параллелизм
- Пиши на русском

Выведи ТОЛЬКО блок задач в формате markdown, без пояснений.

--- ДИСКУССИЯ ---
{discussion}
--- КОНЕЦ ДИСКУССИИ ---

--- ТЕКУЩИЕ ЗАДАЧИ ---
{tasks_text}
--- КОНЕЦ ЗАДАЧ ---
"""

    print("\n[Перегенерирую задачи...]")
    tasks_block = run_text_agent(regen_prompt, timeout=cfg.agent_timeout)
    if not tasks_block or tasks_block.startswith("Error:"):
        print(f"  {_C['red']}Не удалось: {tasks_block or '(пустой ответ)'}{_C['reset']}")
        return

    # Очистка мусора
    clean_block = re.sub(r"^.*?(?=### TASK-)", "", tasks_block, flags=re.DOTALL)
    if not clean_block.strip():
        clean_block = tasks_block

    # Бэкап
    tasks_path = cfg.root_dir / "TASKS.md"
    backup = tasks_path.with_suffix(".md.bak")
    backup.write_text(tasks_text, encoding="utf-8")

    # Перезаписываем TASKS.md: заголовок + новые задачи
    header_match = re.match(r"^(#[^#].*?\n)", tasks_text)
    header = header_match.group(1) if header_match else f"# TASKS — {topic}\n"
    tasks_path.write_text(header + "\n" + clean_block.strip() + "\n", encoding="utf-8")

    print(f"\n  ✓ TASKS.md обновлён")
    print(f"  ✓ Бэкап: {backup.name}")


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
    from .agents import run_text_agent
    summary = run_text_agent(prompt, timeout=cfg.agent_timeout) or "(не удалось сгенерировать)"
    print(f"\n{summary}\n")

    _chat_append(filepath, "techlead", f"**РЕЗОЛЮЦИЯ (одобрено):**\n\n{summary}")
    print("Дискуссия закрыта.")


def _parse_messages(text: str) -> list[dict]:
    """Парсит файл дискуссии в список сообщений.

    Возвращает [{"role": str, "meta": str, "body": str, "raw": str}, ...].
    Первый элемент — заголовок (role="__header__").
    """
    parts = re.split(r"(?=\n## @)", text)
    messages = []
    for i, part in enumerate(parts):
        if i == 0:
            messages.append({"role": "__header__", "meta": "", "body": part, "raw": part})
            continue
        m = re.match(r"\n## @(\S+)\s*(.*?)\n\n?(.*)", part, re.DOTALL)
        if m:
            messages.append({
                "role": m.group(1),
                "meta": m.group(2),
                "body": m.group(3).strip(),
                "raw": part,
            })
        else:
            messages.append({"role": "__unknown__", "meta": "", "body": part.strip(), "raw": part})
    return messages


def _extract_anchors(messages: list[dict]) -> list[str]:
    """Извлекает якоря техлида через LLM — оценки с контекстом что оценивается."""
    has_techlead = any(m["role"] == "techlead" for m in messages)
    if not has_techlead:
        return []

    # Передаём весь контекст чтобы LLM видел на что реагирует техлид
    full_text = "".join(m["raw"] for m in messages)

    prompt = f"""Из дискуссии ниже извлеки якоря — моменты где @techlead оценивает, одобряет,
отвергает или задаёт направление. Стиль техлида разговорный, с матом и сленгом.

ВАЖНО: каждый якорь должен содержать И оценку, И что оценивается.
Плохо: "хуета" (непонятно что)
Хорошо: "хуета — про предложение Gemini использовать WASM-прокси"

Плохо: "мне понравились"
Хорошо: "5 пунктов Клода понравились (код-расходник, проверки вне кода, жёсткость по необратимости, поломка→правило, человек→правила)"

Формат: по одному якорю на строку. Без нумерации и маркеров.
Максимум 15 якорей, только самые значимые.

--- ДИСКУССИЯ ---
{full_text}
--- КОНЕЦ ---
"""

    from .agents import run_text_agent
    result = run_text_agent(prompt, timeout=60)
    if not result or result.startswith("Error:"):
        return []
    return [line.strip() for line in result.strip().splitlines() if line.strip()]


def _chat_compact(filepath: Path, keep_last: int = 4):
    """Компактифицирует дискуссию: ранние сообщения → резюме, якоря техлида сохраняются."""
    text = filepath.read_text(encoding="utf-8")
    messages = _parse_messages(text)

    # header + сообщения
    if len(messages) <= keep_last + 2:
        print(f"  Дискуссия слишком короткая ({len(messages) - 1} сообщений), компактификация не нужна.")
        return

    header = messages[0]
    first_msg = messages[1]  # вводные к дискуссии — не компактифицируем
    to_compact = messages[2:-keep_last]
    to_keep = messages[-keep_last:]

    if not to_compact:
        print(f"  Нечего компактифицировать (первое сообщение + последние {keep_last} = всё).")
        return

    print(f"[Извлекаю якоря техлида...]")
    anchors = _extract_anchors(to_compact)

    # Формируем блок для суммаризации
    compact_text = "".join(m["raw"] for m in to_compact)

    anchor_block = ""
    if anchors:
        anchor_lines = "\n".join(f"- {a}" for a in anchors[:20])
        anchor_block = f"""
ВАЖНО: в сводку ОБЯЗАТЕЛЬНО интегрируй оценки и решения техлида (якоря).
Не выноси их отдельным списком — вплети в текст как цитаты или пересказ.
Каждый якорь должен быть отражён в сводке:
{anchor_lines}
"""

    prompt = f"""Сожми следующие {len(to_compact)} сообщений дискуссии в единую сводку (10-20 строк).
Включи: ключевые решения, отвергнутые варианты, открытые вопросы, хронологию.
{anchor_block}
Оценки техлида (одобрения, отклонения, мат, сленг) — передавай близко к оригиналу,
это маркеры приоритета. Не смягчай и не переформулируй в литературный стиль.

Пиши на русском. Выведи ТОЛЬКО текст сводки, без заголовков.

--- СООБЩЕНИЯ ---
{compact_text}
--- КОНЕЦ ---
"""

    print(f"[Компактифицирую {len(to_compact)} сообщений, сохраняю: вводные + последние {keep_last}]")
    if anchors:
        print(f"[Извлечено якорей техлида: {len(anchors)}]")
        for a in anchors[:10]:
            print(f"  {_C['dim']}• {a[:100]}{_C['reset']}")
    else:
        print(f"[Якорей техлида не найдено]")
    print(f"[Генерирую сводку...]")
    from .agents import run_text_agent
    summary = run_text_agent(prompt, timeout=cfg.agent_timeout)
    if not summary or summary.startswith("Error:"):
        print(f"  {_C['red']}Не удалось сгенерировать сводку: {summary or '(пустой ответ)'}{_C['reset']}")
        return

    # Собираем новый файл
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    compacted = header["raw"]
    compacted += first_msg["raw"]
    compacted += f"\n## @compact ({now})\n\n"
    compacted += f"*[{len(to_compact)} сообщений компактифицировано]*\n\n"
    compacted += summary.strip() + "\n"

    for msg in to_keep:
        compacted += msg["raw"]

    # Бэкап
    backup = filepath.with_suffix(".md.bak")
    backup.write_text(text, encoding="utf-8")

    filepath.write_text(compacted, encoding="utf-8")
    print(f"  ✓ Компактифицировано: {len(to_compact)} → сводка, сохранено {keep_last} последних")
    print(f"  ✓ Бэкап: {backup.name}")
    if anchors:
        print(f"  ✓ Сохранено якорей техлида: {len(anchors)}")


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

    # text mode — промпт через stdin для всех агентов (избегаем лимита аргументов ОС)
    if agent_type == "claude":
        cmd = [acfg.command, "-p", "-", "--output-format", "text", "--permission-mode", "auto"]
    elif agent_type == "qwen":
        cmd = [acfg.command, "-p", "--output-format", "text", "--approval-mode", "yolo"]
    else:
        # gemini и другие — промпт через stdin + пустой -p для headless-режима
        cmd = [acfg.command, "-p", "", "--output-format", "text"]

    use_stdin = True

    reply_lines = []
    spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    start_time = time.time()
    R = _C["reset"]
    color = _agent_color(agent_type)
    label = f"{color}{_C['bold']}{agent_type.capitalize()}{R}"
    try:
        proc = subprocess.Popen(
            cmd, cwd=cfg.root_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.PIPE, text=True, bufsize=1,
        )
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except BrokenPipeError:
            proc.wait()
            stderr = proc.stderr.read() if proc.stderr else ""
            print(f"\n{_C['red']}[{agent_type}: процесс упал при получении промпта]{_C['reset']}")
            if stderr:
                print(f"{_C['red']}[stderr: {stderr[:300]}]{_C['reset']}")
            return

        got_output = False
        print(f"{label}> {_C['dim']}думает...{R}", end="", flush=True)

        while True:
            ready, _, _ = select.select([proc.stdout], [], [], 3.0)
            if ready:
                line = proc.stdout.readline()
                if not line:
                    break
                if not got_output:
                    print(flush=True)  # новая строка после "думает...Xs"
                    got_output = True
                print(_colorize_line(line.rstrip()), flush=True)
                reply_lines.append(line)
            else:
                if proc.poll() is not None:
                    for line in proc.stdout:
                        if not got_output:
                            print(flush=True)
                            got_output = True
                        print(_colorize_line(line.rstrip()), flush=True)
                        reply_lines.append(line)
                    break
                if not got_output:
                    elapsed = int(time.time() - start_time)
                    print(f"\r{label}> {_C['dim']}думает... {elapsed}s{R}   ", end="", flush=True)

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
        rc = proc.returncode
        if stderr:
            print(f"\n{_C['red']}[{agent_type} stderr: {stderr[:300]}]{R}")
        if rc and rc != 0:
            print(f"{_C['red']}[{agent_type} exit code: {rc}]{R}")
        reply = "(пустой ответ)"
    print()

    _print_confidence(reply, agent_type)
    reply = re.sub(r"\n?CONFIDENCE:\s*\d+\s*%\s*$", "", reply).rstrip()

    _chat_append(filepath, agent_type, reply)


def _chat_solo_parallel(filepath: Path, agent_names: list[str], prompt: str, tag: str = "solo"):
    """Запускает несколько агентов параллельно, показывает таймеры, выводит по мере готовности."""
    import threading

    R = _C["reset"]
    results = {}  # agent_name -> reply text
    errors = {}   # agent_name -> error text
    procs = {}    # agent_name -> proc
    start_time = time.time()

    def _run_agent(name):
        acfg = cfg.agents.get(name)
        if acfg is None:
            errors[name] = "не найден в конфиге"
            return
        if name == "claude":
            cmd = [acfg.command, "-p", "-", "--output-format", "text", "--permission-mode", "auto"]
        elif name == "qwen":
            cmd = [acfg.command, "-p", "--output-format", "text", "--approval-mode", "yolo"]
        else:
            cmd = [acfg.command, "-p", "", "--output-format", "text"]

        solo_cwd = "/tmp" if tag == "solo" else cfg.root_dir
        try:
            proc = subprocess.Popen(
                cmd, cwd=solo_cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.PIPE, text=True,
            )
            procs[name] = proc
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
            except BrokenPipeError:
                proc.wait()
                stderr = proc.stderr.read() if proc.stderr else ""
                errors[name] = f"процесс упал при получении промпта: {stderr[:200]}"
                return
            stdout, stderr = proc.communicate(timeout=cfg.agent_timeout)
            results[name] = stdout.strip() if stdout else ""
            if not results[name] and stderr:
                errors[name] = stderr[:300]
        except subprocess.TimeoutExpired:
            proc.kill()
            errors[name] = "ТАЙМАУТ"
        except FileNotFoundError:
            errors[name] = f"команда '{cmd[0]}' не найдена"

    # Запускаем все потоки
    threads = []
    for name in agent_names:
        t = threading.Thread(target=_run_agent, args=(name,), daemon=True)
        t.start()
        threads.append((name, t))

    # Показываем таймеры пока кто-то работает
    labels = {}
    for name in agent_names:
        color = _agent_color(name)
        labels[name] = f"{color}{_C['bold']}{name.capitalize()} ({tag}){R}"

    try:
        while any(t.is_alive() for _, t in threads):
            elapsed = int(time.time() - start_time)
            status_parts = []
            for name, t in threads:
                if t.is_alive():
                    status_parts.append(f"{labels[name]}> {_C['dim']}думает... {elapsed}s{R}")
                elif name in results:
                    status_parts.append(f"{labels[name]}> {_C['green']}готов{R}")
                elif name in errors:
                    status_parts.append(f"{labels[name]}> {_C['red']}ошибка{R}")
            print(f"\r{'   '.join(status_parts)}   ", end="", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n[Прервано]")
        for name, proc in procs.items():
            try:
                proc.kill()
            except Exception:
                pass
        return

    print()  # новая строка после таймеров

    # Выводим результаты
    prompt_quote = prompt[:200] + ("..." if len(prompt) > 200 else "")
    for name in agent_names:
        color = _agent_color(name)
        label = f"{color}{_C['bold']}{name.capitalize()} ({tag}){R}"
        if name in results and results[name]:
            reply = results[name]
            print(f"\n{label}>")
            for line in reply.splitlines():
                print(_colorize_line(line))
            print()
            solo_message = f"> Промпт: {prompt_quote}\n\n{reply}"
            _chat_append(filepath, f"{name} [{tag}]", solo_message)
        elif name in errors:
            print(f"\n{label}> {_C['red']}[{errors[name]}]{R}")
        else:
            print(f"\n{label}> (пустой ответ)")
            _chat_append(filepath, f"{name} [{tag}]", f"> Промпт: {prompt_quote}\n\n(пустой ответ)")


def _chat_solo_reply(filepath: Path, agent_type: str, prompt: str, tag: str = "solo"):
    """Вызывает агента с чистым промптом БЕЗ контекста дискуссии."""
    acfg = cfg.agents.get(agent_type)
    if acfg is None:
        print(f"\n[ОШИБКА: агент '{agent_type}' не найден в конфиге]")
        return

    if agent_type == "claude":
        cmd = [acfg.command, "-p", "-", "--output-format", "text", "--permission-mode", "auto"]
    elif agent_type == "qwen":
        cmd = [acfg.command, "-p", "--output-format", "text", "--approval-mode", "yolo"]
    else:
        cmd = [acfg.command, "-p", "", "--output-format", "text"]

    reply_lines = []
    start_time = time.time()
    R = _C["reset"]
    color = _agent_color(agent_type)
    label = f"{color}{_C['bold']}{agent_type.capitalize()} ({tag}){R}"
    try:
        # solo запускается из /tmp чтобы агент не видел файлы проекта
        solo_cwd = "/tmp" if tag == "solo" else cfg.root_dir
        proc = subprocess.Popen(
            cmd, cwd=solo_cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.PIPE, text=True, bufsize=1,
        )
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except BrokenPipeError:
            proc.wait()
            stderr = proc.stderr.read() if proc.stderr else ""
            print(f"\n{_C['red']}[{agent_type}: процесс упал при получении промпта]{_C['reset']}")
            if stderr:
                print(f"{_C['red']}[stderr: {stderr[:300]}]{_C['reset']}")
            return

        got_output = False
        print(f"{label}> {_C['dim']}думает...{R}", end="", flush=True)

        while True:
            ready, _, _ = select.select([proc.stdout], [], [], 3.0)
            if ready:
                line = proc.stdout.readline()
                if not line:
                    break
                if not got_output:
                    print(flush=True)
                    got_output = True
                print(_colorize_line(line.rstrip()), flush=True)
                reply_lines.append(line)
            else:
                if proc.poll() is not None:
                    for line in proc.stdout:
                        if not got_output:
                            print(flush=True)
                            got_output = True
                        print(_colorize_line(line.rstrip()), flush=True)
                        reply_lines.append(line)
                    break
                if not got_output:
                    elapsed = int(time.time() - start_time)
                    print(f"\r{label}> {_C['dim']}думает... {elapsed}s{R}   ", end="", flush=True)

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
        rc = proc.returncode
        if stderr:
            print(f"\n{_C['red']}[{agent_type} stderr: {stderr[:300]}]{R}")
        if rc and rc != 0:
            print(f"{_C['red']}[{agent_type} exit code: {rc}]{R}")
        reply = "(пустой ответ)"
    print()

    # Записываем с пометкой и цитатой промпта
    prompt_quote = prompt[:200] + ("..." if len(prompt) > 200 else "")
    solo_message = f"> Промпт: {prompt_quote}\n\n{reply}"
    _chat_append(filepath, f"{agent_type} [{tag}]", solo_message)


def _chat_fresh_reply(filepath: Path, agent_type: str, prompt: str):
    """Вызывает агента с вводными дискуссии + промптом, но БЕЗ хода обсуждения."""
    messages = _parse_messages(filepath.read_text(encoding="utf-8"))
    # Первое сообщение после заголовка — вводные
    intro = messages[1]["body"] if len(messages) > 1 else ""
    full_prompt = f"""Контекст дискуссии (только вводные, без хода обсуждения):

{intro}

---

Вопрос: {prompt}

Отвечай на русском. Дай свежий взгляд — ты не видишь что обсуждали другие участники."""
    _chat_solo_reply(filepath, agent_type, full_prompt, tag="fresh")


def _colorize_line(line: str) -> str:
    """Подсвечивает inline markdown: **bold**, `code`, @agent."""
    R = _C["reset"]
    BOLD = _C["bold"]
    CYAN = _C["cyan"]
    GREEN = _C["green"]
    # **bold** → жирный
    line = re.sub(r"\*\*(.+?)\*\*", rf"{BOLD}\1{R}", line)
    # `code` → cyan
    line = re.sub(r"`([^`]+)`", rf"{CYAN}\1{R}", line)
    # @agent → цвет агента
    def _color_agent(m):
        name = m.group(1)
        return f"{_agent_color(name)}@{name}{R}"
    line = re.sub(r"@(\w+)", _color_agent, line)
    return line


def _format_discussion(text: str) -> str:
    """Форматирует markdown дискуссии для терминала с цветами."""
    R = _C["reset"]
    DIM = _C["dim"]
    BOLD = _C["bold"]
    YELLOW = _C["yellow"]
    GREEN = _C["green"]
    in_code_block = False
    lines = text.splitlines()
    result = []
    for line in lines:
        # Блоки кода — без подсветки
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            result.append(f"{DIM}{line}{R}")
            continue
        if in_code_block:
            result.append(f"{DIM}{line}{R}")
            continue

        m = re.match(r"^## @(\w+)\s*(.*)$", line)
        if m:
            agent = m.group(1)
            meta = m.group(2)
            color = _agent_color(agent)
            result.append(f"\n{DIM}{'═' * 60}{R}")
            result.append(f"  {color}{BOLD}@{agent}{R} {DIM}{meta}{R}")
            result.append(f"{DIM}{'═' * 60}{R}")
        elif line.startswith("# ") and not line.startswith("## "):
            result.append(f"\n{YELLOW}{'━' * 60}{R}")
            result.append(f"  {YELLOW}{BOLD}{line[2:]}{R}")
            result.append(f"{YELLOW}{'━' * 60}{R}")
        elif re.match(r"^###\s+", line):
            # ### заголовок
            result.append(f"\n{GREEN}{BOLD}{line}{R}")
        elif re.match(r"^CONFIDENCE:\s*\d+\s*%", line.strip()):
            continue
        elif line.strip().startswith("- "):
            # Списки — подсветка содержимого
            result.append(f"  {_colorize_line(line)}")
        else:
            result.append(_colorize_line(line))
    return "\n".join(result)


def _print_chat_help():
    """Справка по командам чата."""
    R = _C["reset"]
    DIM = _C["dim"]
    Y = _C["yellow"]
    G = _C["green"]
    # Группы: агенты, спецрежимы, контекст, анализ, жизненный цикл, служебные
    SEP = None  # разделитель между группами
    print(f"{DIM}Команды:{R}")
    cmds = [
        # --- Агенты ---
        (f"{DIM}(текст){R}",                          8,  "ваш комментарий в дискуссию"),
        (f"{Y}/claude{R} {DIM}[текст]{R}",            15, f"[комментарий +] ответ {_C['cyan']}Claude{R}"),
        (f"{Y}/gemini{R} {DIM}[текст]{R}",            15, f"[комментарий +] ответ {_C['magenta']}Gemini{R}"),
        (f"{Y}/qwen{R} {DIM}[текст]{R}",              13, f"[комментарий +] ответ {_agent_color('qwen')}Qwen{R}"),
        (f"{Y}/both{R} {DIM}[текст]{R}",              13, "Claude + Gemini"),
        (f"{Y}/all{R} {DIM}[текст]{R}",               12, "все агенты последовательно"),
        SEP,
        # --- Спецрежимы ---
        (f"{Y}/solo{R} {DIM}<agent> <промпт>{R}",     21, "чистый запрос без контекста дискуссии"),
        (f"{Y}/fresh{R} {DIM}<agent> <промпт>{R}",    22, "вводные + промпт, без хода обсуждения"),
        SEP,
        # --- Контекст ---
        (f"{Y}/show{R} {DIM}[N]{R}",                     9, "вся дискуссия (пейджер) или последние N сообщений"),
        (f"{Y}/stats{R}",                               6, "размер, токены, участники"),
        (f"{Y}/summary{R}",                              8, "саммари дискуссии (без закрытия)"),
        (f"{Y}/compact{R} {DIM}[N]{R}",                12, "сжать ранние сообщения в сводку (последние N, по умолчанию 4)"),
        (f"{Y}/undo{R}",                               5, "откатить compact/tasks из .bak"),
        SEP,
        # --- Жизненный цикл ---
        (f"{Y}/tasks{R}",                               6, "ревью задач vs дискуссия → правки → перегенерация"),
        (f"{G}/ok{R} {DIM}[текст]{R}",                 11, "одобрить и закрыть (резолюция + задачи)"),
        (f"{Y}/resolve{R} {DIM}[текст]{R}",            16, "резолюция вручную + задачи"),
        (f"{Y}/reopen{R} {DIM}[причина]{R}",           16, "переоткрыть (агенты критикуют резолюцию)"),
        SEP,
        # --- Служебные ---
        (f"{Y}/help{R}",                                5, "эта справка"),
        (f"{_C['red']}/exit{R}",                        5, "выйти без резолюции"),
    ]
    col = max(v for c in cmds if c is not None for _, v, _ in [c]) + 1
    for entry in cmds:
        if entry is None:
            print()
            continue
        colored, vlen, desc = entry
        pad = " " * (col - vlen)
        print(f"  {colored}{pad}— {desc}")


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
