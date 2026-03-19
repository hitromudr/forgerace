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


def show_status():
    """Показывает статус всех задач."""
    tasks = parse_tasks()
    if not tasks:
        hint = run_hint().rsplit(" ", 1)[0]
        print(f"\n  {C['dim']}📋 Нет задач в TASKS.md. Подсказка:{R} {hint} run\n")
        return

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

    # merge-pending
    sub.add_parser("merge-pending", help="Промержить review-задачи в develop")

    # status
    sub.add_parser("status", help="Статус задач")

    args = parser.parse_args()

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
        parser.print_help()
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

    log.info("ForgeRace завершён")


def main_with_signal_handling():
    """Entry point с обработкой сигналов."""
    def _force_exit(*_):
        print("\nПрервано. Убиваю дочерние процессы...")
        try:
            os.killpg(os.getpgid(os.getpid()), 9)
        except ProcessLookupError:
            pass
        os._exit(1)

    os.setpgrp()
    signal.signal(signal.SIGINT, _force_exit)
    signal.signal(signal.SIGTERM, _force_exit)
    main()
