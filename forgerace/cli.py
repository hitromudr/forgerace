"""CLI точка входа: argparse, команды run/discuss/status/merge-pending."""

import argparse
import logging
import os
import signal
import sys
from pathlib import Path

from .config import cfg, init_config, run_hint
from .discuss import discuss_chat, discuss_create, discuss_list, discuss_reply, discuss_show
from .merge import ensure_develop_branch, merge_to_develop
from .utils import C, R, agent_color
from .pipeline import run_pipeline
from .tasks import parse_tasks, update_task_status
from .utils import log, run_cmd, setup_logging


_INIT_TOML = '''# ForgeRace configuration
# Docs: https://github.com/hitromudr/forgerace

[project]
name = "{name}"
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

[agents.qwen]
command = "qwen"
args = ["-p", "--approval-mode", "yolo", "--output-format", "stream-json"]
review_args = ["-p", "-", "--approval-mode", "yolo"]
inactivity_timeout = 180

[build]
commands = []
check_command = ""

[limits]
max_parallel_tasks = 10
agent_timeout = 900
max_review_rounds = 3
'''

_INIT_TASKS = '''# TASKS — {name}
'''


def _cmd_init():
    """Создаёт forgerace.toml и TASKS.md в текущей директории."""
    cwd = Path.cwd()
    name = cwd.name

    toml_path = cwd / "forgerace.toml"
    tasks_path = cwd / "TASKS.md"

    created = []
    if toml_path.exists():
        print(f"  {C['dim']}forgerace.toml уже существует{R}")
    else:
        toml_path.write_text(_INIT_TOML.format(name=name), encoding="utf-8")
        created.append("forgerace.toml")

    if tasks_path.exists():
        print(f"  {C['dim']}TASKS.md уже существует{R}")
    else:
        tasks_path.write_text(_INIT_TASKS.format(name=name), encoding="utf-8")
        created.append("TASKS.md")

    # Создаём обёртку fr для короткого вызова
    import sys
    forgerace_py = Path(sys.argv[0]).resolve()
    fr_path = cwd / "fr"
    if not fr_path.exists():
        fr_path.write_text(f"#!/bin/sh\nexec python3 {forgerace_py} \"$@\"\n", encoding="utf-8")
        fr_path.chmod(0o755)
        created.append("fr")

    if created:
        print(f"  {C['green']}✓ Создано: {', '.join(created)}{R}")
    print(f"\n  Теперь:")
    print(f"    {C['bold']}vim forgerace.toml{R}     — настрой build-команды и агентов")
    print(f"    {C['bold']}./fr discuss new ...{R}  — запусти дискуссию → /ok → задачи")
    print(f"    {C['bold']}./fr run{R}              — запусти агентов")
    print(f"    {C['bold']}./fr help{R}             — все команды")


