"""Система дискуссий: создание, ответы агентов, интерактивный чат, резолюции."""

import os
import re
import select
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from .config import cfg, run_hint, resolve_agent_frame
from .decompose import insert_tasks_into_tasksmd
from .tasks import parse_tasks, link_task_discussion
from .utils import log, C, agent_color

_C = C  # alias для совместимости
_agent_color = agent_color
_chat_cwd = None  # рабочая директория агентов, инициализируется в discuss_chat


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


def _build_discuss_prompt(display_name: str, frame_section: str, discussion: str,
                          tier: str) -> str:
    """Build discussion prompt adapted to model tier."""
    context_hint = f" {cfg.discuss_context}" if cfg.discuss_context else ""

    if tier == "weak":
        # Weak models: strict structure, concrete questions, no room for rambling
        return f"""Ты участник дискуссии{context_hint}. Роль: @{display_name}.
{frame_section}
## Правила ответа
1. Прочитай ВСЮ дискуссию. Если другие участники уже ответили — опирайся на их ответы, не повторяй.
2. Отвечай КОНКРЕТНО: названия файлов, функций, структуры данных. Без общих рассуждений.
3. НЕ предлагай внешние зависимости (pip install). Используй только стандартную библиотеку Python.
4. НЕ выдумывай код, которого нет в проекте. Если не знаешь — скажи прямо.
5. Максимум 300 слов.

## Формат ответа
Ответь на 3 вопроса:
1. С чем из предыдущих ответов согласен/не согласен и почему?
2. Какое конкретное решение предлагаешь? (файлы, функции, сигнатуры)
3. Какие риски видишь в своём решении?

Пиши на русском.
{cfg.confidence_instruction}

--- ДИСКУССИЯ ---
{discussion}
--- КОНЕЦ ---
"""
    elif tier == "medium":
        # Medium models: structured with constraints against overengineering
        return f"""Ты участник дискуссии{context_hint}. Роль: @{display_name}.
{frame_section}
Прочитай ВСЮ дискуссию включая ответы других участников.

## Правила
1. Опирайся на предыдущие ответы — спорь, дополняй, развивай. НЕ повторяй чужие мысли.
2. Конкретика: файлы, функции, сигнатуры. Абстрактные рассуждения — бесполезны.
3. НЕ предлагай внешние зависимости (pip install). Только стандартная библиотека Python + код проекта.
4. НЕ создавай новые пакеты/подпакеты если можно обойтись одним файлом.
5. НЕ меняй сигнатуры существующих функций если можно обернуть вызов в try/except.
6. НЕ ссылайся на номера строк или код, которого ещё нет в проекте.
7. Простое решение лучше "правильного". Если задача решается в 200 строк — не делай архитектуру на 2000.
8. Максимум 500 слов.

## Структура ответа
- **Несогласия**: с чем из предыдущих ответов не согласен и почему
- **Решение**: конкретное предложение (файлы, функции, как вызывается)
- **Риски**: что может пойти не так

Пиши на русском.
{cfg.confidence_instruction}

--- ДИСКУССИЯ ---
{discussion}
--- КОНЕЦ ---
"""
    else:
        # Strong models: current open prompt
        return f"""Ты участник дискуссии{context_hint}.
Твоя роль: @{display_name}.
{frame_section}
Прочитай дискуссию и напиши свой ответ.
Уровень ответа определяй по контексту дискуссии:
- Если обсуждаются концепции, стратегии, trade-offs — рассуждай на уровне принципов и альтернатив. НЕ прыгай к коду.
- Если обсуждается конкретная реализация — предлагай структуры, алгоритмы, примеры.
- Если не согласен с предыдущим участником — аргументируй.

Отвечай ТОЛЬКО текстом своего сообщения (без заголовка, без форматирования секции).
Пиши на русском.
{cfg.confidence_instruction}

--- ДИСКУССИЯ ---
{discussion}
--- КОНЕЦ ---
"""