def show_status():
    """Показывает статус всех задач + граф зависимостей."""
    tasks = parse_tasks()
    if not tasks:
        hint = run_hint().rsplit(" ", 1)[0]
        print(f"\n  {C['dim']}📋 Нет задач в TASKS.md. Подсказка:{R} {hint} run\n")
        return

    task_map = {t.id: t for t in tasks}
    done_ids = {t.id for t in tasks if t.status == "done"}

    status_groups = {}
    for t in tasks:
        s = t.status.split(":")[0] or "unknown"
        status_groups.setdefault(s, []).append(t)

    status_styles = {
        "done":        ("✓", C["green"]),
        "review":      ("⏳", C["yellow"]),
        "in_progress": ("▶", C["cyan"]),
        "open":        ("○", C["white"]),
        "blocked":     ("✗", C["dim"]),
        "failed":      ("❌", C["red"]),
        "unknown":     ("?", C["dim"]),
    }
    for status in ["done", "review", "in_progress", "open", "blocked", "failed", "unknown"]:
        group = status_groups.get(status, [])
        if not group:
            continue
        icon, color = status_styles.get(status, ("?", ""))
        print(f"\n{color}{icon} {status.upper()} ({len(group)}):{R}")
        for t in group:
            a = t.agent if t.agent and t.agent != "—" else ""
            agent_info = f" {agent_color(a)}[{a}]{R}" if a else ""
            deps_info = f" {C['dim']}(ждёт: {', '.join(t.deps)}){R}" if t.deps and status == "open" else ""
            print(f"    {C['bold']}{t.id}{R}: {t.name}{agent_info}{deps_info}")

    # Граф зависимостей для незавершённых задач
    active = [t for t in tasks if t.status != "done"]
    if active:
        print(f"\n{C['yellow']}{C['bold']}Граф зависимостей:{R}")

        # Найдём задачи без незавершённых зависимостей (готовы к запуску)
        ready_ids = set()
        for t in active:
            unmet = [d for d in t.deps if d not in done_ids]
            if not unmet:
                ready_ids.add(t.id)

        # Кто от кого зависит (обратный граф — кто разблокируется)
        unlocks: dict[str, list[str]] = {}
        for t in active:
            for d in t.deps:
                if d not in done_ids:
                    unlocks.setdefault(d, []).append(t.id)

        # Рисуем дерево от корней (задачи без незавершённых зависимостей)
        printed = set()

        def _print_tree(tid: str, indent: int = 0):
            if tid in printed:
                return
            printed.add(tid)
            t = task_map.get(tid)
            if not t or t.status == "done":
                return
            prefix = "  " + "│ " * indent
            s = t.status.split(":")[0]
            icon, color = status_styles.get(s, ("?", ""))
            ready_marker = f" {C['green']}◀ ready{R}" if tid in ready_ids and s == "open" else ""
            print(f"{prefix}{color}{icon}{R} {C['bold']}{tid}{R}: {t.name}{ready_marker}")
            for child in unlocks.get(tid, []):
                connector = "  " + "│ " * indent + "├─"
                # Не печатаем connector отдельно — он часть дочернего вызова
                _print_tree(child, indent + 1)

        # Начинаем с корней (нет незавершённых зависимостей)
        roots = [t.id for t in active if not any(d not in done_ids for d in t.deps)]
        orphans = [t.id for t in active if t.id not in roots and t.id not in {c for kids in unlocks.values() for c in kids}]

        for root in roots:
            _print_tree(root)
        for orph in orphans:
            _print_tree(orph)

    print()


def merge_pending_tasks():
    """Мержит все review-задачи в develop."""
    ensure_develop_branch()
    tasks = parse_tasks()
    review_tasks = [t for t in tasks if t.status.startswith("review:")]

    if not review_tasks:
        print("Нет задач в статусе review.")
        return

    for t in review_tasks:
        branch = t.branch
        if not branch or branch == "—":
            print(f"  ⚠ {t.id} ({t.name}) — нет ветки, пропускаю")
            continue

        result = run_cmd(["git", "branch", "--list", branch], cwd=cfg.root_dir, check=False)
        if not result.stdout.strip():
            print(f"  ⚠ {t.id} — ветка {branch} не найдена, пропускаю")
            continue

        print(f"  → Мержу {t.id} ({t.name}): {branch} → {cfg.dev_branch}...")
        if merge_to_develop(branch, t.id):
            update_task_status(t.id, "done")
            print(f"  ✓ {t.id} → done")
        else:
            print(f"  ✗ {t.id} — конфликт, нужен ручной мерж")

    print()
    show_status()


def _cmd_agents_list():
    """Показывает всех агентов и их статус."""
    for name, acfg in cfg.agents.items():
        status = f"{C['green']}ON{R}" if acfg.enabled else f"{C['red']}OFF{R}"
        print(f"  {C['bold']}{name}{R}: {status}  ({acfg.command})")
    print(f"\n  Активные: {C['bold']}{cfg.agent_names}{R}")


def _cmd_agent_toggle(agent_name: str, enable: bool):
    """Включает/выключает агента в forgerace.toml."""
    if agent_name not in cfg.agents:
        print(f"  {C['red']}Агент '{agent_name}' не найден. Доступные: {list(cfg.agents.keys())}{R}")
        return

    toml_path = cfg.root_dir / "forgerace.toml"
    if not toml_path.exists():
        print(f"  {C['red']}forgerace.toml не найден{R}")
        return

    content = toml_path.read_text(encoding="utf-8")
    section = f"[agents.{agent_name}]"
    if section not in content:
        print(f"  {C['red']}Секция {section} не найдена в forgerace.toml{R}")
        return

    # Ищем enabled в секции агента или добавляем
    lines = content.splitlines()
    section_idx = next(i for i, l in enumerate(lines) if l.strip() == section)

    # Найдём конец секции (следующая [секция] или EOF)
    end_idx = len(lines)
    for i in range(section_idx + 1, len(lines)):
        if lines[i].strip().startswith("["):
            end_idx = i
            break

    # Ищем enabled = ... в секции
    enabled_idx = None
    for i in range(section_idx + 1, end_idx):
        if lines[i].strip().startswith("enabled"):
            enabled_idx = i
            break

    value = "true" if enable else "false"
    if enabled_idx is not None:
        lines[enabled_idx] = f"enabled = {value}"
    else:
        # Вставляем перед концом секции
        lines.insert(end_idx, f"enabled = {value}")

    toml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Перечитаем конфиг
    init_config(config_path=toml_path)

    action = "включён" if enable else "выключен"
    color = C['green'] if enable else C['red']
    print(f"  {color}{agent_name} {action}{R}")
    print(f"  Активные: {C['bold']}{cfg.agent_names}{R}")