def discuss_reply(topic: str, agent_spec: str):
    """Запускает агента чтобы он ответил в дискуссии.

    agent_spec может быть 'claude', 'qwen+octagon', 'gemini+arbiter' и т.д.
    """
    filepath = cfg.discuss_dir / f"{topic}.md"
    if not filepath.exists():
        log.error(f"Дискуссия {topic} не найдена")
        return

    model_name, frame_content = resolve_agent_frame(agent_spec)
    discussion = filepath.read_text(encoding="utf-8")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    frame_section = ""
    if frame_content:
        frame_section = f"\n## Твой когнитивный фрейм\n{frame_content}\n"

    # Display name: show default_frame if no explicit +frame
    if "+" not in agent_spec:
        acfg = cfg.agents.get(agent_spec)
        display_name = f"{agent_spec}+{acfg.default_frame}" if acfg and acfg.default_frame else agent_spec
    else:
        display_name = agent_spec

    # Determine tier from agent config
    acfg = cfg.agents.get(model_name)
    tier = acfg.tier if acfg else "strong"
    prompt = _build_discuss_prompt(display_name, frame_section, discussion, tier)

    from .agents import run_reviewer
    agent_type = model_name  # run_reviewer использует model_name для выбора CLI
    reply_text = run_reviewer(agent_type, prompt) or "(пустой ответ)"

    _print_confidence(reply_text, agent_spec)
    reply_text = re.sub(r"\n?CONFIDENCE:\s*\d+\s*%\s*$", "", reply_text).rstrip()

    with open(filepath, "a", encoding="utf-8") as f:
        f.write(f"\n## @{display_name} ({now})\n\n{reply_text}\n")

    log.info(f"@{display_name} ответил в {topic}")


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
        # Генерируем автокомплит: /model, /model+frame для всех комбинаций
        _agent_cmds = [f"/{n}" for n in cfg.agent_names]
        _frame_cmds = []
        if cfg.frames:
            for aname in cfg.agent_names:
                for fname in cfg.frames:
                    _frame_cmds.append(f"/{aname}+{fname}")
        # /help <topic> автокомплит
        _help_topics = [f"/help {f}" for f in (cfg.frames or {})]
        _help_topics += ["/help all", "/help both", "/help solo", "/help fresh",
                         "/help ok", "/help resolve", "/help reset", "/help compact",
                         "/help frames", "/help scenarios"]
        _chat_commands = [
            *_agent_cmds, *_frame_cmds, "/both", "/all",
            "/solo", "/fresh",
            "/show", "/stats", "/summary", "/compact", "/drop", "/undo", "/reset", "/cd",
            "/tasks", "/ok", "/resolve", "/reopen",
            *_help_topics, "/help", "/exit",
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

    global _chat_cwd
    _chat_cwd = cfg.root_dir  # рабочая директория агентов, меняется через /cd

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
            if extra:
                _print_help_detail(extra.strip().lstrip("/").lstrip("+"))
            else:
                _print_chat_help()
            continue
        elif cmd == "/cd":
            if not extra:
                print(f"  {_C['yellow']}cwd агентов: {_chat_cwd}{_C['reset']}")
                continue
            new_dir = Path(extra).expanduser()
            if not new_dir.is_absolute():
                new_dir = (_chat_cwd / new_dir).resolve()
            if new_dir.is_dir():
                _chat_cwd = new_dir
                print(f"  ✓ cwd агентов: {_chat_cwd}")
            else:
                print(f"  {_C['red']}Директория не найдена: {new_dir}{_C['reset']}")
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
        elif cmd in ("/both", "/all") or _is_agent_cmd(cmd):
            # Парсим extra: отделяем агентов (если есть) от комментария
            # /gemini+arbiter claude+topdown — вызвать двоих последовательно
            # /claude текст комментария — комментарий + вызов claude
            # /all — все enabled агенты
            agents_to_call = []
            comment = ""
            if cmd == "/all":
                # /all gemini,claude — explicit order; /all — default order
                if extra:
                    explicit = _parse_agent_specs(extra)
                    if explicit:
                        agents_to_call = explicit
                    else:
                        comment = extra
                        agents_to_call = list(cfg.agent_names)
                else:
                    agents_to_call = list(cfg.agent_names)
            elif cmd == "/both":
                agents_to_call = [n for n in ("claude", "gemini") if n in cfg.agent_names]
                comment = extra
            else:
                # /claude, /qwen+octagon, /gemini+arbiter и т.д.
                spec = cmd.lstrip("/")
                model_name = spec.split("+", 1)[0] if "+" in spec else spec
                if model_name in cfg.agent_names:
                    agents_to_call = [spec]
                    comment = extra
                else:
                    print(f"  {_C['red']}Агент '{model_name}' не найден в конфиге{_C['reset']}")
                    continue
            if comment:
                _chat_append(filepath, "techlead", comment)
            for spec in agents_to_call:
                _chat_agent_reply(filepath, spec)
            print(f"{_C['dim']}{'─' * 60}{_C['reset']}")
            print("  Введите текст — добавить свой комментарий в дискуссию")
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
            # Парсим: /fresh agent1,agent2 промпт  или  /fresh agent1 agent2 промпт
            # Агенты — слова без пробелов в начале, промпт — всё остальное после последнего агента
            fresh_tokens = extra.split()
            # Собираем агентов: токены которые являются именами агентов (с учётом запятых)
            fresh_agents = []
            prompt_start_idx = 0
            for i, tok in enumerate(fresh_tokens):
                # Разделяем по запятой: "gemini,claude" → ["gemini", "claude"]
                parts = [p.strip() for p in tok.split(",") if p.strip()]
                all_agents = all(p.split("+")[0] in cfg.agent_names for p in parts)
                if all_agents:
                    fresh_agents.extend(parts)
                    prompt_start_idx = i + 1
                else:
                    break
            fresh_prompt = " ".join(fresh_tokens[prompt_start_idx:]) if prompt_start_idx < len(fresh_tokens) else ""
            if not fresh_agents or not fresh_prompt:
                print(f"  {_C['red']}Формат: /fresh <agent[,agent...]> <промпт>{_C['reset']}")
                continue
            bad = [a for a in fresh_agents if a.split("+")[0] not in cfg.agent_names]
            if bad:
                print(f"  {_C['red']}Агенты не найдены: {', '.join(bad)}{_C['reset']}")
                continue
            if len(fresh_agents) > 1:
                messages = _parse_messages(filepath.read_text(encoding="utf-8"))
                intro = messages[1]["body"] if len(messages) > 1 else ""
                full_fresh = f"ЗАДАНИЕ: {fresh_prompt}\n\nКонтекст дискуссии (для справки, без хода обсуждения):\n{intro}\n\nНАПОМИНАНИЕ — твоё задание: {fresh_prompt}\nОтвечай СТРОГО на задание. Если это фактический вопрос — ответь кратко. Не развивай тему дискуссии, не предлагай свой взгляд на парадоксы, не вступай в полемику — только ответ на задание.\nОтвечай на русском."
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
            print("[Дискуссия переоткрыта — вызываю всех агентов]\n")
            for name in cfg.agent_names:
                _chat_agent_reply(filepath, name)
            print(f"{_C['dim']}{'─' * 60}{_C['reset']}")
            continue
        elif cmd == "/tasks":
            _chat_review_tasks(filepath)
            continue
        elif cmd == "/reset":
            text = filepath.read_text(encoding="utf-8")
            messages = _parse_messages(text)
            if len(messages) < 2:
                print(f"  {_C['red']}Нечего сбрасывать — только заголовок{_C['reset']}")
                continue
            # Сохраняем бэкап
            backup = filepath.with_suffix(".md.bak")
            backup.write_text(text, encoding="utf-8")
            # Оставляем заголовок + первое сообщение (интро @techlead)
            header = messages[0]["raw"]  # "# topic\n"
            intro = messages[1]["raw"]   # "## @techlead ...\n\n...\n"
            filepath.write_text(header + "\n" + intro, encoding="utf-8")
            removed = len(messages) - 2
            print(f"  ✓ Сброс к интро. Удалено {removed} сообщений. Бэкап: {backup.name}")
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
        elif cmd == "/drop":
            if not extra:
                print(f"  {_C['red']}Укажи номер сообщения или имя агента. Пример: /drop 3, /drop -1, /drop claude{_C['reset']}")
                continue
            text_content = filepath.read_text(encoding="utf-8")
            messages = _parse_messages(text_content)
            if len(messages) < 2:
                print(f"  {_C['red']}Нечего удалять — только заголовок{_C['reset']}")
                continue
            # Parse argument: number (1-based, negatives from end) or agent name
            parts_drop = extra.split(None, 1)
            arg = parts_drop[0]
            drop_idx = None
            try:
                n = int(arg)
                if n < 0:
                    # -1 = last, -2 = second to last, etc.
                    drop_idx = len(messages) + n
                else:
                    drop_idx = n
            except ValueError:
                # Agent name — find last message from this agent
                agent_name = arg.lower()
                occurrence = 1
                if len(parts_drop) > 1 and parts_drop[1].isdigit():
                    occurrence = int(parts_drop[1])
                found = 0
                for i in range(len(messages) - 1, 0, -1):
                    if messages[i]["role"].lower() == agent_name:
                        found += 1
                        if found == occurrence:
                            drop_idx = i
                            break
                if drop_idx is None:
                    print(f"  {_C['red']}Сообщение от '{agent_name}' не найдено{_C['reset']}")
                    continue
            if drop_idx is not None and (drop_idx < 1 or drop_idx >= len(messages)):
                print(f"  {_C['red']}Номер вне диапазона (1..{len(messages) - 1}){_C['reset']}")
                continue
            dropped = messages[drop_idx]
            # Backup before dropping
            backup = filepath.with_suffix(".md.bak")
            backup.write_text(text_content, encoding="utf-8")
            # Reconstruct without the dropped message
            remaining = [m for i, m in enumerate(messages) if i != drop_idx]
            filepath.write_text("".join(m["raw"] for m in remaining), encoding="utf-8")
            body_preview = dropped["body"][:80].replace("\n", " ")
            print(f"  ✓ Удалено сообщение #{drop_idx} @{dropped['role']}: {body_preview}...")
            print(f"    Бэкап: {backup.name} (восстановить: /undo)")
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
            print("[Финальный раунд — все агенты высказываются перед закрытием]\n")
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
        elif cmd == "cd":
            print(f"  {_C['yellow']}Вы имели в виду /cd {extra}?{_C['reset']}")
            continue
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

    # Определяем целевой TASKS.md — из _chat_cwd если задан, иначе cfg
    target_dir = _chat_cwd if _chat_cwd and _chat_cwd != cfg.root_dir else cfg.root_dir
    tasks_file = target_dir / cfg.tasks_file_rel
    if not tasks_file.exists():
        tasks_file.write_text(f"# TASKS — {topic}\n", encoding="utf-8")
        print(f"  Создан {tasks_file}")

    tasks = parse_tasks(tasks_file)
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
- **Интеграция**: что добавить при мерже (импорты, вызовы) или —
- **Описание**: что именно реализовать
- **Запрещено**: конкретные антипаттерны для этой задачи (из дискуссии). Пример: "не хардкодить ID", "не использовать networkidle". Если нет явных запретов — поставь "—"
- **Проверка**: команда верификации ДЛЯ ЭТОЙ ЗАДАЧИ. Пример: "ruff check src/new_file.py && pytest tests/test_new.py -v". Должна быть конкретной и запускаемой. Если неизвестна — "make check"
- **Критерий готовности**: бизнес-результат (что должно работать/измениться для пользователя)
- **Дискуссия**: {topic}
- **Агент**: —
- **Ветка**: —

Правила:
- Нумерация задач начинается с TASK-{next_task_num:03d}
- Если дискуссия привязана к задаче {linked_task_id} — первая подзадача ЗАМЕНЯЕТ её
- Задачи должны быть атомарными (одна задача = один файл/модуль)
- Зависимости: указывай ТОЛЬКО реальные — когда задача B использует код/файлы из задачи A. Если задачи работают с разными файлами и не зависят друг от друга — ставь "—". НЕ ставь линейную цепочку "каждая ждёт предыдущую" — это блокирует параллельное выполнение.
- Максимизируй параллелизм: чем больше задач на этапе 1 без зависимостей, тем лучше
- "Запрещено" — извлеки из дискуссии конкретные решения "не делать X". Это kill box для агента-исполнителя.
- "Проверка" — команда которую агент ОБЯЗАН запустить после реализации. Без зелёной проверки задача не считается выполненной.
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

    copy_file = filepath.parent / f"{topic}-tasks.md"
    copy_file.write_text(tasks_block + "\n", encoding="utf-8")

    # Очистка мусора из ответа Claude (может добавить пояснения перед задачами)
    clean_block = re.sub(r"^.*?(?=### TASK-)", "", tasks_block, flags=re.DOTALL)
    if not clean_block.strip():
        clean_block = tasks_block  # fallback если regex не нашёл

    # Fix single-line tasks: "### TASK-X: Name - **Статус**: open - ..." → multi-line
    def _fix_oneline_task(line: str) -> str:
        if line.startswith("### TASK-") and " - **" in line:
            parts = re.split(r" - (?=\*\*)", line, maxsplit=1)
            if len(parts) == 2:
                fields = re.split(r" - (?=\*\*)", parts[1])
                return parts[0] + "\n" + "\n".join(f"- {f}" for f in fields)
        return line

    fixed_lines = [_fix_oneline_task(line) for line in clean_block.split("\n")]
    clean_block = "\n".join(fixed_lines)

    # Validate generated tasks (phantom deps, duplicates, suspicious paths)
    from .decompose import validate_generated_tasks
    clean_block = validate_generated_tasks(clean_block, parse_tasks(tasks_file))

    # Renumber tasks to avoid duplicates, then insert — all under file lock
    from .tasks import tasks_file_lock, _atomic_write
    with tasks_file_lock():
        # Re-read current max task number (another process may have written)
        current_tasks = parse_tasks(tasks_file)
        actual_max = max((int(re.match(r"TASK-(\d+)", t.id).group(1))
                          for t in current_tasks if re.match(r"TASK-(\d+)", t.id)), default=0)
        # Renumber if needed (LLM used stale next_task_num)
        if actual_max >= next_task_num:
            task_ids_in_block = re.findall(r"TASK-(\d+)", clean_block)
            old_ids = sorted(set(task_ids_in_block), key=int)
            new_start = actual_max + 1
            for i, old_id in enumerate(old_ids):
                old_full = f"TASK-{old_id}"
                new_full = f"TASK-{new_start + i:03d}"
                clean_block = clean_block.replace(old_full, new_full)
            log.info(f"Перенумерованы задачи: {old_ids[0]}..{old_ids[-1]} → {new_start}..{new_start + len(old_ids) - 1}")

        # Insert directly (no nested lock — we already hold the file lock)
        content = tasks_file.read_text(encoding="utf-8")
        content = content.rstrip() + "\n\n" + clean_block.rstrip() + "\n"
        _atomic_write(tasks_file, content)

    print(f"\n  ✓ Задачи вставлены в {tasks_file}")
    print(f"  ✓ Копия: {copy_file}")
    log.info(f"{linked_task_id or topic}: подзадачи вставлены в {tasks_file}")
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
    _C["dim"]
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

    target_dir = _chat_cwd if _chat_cwd and _chat_cwd != cfg.root_dir else cfg.root_dir
    tasks_path = target_dir / cfg.tasks_file_rel
    if not tasks_path.exists():
        print(f"  {_C['yellow']}{tasks_path} не найден — используйте /ok или /resolve для генерации задач{_C['reset']}")
        return

    tasks = parse_tasks(tasks_path)
    all_tasks = [t for t in tasks]

    if not all_tasks:
        print(f"  {_C['yellow']}TASKS.md пуст — используйте /ok или /resolve для генерации задач{_C['reset']}")
        return

    tasks_text = tasks_path.read_text(encoding="utf-8")

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
    print("  Варианты:")
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
- **Запрещено**: антипаттерны для этой задачи (из дискуссии) или —
- **Проверка**: конкретная команда верификации (make check, pytest ..., ruff ...)
- **Критерий готовности**: бизнес-результат
- **Дискуссия**: {topic}
- **Агент**: —
- **Ветка**: —

Правила:
- Сохрани нумерацию существующих задач которые остаются (не перенумеровывай!)
- Новые задачи нумеруй начиная с TASK-{next_task_num:03d}
- Задачи помеченные ✗ — НЕ включай
- Задачи помеченные ✏ — включи с исправлениями
- Задачи помеченные + — добавь как новые
- "Запрещено" — извлеки из дискуссии конкретные "не делать X" для этой задачи
- "Проверка" — команда которую агент ОБЯЗАН запустить. Без зелёной проверки — задача не done
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
    backup = tasks_path.with_suffix(".md.bak")
    backup.write_text(tasks_text, encoding="utf-8")

    # Перезаписываем TASKS.md: заголовок + новые задачи
    header_match = re.match(r"^(#[^#].*?\n)", tasks_text)
    header = header_match.group(1) if header_match else f"# TASKS — {topic}\n"
    tasks_path.write_text(header + "\n" + clean_block.strip() + "\n", encoding="utf-8")

    print("\n  ✓ TASKS.md обновлён")
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

    print("[Извлекаю якоря техлида...]")
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
        print("[Якорей техлида не найдено]")
    print("[Генерирую сводку...]")
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


def _is_agent_cmd(cmd: str) -> bool:
    """Проверяет, является ли команда вызовом агента: /claude, /qwen+octagon и т.д."""
    if not cmd.startswith("/"):
        return False
    spec = cmd.lstrip("/")
    model_name = spec.split("+", 1)[0] if "+" in spec else spec
    return model_name in cfg.agent_names


def _parse_agent_specs(text: str) -> list[str]:
    """Парсит строку с агентами: 'gemini,claude' или 'gemini claude' или 'qwen+octagon gemini+arbiter'.

    Возвращает список agent specs если ВСЕ токены — валидные агенты.
    Возвращает [] если хотя бы один токен — не агент (значит это комментарий).
    """
    specs = []
    for tok in text.replace(",", " ").split():
        tok = tok.strip()
        if not tok:
            continue
        model_name = tok.split("+", 1)[0] if "+" in tok else tok
        if model_name not in cfg.agent_names:
            return []  # не агент — значит вся строка это комментарий
        specs.append(tok)
    return specs


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


_AUTO_COMPACT_THRESHOLD = 80_000  # символов (~20K токенов)


def _chat_agent_reply(filepath: Path, agent_spec: str):
    """Вызывает агента со стримингом текста по токенам.

    agent_spec может быть 'claude', 'qwen+octagon', 'gemini+arbiter' и т.д.
    """
    model_name, frame_content = resolve_agent_frame(agent_spec)
    discussion = filepath.read_text(encoding="utf-8")
    # Auto-compact если дискуссия слишком большая
    if len(discussion) > _AUTO_COMPACT_THRESHOLD:
        print(f"  {_C['yellow']}⚡ Дискуссия {len(discussion)//1000}K символов — автосжатие...{_C['reset']}")
        _chat_compact(filepath, keep_last=4)
        discussion = filepath.read_text(encoding="utf-8")

    cwd = _chat_cwd or cfg.root_dir
    # Claude CLI сам читает CLAUDE.md — не дублируем. Остальным инжектим project_docs.
    docs_section = ""
    if model_name != "claude" and cfg.project_docs:
        docs_section = f"\n## Документация проекта\n{cfg.project_docs}\n"
    frame_section = ""
    if frame_content:
        frame_section = f"\n## Твой когнитивный фрейм\n{frame_content}\n"
    # Display name: show default_frame if no explicit +frame
    if "+" not in agent_spec:
        acfg = cfg.agents.get(agent_spec)
        if acfg and acfg.default_frame:
            display_name = f"{agent_spec}+{acfg.default_frame}"
        else:
            display_name = agent_spec
    else:
        display_name = agent_spec
    prompt = f"""Ты участник дискуссии{' ' + cfg.discuss_context if cfg.discuss_context else ''}.
Твоя роль: @{display_name}.
Рабочая директория: {cwd}
Ты можешь читать файлы и изучать проекты в этой директории если это нужно для ответа.
{docs_section}{frame_section}
Прочитай дискуссию и напиши свой ответ.
Уровень ответа определяй по контексту дискуссии:
- Если обсуждаются концепции, стратегии, trade-offs — рассуждай на уровне принципов и альтернатив. НЕ прыгай к коду и реализации.
- Если обсуждается конкретная реализация — предлагай структуры, алгоритмы, примеры кода.
- Если не согласен — аргументируй с позиции trade-offs, а не личных предпочтений.
Отвечай кратко и по делу. Пиши на русском. Выведи ТОЛЬКО текст ответа, без заголовков и метаданных.
ВАЖНО: ты НЕ принимаешь решений. Только @techlead утверждает план и выносит резолюции.
Ты можешь предлагать и рекомендовать, но НИКОГДА не пиши "план утверждён", "решено", "фиксируем" и т.п.
{cfg.confidence_instruction}

--- ДИСКУССИЯ ---
{discussion}
--- КОНЕЦ ---
"""

    agent_type = model_name  # для выбора CLI команды
    acfg = cfg.agents.get(agent_type)
    if acfg is None:
        print(f"\n[ОШИБКА: агент '{agent_type}' не найден в конфиге]")
        return

    R = _C["reset"]
    color = _agent_color(agent_type)
    label = f"{color}{_C['bold']}{agent_type.capitalize()}{R}"

    # OpenAI-compatible API — call directly, no subprocess
    if acfg.protocol == "openai":
        from .agents import _call_openai_api
        start_time = time.time()
        print(f"{label}> {_C['dim']}думает...{R}", end="", flush=True)
        try:
            reply = _call_openai_api(acfg, prompt, acfg.inactivity_timeout or 300)
        except KeyboardInterrupt:
            print("\n[Прервано]")
            return
        elapsed = int(time.time() - start_time)
        print(f"\r{label}> {_C['dim']}думает... {elapsed}s{R}   ")
        if reply:
            for line in reply.split("\n"):
                print(_colorize_line(line), flush=True)
        else:
            reply = "(пустой ответ)"
        print()
    else:
        # CLI agents — subprocess with streaming
        if agent_type == "claude":
            cmd = [acfg.command, "-p", "-", "--output-format", "text", "--permission-mode", "auto"]
        elif agent_type == "qwen":
            cmd = [acfg.command, "-p", "--output-format", "text", "--approval-mode", "yolo"]
        elif acfg.command == "goose":
            # Extract --model and --provider from agent args, override output to text
            goose_model = "meta/llama-3.3-70b-instruct"
            goose_provider = "openai"
            for j, a in enumerate(acfg.args):
                if a == "--model" and j + 1 < len(acfg.args):
                    goose_model = acfg.args[j + 1]
                elif a == "--provider" and j + 1 < len(acfg.args):
                    goose_provider = acfg.args[j + 1]
            cmd = [acfg.command, "run", "-i", "/dev/stdin", "--output-format", "text",
                   "--provider", goose_provider, "--model", goose_model,
                   "--no-profile"]  # no extensions for discuss/review
        else:
            cmd = [acfg.command, "-p", "", "--output-format", "text"]

        # Build subprocess env (for goose: OPENAI_HOST, OPENAI_API_KEY)
        proc_env = None
        if acfg.env:
            proc_env = {**os.environ, **acfg.env}

        reply_lines = []
        start_time = time.time()
        try:
            proc = subprocess.Popen(
                cmd, cwd=_chat_cwd or cfg.root_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.PIPE, text=True, bufsize=1, env=proc_env,
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

    _print_confidence(reply, display_name)
    reply = re.sub(r"\n?CONFIDENCE:\s*\d+\s*%\s*$", "", reply).rstrip()

    _chat_append(filepath, display_name, reply)


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

        # OpenAI-compatible API — direct HTTP call
        if acfg.protocol == "openai":
            from .agents import _call_openai_api
            try:
                text = _call_openai_api(acfg, prompt, acfg.inactivity_timeout or 300)
                results[name] = text or ""
                if not text:
                    errors[name] = "пустой ответ от API"
            except Exception as e:
                errors[name] = str(e)[:300]
            return

        # CLI agents — subprocess
        if name == "claude":
            cmd = [acfg.command, "-p", "-", "--output-format", "text", "--permission-mode", "auto"]
        elif name == "qwen":
            cmd = [acfg.command, "-p", "--output-format", "text", "--approval-mode", "yolo"]
        else:
            cmd = [acfg.command, "-p", "", "--output-format", "text"]

        solo_cwd = "/tmp"
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
        print("\n[Прервано]")
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
            _print_confidence(reply, name)
            reply = re.sub(r"\n?CONFIDENCE:\s*\d+\s*%\s*$", "", reply).rstrip()
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
        # solo/fresh запускаются из /tmp чтобы агент не видел файлы проекта
        solo_cwd = "/tmp"
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
    full_prompt = f"""ЗАДАНИЕ: {prompt}

Контекст дискуссии (для справки, без хода обсуждения):
{intro}

НАПОМИНАНИЕ — твоё задание: {prompt}
Отвечай СТРОГО на задание. Если это фактический вопрос — ответь кратко. Не развивай тему дискуссии, не предлагай свой взгляд на парадоксы, не вступай в полемику — только ответ на задание.
Отвечай на русском."""
    _chat_solo_reply(filepath, agent_type, full_prompt, tag="fresh")


def _colorize_line(line: str) -> str:
    """Подсвечивает inline markdown: **bold**, `code`, @agent."""
    R = _C["reset"]
    BOLD = _C["bold"]
    CYAN = _C["cyan"]
    _C["green"]
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


def _print_help_detail(topic: str):
    """Подробная справка по команде или фрейму."""
    R = _C["reset"]
    DIM = _C["dim"]
    B = _C["bold"]
    _C["yellow"]

    # Фреймы
    if topic in cfg.frames:
        f = cfg.frames[topic]
        print(f"\n  {B}+{topic}{R} — {f.description}")
        if f.content:
            print(f"  {DIM}{'─' * 50}{R}")
            for line in f.content.strip().splitlines():
                print(f"  {line}")
        print()
        return

    # agent+frame → redirect на фрейм
    if "+" in topic:
        frame_part = topic.split("+", 1)[1]
        if frame_part in cfg.frames:
            _print_help_detail(frame_part)
            return

    # Конкретный агент
    if topic in cfg.agent_names:
        acfg = cfg.agents[topic]
        frame_examples = ", ".join(f"/{topic}+{f}" for f in list(cfg.frames)[:3]) if cfg.frames else ""
        print(f"\n  {B}/{topic}{R} {DIM}[текст]{R}")
        print(f"  {DIM}{'─' * 50}{R}")
        print(f"  Вызвать {topic}. Текст — комментарий @techlead перед вызовом.")
        print(f"  Команда: {acfg.command}")
        print(f"  Таймаут: {acfg.inactivity_timeout}s")
        if frame_examples:
            print(f"  С фреймами: {frame_examples}")
        print()
        return

    # Команды
    details = {
        "all": (
            "/all [агенты]",
            "Вызвать всех enabled агентов последовательно.\n"
            "Без аргументов — порядок из конфига.\n"
            "С аргументами — явный порядок: /all gemini,claude\n"
            "Поддерживает фреймы: /all qwen+audit,qwen+wild",
        ),
        "both": (
            "/both [текст]",
            "Вызвать claude и gemini последовательно.\n"
            "Текст добавляется как комментарий @techlead перед вызовом.",
        ),
        "solo": (
            "/solo <агенты> <промпт>",
            "Агенты получают ТОЛЬКО промпт, без контекста дискуссии.\n"
            "Несколько агентов — параллельно. Работают в /tmp.\n"
            "Пример: /solo qwen+audit,qwen+wild оцени архитектуру X",
        ),
        "fresh": (
            "/fresh <агенты> <промпт>",
            "Агенты получают интро дискуссии + промпт (без хода обсуждения).\n"
            "Несколько агентов — параллельно.\n"
            "Пример: /fresh gemini,claude ответь на 5 вопросов из вводной",
        ),
        "ok": (
            "/ok [текст]",
            "Финализация дискуссии:\n"
            "1. Добавляет комментарий (если есть)\n"
            "2. Все агенты высказывают финальные замечания\n"
            "3. Генерируется резолюция\n"
            "4. Из резолюции создаются задачи в TASKS.md\n"
            "   (с полями Запрещено и Проверка из дискуссии)",
        ),
        "resolve": (
            "/resolve [текст]",
            "Ручная резолюция без финального раунда агентов.\n"
            "Текст становится резолюцией, затем генерируются задачи.",
        ),
        "reset": (
            "/reset",
            "Сбрасывает дискуссию к интро (первое сообщение @techlead).\n"
            "Все ответы агентов удаляются. Бэкап → .bak\n"
            "Восстановить: /undo",
        ),
        "compact": (
            "/compact [N]",
            "Сжимает ранние сообщения в сводку через LLM.\n"
            "Оставляет последние N сообщений (по умолчанию 4).\n"
            "Полезно когда дискуссия > 80K символов (auto-compact).\n"
            "Восстановить: /undo",
        ),
        "show": (
            "/show [N]",
            "Показать дискуссию через пейджер (less).\n"
            "Без аргументов — вся дискуссия.\n"
            "С числом — последние N сообщений.",
        ),
        "stats": (
            "/stats",
            "Размер дискуссии в символах и токенах,\n"
            "список участников, количество сообщений.",
        ),
        "summary": (
            "/summary",
            "LLM-саммари дискуссии без закрытия.\n"
            "Выводит в терминал, не записывает в файл.",
        ),
        "drop": (
            "/drop <N | -N | agent [K]>",
            "Удалить конкретное сообщение из дискуссии.\n"
            "/drop 3       — удалить сообщение #3\n"
            "/drop -1      — удалить последнее сообщение\n"
            "/drop claude   — удалить последнее сообщение от claude\n"
            "/drop gemini 2 — удалить предпоследнее от gemini\n"
            "Создаёт бэкап. Восстановить: /undo",
        ),
        "undo": (
            "/undo",
            "Восстановить дискуссию из .bak.\n"
            "Работает после /reset, /compact, /drop, /tasks.",
        ),
        "cd": (
            "/cd <path>",
            "Сменить рабочую директорию агентов.\n"
            "Агенты смогут читать файлы из этой директории.\n"
            "Без аргументов — показать текущую.",
        ),
        "tasks": (
            "/tasks",
            "Ревью задач vs дискуссия: LLM сравнивает текущие\n"
            "задачи с ходом дискуссии, предлагает правки.\n"
            "После ревью — интерактивная генерация обновлённых задач.",
        ),
        "reopen": (
            "/reopen [причина]",
            "Переоткрыть закрытую дискуссию.\n"
            "Все агенты критикуют предыдущую резолюцию.",
        ),
        "frames": (
            "Когнитивные фреймы",
            "Фреймы — линзы мышления. Модель + фрейм = агент-специалист.\n"
            "Синтаксис: /agent+frame (например /qwen+audit)\n"
            "Одна модель с разными фреймами даёт разные ответы.\n\n"
            "Доступные фреймы: " + ", ".join(f"+{n}" for n in cfg.frames) + "\n"
            "Подробнее о фрейме: /help <имя_фрейма>",
        ),
        "scenarios": (
            "Сценарии дискуссий",
            "Готовые цепочки фреймов:\n\n"
            "  проверь → сломай → оцени:\n"
            "    /agent+audit → /agent+wild → /agent+price\n\n"
            "  глубокий разбор:\n"
            "    /agent+optimizer → /agent+meta → /agent+audit\n\n"
            "  дебаты (два агента):\n"
            "    /agent1+theory → /agent2+evidence → /agent1+price",
        ),
    }

    if topic in details:
        title, body = details[topic]
        print(f"\n  {B}{title}{R}")
        print(f"  {DIM}{'─' * 50}{R}")
        for line in body.splitlines():
            print(f"  {line}")
        print()
        return

    # Fallback: подсказка
    available = sorted(details.keys())
    frame_list = list(cfg.frames.keys())
    agent_list = cfg.agent_names
    print(f"  {_C['red']}Неизвестная тема: {topic}{R}")
    print(f"  {DIM}Команды: {', '.join(available)}{R}")
    if frame_list:
        print(f"  {DIM}Фреймы:  {', '.join(frame_list)}{R}")
    if agent_list:
        print(f"  {DIM}Агенты:  {', '.join(agent_list)}{R}")


def _print_chat_help():
    """Справка по командам чата."""
    R = _C["reset"]
    DIM = _C["dim"]
    Y = _C["yellow"]
    G = _C["green"]
    B = _C["bold"]

    # Генерируем список агентов из конфига
    agent_lines = []
    for name in cfg.agent_names:
        color = _agent_color(name)
        agent_lines.append(
            (f"{Y}/{name}{R} {DIM}[текст]{R}", len(f"/{name} [текст]"),
             f"[комментарий +] ответ {color}{name.capitalize()}{R}")
        )

    # Фреймы
    frame_names = list(cfg.frames.keys()) if cfg.frames else []
    frame_hint = ""
    if frame_names:
        examples = [f"/{cfg.agent_names[0]}+{frame_names[0]}"] if cfg.agent_names and frame_names else []
        if len(frame_names) > 1 and len(cfg.agent_names) > 1:
            examples.append(f"/{cfg.agent_names[1]}+{frame_names[1]}")
        frame_hint = f" {DIM}({', '.join(examples)}){R}" if examples else ""

    SEP = None
    HEADER = "HEADER"  # маркер заголовка группы

    cmds = [
        # --- Вызов агентов ---
        (HEADER, 0, f"{B}Вызов агентов:{R}"),
        (f"{DIM}(текст){R}", 8, "ваш комментарий в дискуссию"),
        *agent_lines,
        (f"{Y}/both{R} {DIM}[текст]{R}", len("/both [текст]"), "claude + gemini последовательно"),
        (f"{Y}/all{R} {DIM}[агенты]{R}", len("/all [агенты]"), "все агенты, или явный порядок: /all gemini,claude"),
        SEP,
        # --- Фреймы ---
        (HEADER, 0, f"{B}Когнитивные фреймы{R} {DIM}(модель+фрейм):{R}"),
        (f"{Y}/agent+frame{R}", len("/agent+frame"),
         f"вызвать модель с фреймом{frame_hint}"),
    ]
    M = _C["magenta"]
    for fname in frame_names:
        fdesc = cfg.frames[fname].description or fname
        cmds.append((f"{M}+{fname}{R}", len(f"+{fname}"), f"{DIM}{fdesc}{R}"))
    # Сценарии — динамические, на основе enabled агентов
    if frame_names and cfg.agent_names:
        a = cfg.agent_names[0]  # первый доступный агент
        scenarios = []
        if "audit" in frame_names and "wild" in frame_names and "price" in frame_names:
            scenarios.append(f"/{a}+audit → /{a}+wild → /{a}+price")
        if "optimizer" in frame_names and "meta" in frame_names and "audit" in frame_names:
            scenarios.append(f"/{a}+optimizer → /{a}+meta → /{a}+audit")
        if "theory" in frame_names and "evidence" in frame_names and len(cfg.agent_names) > 1:
            b = cfg.agent_names[1]
            scenarios.append(f"/{a}+theory → /{b}+evidence → /{a}+price")
        if scenarios:
            cmds.append(SEP)
            cmds.append((HEADER, 0, f"{B}Сценарии:{R}"))
            labels = ["проверь → сломай → оцени", "глубокий разбор", "дебаты"]
            for i, sc in enumerate(scenarios):
                label = labels[i] if i < len(labels) else ""
                cmds.append((f"{Y}{label}{R}", len(label), f"{DIM}{sc}{R}"))
    cmds += [
        SEP,
        # --- Спецрежимы ---
        (HEADER, 0, f"{B}Спецрежимы:{R}"),
        (f"{Y}/solo{R} {DIM}<агенты> <промпт>{R}", len("/solo <агенты> <промпт>"),
         "параллельно, без контекста дискуссии"),
        (f"{Y}/fresh{R} {DIM}<агенты> <промпт>{R}", len("/fresh <агенты> <промпт>"),
         "параллельно, только интро + промпт"),
        SEP,
        # --- Управление контекстом ---
        (HEADER, 0, f"{B}Контекст:{R}"),
        (f"{Y}/show{R} {DIM}[N]{R}", len("/show [N]"), "вся дискуссия или последние N сообщений"),
        (f"{Y}/stats{R}", 6, "размер, токены, участники"),
        (f"{Y}/summary{R}", 8, "саммари дискуссии (без закрытия)"),
        (f"{Y}/compact{R} {DIM}[N]{R}", len("/compact [N]"), "сжать ранние сообщения (оставить последние N)"),
        (f"{Y}/drop{R} {DIM}<N|agent>{R}", len("/drop <N|agent>"), "удалить конкретное сообщение"),
        (f"{Y}/reset{R}", 6, "сбросить к интро, бэкап → .bak"),
        (f"{Y}/undo{R}", 5, "восстановить из .bak"),
        (f"{Y}/cd{R} {DIM}<path>{R}", len("/cd <path>"), "сменить рабочую директорию агентов"),
        SEP,
        # --- Жизненный цикл ---
        (HEADER, 0, f"{B}Закрытие:{R}"),
        (f"{Y}/tasks{R}", 6, "ревью задач vs дискуссия → правки → перегенерация"),
        (f"{G}/ok{R} {DIM}[текст]{R}", len("/ok [текст]"), "одобрить и закрыть (резолюция + задачи)"),
        (f"{Y}/resolve{R} {DIM}[текст]{R}", len("/resolve [текст]"), "резолюция вручную + задачи"),
        (f"{Y}/reopen{R} {DIM}[причина]{R}", len("/reopen [причина]"), "переоткрыть (агенты критикуют резолюцию)"),
        SEP,
        (f"{Y}/help{R} {DIM}[команда]{R}", len("/help [команда]"), "справка или подробности по команде/фрейму"),
        (f"{_C['red']}/exit{R}", 5, "выйти без резолюции"),
    ]

    col = max(v for c in cmds if c is not None and c != HEADER and c[0] != HEADER
              for _, v, _ in [c]) + 2
    for entry in cmds:
        if entry is None:
            print()
            continue
        colored, vlen, desc = entry
        if colored == HEADER:
            print(f"  {desc}")
            continue
        pad = " " * max(col - vlen, 1)
        print(f"    {colored}{pad}{DIM}—{R} {desc}")
    print(f"\n  {DIM}Tab — автодополнение команд. /help <команда> — подробности.{R}")


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