def main():
    parser = argparse.ArgumentParser(
        description="ForgeRace — мультиагентный оркестратор",
        usage="forgerace [--config CONFIG] [--root ROOT] [--verbose] <command> ...",
    )
    parser.add_argument("--config", type=Path, help="Путь к forgerace.toml")
    parser.add_argument("--root", type=Path, help="Корневая директория проекта")
    parser.add_argument("--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", title="commands", metavar="")
    _orig_format_help = parser.format_help
    parser.format_help = lambda: _orig_format_help().replace("commands:\n  \n", "commands:\n")

    # run
    run_p = sub.add_parser("run", help="Запустить задачи из TASKS.md")
    run_p.add_argument("--task", help="Конкретная задача (TASK-032)")
    run_p.add_argument("--retry", action="store_true", help="Перезапустить упавшие")
    run_p.add_argument("--dry-run", action="store_true")
    run_p.add_argument("--auto", action="store_true", help="Автозапуск разблокированных")
    run_p.add_argument("--max-tasks", type=int, default=None,
                        help="Макс. задач параллельно")

    # discuss
    disc_p = sub.add_parser("discuss", help="Управление дискуссиями")
    disc_sub = disc_p.add_subparsers(dest="disc_cmd")

    disc_new = disc_sub.add_parser("new", help="Создать дискуссию")
    disc_new.add_argument("topic", help="Имя темы")
    disc_new.add_argument("question", help="Вопрос / начальное сообщение")
    disc_new.add_argument("--author", default="techlead")

    disc_reply = disc_sub.add_parser("reply", help="Агент отвечает в дискуссии")
    disc_reply.add_argument("topic", help="Имя темы")
    disc_reply.add_argument("--agent", required=True)

    disc_sub.add_parser("list", help="Список дискуссий")

    disc_show = disc_sub.add_parser("show", help="Показать дискуссию")
    disc_show.add_argument("topic", help="Имя темы")

    disc_chat_p = disc_sub.add_parser("chat", help="Интерактивный чат")
    disc_chat_p.add_argument("topic", help="Имя темы")

    disc_regen = disc_sub.add_parser("regen", help="Перегенерировать задачи из дискуссии")
    disc_regen.add_argument("topic", help="Имя темы")

    # agents
    agents_p = sub.add_parser("agents", help="Управление агентами (вкл/выкл/список)")
    agents_sub = agents_p.add_subparsers(dest="agents_cmd")
    agents_sub.add_parser("list", help="Показать агентов и их статус")
    agents_on = agents_sub.add_parser("on", help="Включить агента")
    agents_on.add_argument("agent_name", help="Имя агента (claude, gemini, qwen)")
    agents_off = agents_sub.add_parser("off", help="Выключить агента")
    agents_off.add_argument("agent_name", help="Имя агента (claude, gemini, qwen)")

    # init
    sub.add_parser("init", help="Создать forgerace.toml и TASKS.md в текущей директории")

    # merge-pending
    sub.add_parser("merge-pending", help="Промержить review-задачи в develop")

    # status
    sub.add_parser("status", help="Статус задач")

    # help
    sub.add_parser("help", help="Показать справку")

    args = parser.parse_args()

    if args.command == "help" or args.command is None:
        parser.print_help()
        return

    # init — создаёт файлы в CWD, --config не имеет смысла
    if args.command == "init":
        if args.config:
            print(f"  {C['yellow']}--config игнорируется для init (файлы создаются в текущей директории){R}")
        _cmd_init()
        return

    # Инициализация конфига
    # --root имеет приоритет; если не указан — TOML root; если и его нет — CWD
    init_config(config_path=args.config, root_dir=args.root)
    setup_logging(verbose=args.verbose)

    # Дискуссии
    if args.command == "discuss":
        if args.disc_cmd == "new":
            discuss_create(args.topic, args.question, args.author)
        elif args.disc_cmd == "reply":
            discuss_reply(args.topic, args.agent)
        elif args.disc_cmd == "list":
            discuss_list()
        elif args.disc_cmd == "show":
            discuss_show(args.topic)
        elif args.disc_cmd == "chat":
            discuss_chat(args.topic)
        elif args.disc_cmd == "regen":
            from .discuss import _post_resolve
            filepath = cfg.discuss_dir / f"{args.topic}.md"
            if not filepath.exists():
                log.error(f"Дискуссия {args.topic} не найдена")
            elif "РЕЗОЛЮЦИЯ" not in filepath.read_text(encoding="utf-8"):
                log.error(f"Дискуссия {args.topic} не закрыта (нет РЕЗОЛЮЦИИ)")
            else:
                _post_resolve(filepath)
        else:
            disc_p.print_help()
        return

    # agents
    if args.command == "agents":
        if args.agents_cmd == "list" or args.agents_cmd is None:
            _cmd_agents_list()
        elif args.agents_cmd == "on":
            _cmd_agent_toggle(args.agent_name, True)
        elif args.agents_cmd == "off":
            _cmd_agent_toggle(args.agent_name, False)
        return

    # merge-pending
    if args.command == "merge-pending":
        merge_pending_tasks()
        return

    # status
    if args.command == "status":
        show_status()
        return

    # run
    if args.command != "run":
        return

    max_tasks = args.max_tasks or cfg.max_parallel_tasks
    log.info("=" * 60)
    log.info("ForgeRace запущен")
    log.info(f"Корень: {cfg.root_dir}")
    log.info(f"Агенты: {cfg.agent_names}")
    log.info(f"Макс. задач: {max_tasks}")
    log.info("=" * 60)

    run_pipeline(
        specific_task=getattr(args, "task", None),
        dry_run=getattr(args, "dry_run", False),
        max_tasks=max_tasks,
        retry=getattr(args, "retry", False),
        auto=getattr(args, "auto", False),
    )

    # os._exit(0) вызывается внутри run_pipeline


def _restore_terminal():
    """Восстанавливает терминал после агентов, которые могут сломать stty."""
    os.system("stty sane 2>/dev/null")


def main_with_signal_handling():
    """Entry point с обработкой сигналов."""
    def _force_exit(*_):
        print("\nПрервано. Убиваю дочерние процессы...")
        _restore_terminal()
        try:
            os.killpg(os.getpgid(os.getpid()), 9)
        except ProcessLookupError:
            pass
        os._exit(1)

    # setpgrp + custom SIGINT только для run (discuss нужен нормальный Ctrl+C)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "run":
        signal.signal(signal.SIGINT, _force_exit)
        signal.signal(signal.SIGTERM, _force_exit)
        os.setpgrp()  # после handler — чтобы SIGINT между ними не потерялся
    try:
        main()
    finally:
        _restore_terminal()
