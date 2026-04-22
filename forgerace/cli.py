"""CLI точка входа: argparse, команды run/discuss/status/merge-pending."""

import argparse
import os
import signal
import sys
# Import error‑path test runner (optional integration)
try:
    from .test_error_paths import run_error_path_tests
    # If the benchmark is invoked with a special flag, run the tests.
    if "--test-error-paths" in sys.argv:
        exit_code = run_error_path_tests()
        sys.exit(exit_code)
except Exception:
    # Silently ignore if the test module is not present or any import error occurs.
    pass
from pathlib import Path

from .config import cfg, init_config, run_hint
from .discuss import discuss_chat, discuss_create, discuss_list, discuss_reply, discuss_show
from .merge import merge_to_develop
from .utils import C, R, agent_color
from .pipeline import run_pipeline
from .tasks import parse_tasks, update_task_status
from .types import MergeResult
from .decompose import create_checkpoint_task
from .utils import log, run_cmd, setup_logging
from typing import Optional
from .benchmark import BenchmarkStore
# web_server, checkpoint, diagnose_engine — created by agents, not yet merged
# from .web_server import create_web_server


_AGENT_CONFIGS = {
    "claude": {
        "command": "claude",
        "args": '["-p", "--allowedTools", "Read,Write,Edit,Bash,Grep,Glob,WebFetch,WebSearch",\n'
                '        "--max-turns", "50", "--output-format", "stream-json", "--verbose"]',
        "review_args": '["-p", "-", "--output-format", "text", "--permission-mode", "auto"]',
        "timeout": 300,
    },
    "gemini": {
        "command": "gemini",
        "args": '["-p", "--approval-mode", "yolo", "--output-format", "stream-json"]',
        "review_args": '["-p", "-"]',
        "timeout": 180,
    },
    "qwen": {
        "command": "qwen",
        "args": '["-p", "--approval-mode", "yolo", "--output-format", "stream-json"]',
        "review_args": '["-p", "-", "--approval-mode", "yolo"]',
        "timeout": 180,
    },
}


def _build_init_toml(name: str, detected_agents: list[str], has_frames: bool) -> str:
    """Генерирует forgerace.toml с автодетектом агентов и фреймами."""
    lines = [
        '# ForgeRace configuration',
        '# Docs: https://github.com/hitromudr/forgerace',
        '',
        '[project]',
        f'name = "{name}"',
        'root = "."',
        '# dev_branch = "main"  # default: current branch',
        'discuss_dir = "docs/discuss"',
    ]

    # Frames
    if has_frames:
        lines += [
            '',
            '# ── Когнитивные фреймы ──────────────────────────────────────',
            '# /agent+frame в дискуссиях: /qwen+audit, /gemini+wild ...',
            '# Сценарий: /qwen+audit → /qwen+wild → /qwen+price',
            '',
            '[frames.audit]',
            'description = "8 осей выживаемости решения"',
            'file = "frames/audit.md"',
            '',
            '[frames.wild]',
            'description = "дикие стратегии, инверсия, слом шаблонов"',
            'file = "frames/wild.md"',
            '',
            '[frames.price]',
            'description = "цена выбора, trade-off матрицы, арбитраж"',
            'file = "frames/price.md"',
            '',
            '[frames.theory]',
            'description = "от принципов к реализации"',
            'file = "frames/theory.md"',
            '',
            '[frames.evidence]',
            'description = "от фактов к обобщению"',
            'file = "frames/evidence.md"',
            '',
            '[frames.optimizer]',
            'description = "4-фазный разбор: grounding → divergence → red-teaming"',
            'file = "frames/optimizer.md"',
            '',
            '[frames.meta]',
            'description = "13 фреймворков: диалектика, теория игр, стоицизм..."',
            'file = "frames/meta.md"',
            '',
            '[frames.vectors]',
            'description = "10 когнитивных векторов: дедукция, инверсия, парадокс..."',
            'file = "frames/vectors.md"',
        ]

    # Agents — только обнаруженные
    lines.append('')
    lines.append('# ── Агенты ───────────────────────────────────────────────────')

    all_agents = ["claude", "gemini", "qwen"]
    for agent in all_agents:
        acfg = _AGENT_CONFIGS[agent]
        enabled = agent in detected_agents
        lines += [
            '',
            f'[agents.{agent}]',
            f'enabled = {"true" if enabled else "false"}'
            f'{"" if enabled else "  # не найден в PATH"}',
            f'command = "{acfg["command"]}"',
            f'args = {acfg["args"]}',
            f'review_args = {acfg["review_args"]}',
            f'inactivity_timeout = {acfg["timeout"]}',
        ]

    lines += [
        '',
        '[build]',
        'commands = []',
        'check_command = ""',
        '',
        '[limits]',
        'max_parallel_tasks = 10',
        'agent_timeout = 900',
        'max_review_rounds = 3',
        '',
    ]
    return '\n'.join(lines)

_INIT_TASKS = '''# TASKS — {name}
'''


def _generate_brief(cwd: Path, brief_path: Path):
    """Генерирует PROJECT_BRIEF.md — анализ проекта для архитектурных дискуссий."""
    import subprocess
    import shutil

    # Собираем контекст проекта
    context_parts = []

    # README
    for name in ("README.md", "readme.md", "README.rst", "README"):
        readme = cwd / name
        if readme.exists():
            text = readme.read_text(encoding="utf-8", errors="ignore")[:6000]
            context_parts.append(f"## README\n{text}")
            break

    # CLAUDE.md
    claude_md = cwd / "CLAUDE.md"
    if claude_md.exists():
        text = claude_md.read_text(encoding="utf-8", errors="ignore")[:4000]
        context_parts.append(f"## CLAUDE.md\n{text}")

    # Зависимости
    for dep_file in ("requirements.txt", "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "Gemfile"):
        dep_path = cwd / dep_file
        if dep_path.exists():
            text = dep_path.read_text(encoding="utf-8", errors="ignore")[:3000]
            context_parts.append(f"## {dep_file}\n{text}")

    # Структура директорий (2 уровня)
    try:
        result = subprocess.run(
            ["find", ".", "-maxdepth", "2", "-type", "f", "-not", "-path", "./.git/*",
             "-not", "-path", "./node_modules/*", "-not", "-path", "./.venv/*"],
            cwd=cwd, capture_output=True, text=True, timeout=10,
        )
        files = result.stdout.strip()
        if files:
            # Ограничиваем до 100 файлов
            file_list = files.split("\n")[:100]
            context_parts.append("## Структура файлов\n" + "\n".join(file_list))
    except Exception:
        pass

    if not context_parts:
        print(f"  {C['dim']}Нет файлов для анализа — PROJECT_BRIEF.md не создан{R}")
        return

    context = "\n\n".join(context_parts)

    prompt = f"""Проанализируй проект и создай PROJECT_BRIEF.md — краткое описание для архитектурных дискуссий.

Формат ответа — ТОЛЬКО markdown, без обёрток ```markdown```:

# Project Brief

## Что это
(1-3 предложения: что делает проект, для кого)

## Стек
(язык, фреймворки, ключевые библиотеки)

## Архитектура
(основные компоненты и как они связаны, 3-7 пунктов)

## Ограничения и контекст
(инфраструктурные, бизнес-ограничения, что важно для принятия архитектурных решений)

## Зависимости
(внешние сервисы, API, базы данных)

---

Контекст проекта:

{context}"""

    # Пробуем доступные агенты (в порядке предпочтения)
    for cmd_name in ("claude", "gemini", "qwen"):
        if not shutil.which(cmd_name):
            continue
        print(f"  {C['yellow']}⚡ Генерация PROJECT_BRIEF.md через {cmd_name}...{R}", end="", flush=True)
        try:
            if cmd_name == "claude":
                cmd = [cmd_name, "-p", "-", "--output-format", "text", "--permission-mode", "auto"]
            elif cmd_name == "qwen":
                cmd = [cmd_name, "-p", "--output-format", "text", "--approval-mode", "yolo"]
            else:
                cmd = [cmd_name, "-p", "", "--output-format", "text"]
            result = subprocess.run(
                cmd, cwd=cwd, input=prompt,
                capture_output=True, text=True, timeout=120,
            )
            text = (result.stdout or "").strip()
            if text and len(text) > 100:
                brief_path.write_text(text, encoding="utf-8")
                print(f" {C['green']}✓{R}")
                return
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
    print(f" {C['red']}✗ нет доступных агентов{R}")


def _detect_agents() -> list[str]:
    """Ищет доступные CLI агентов в PATH."""
    import shutil
    found = []
    for name in ("claude", "gemini", "qwen"):
        if shutil.which(name):
            found.append(name)
    return found


def _cmd_init():
    """Инициализирует ForgeRace в текущей директории."""
    cwd = Path.cwd()
    name = cwd.name
    forgerace_dir = Path(__file__).resolve().parent.parent  # корень репы forgerace

    # 1. Детектим агентов
    detected = _detect_agents()
    if detected:
        print(f"  {C['green']}✓ Найдены агенты: {', '.join(detected)}{R}")
    else:
        print(f"  {C['yellow']}⚠ Агенты не найдены в PATH (claude, gemini, qwen){R}")

    # 2. Проверяем наличие встроенных фреймов
    has_frames = (forgerace_dir / "frames" / "audit.md").exists()
    if has_frames:
        print(f"  {C['green']}✓ Когнитивные фреймы: {forgerace_dir / 'frames'}{R}")

    # 3. Создаём файлы
    created = []

    toml_path = cwd / "forgerace.toml"
    if toml_path.exists():
        print(f"  {C['dim']}forgerace.toml уже существует{R}")
    else:
        toml_path.write_text(
            _build_init_toml(name, detected, has_frames), encoding="utf-8")
        created.append("forgerace.toml")

    tasks_path = cwd / "TASKS.md"
    if tasks_path.exists():
        print(f"  {C['dim']}TASKS.md уже существует{R}")
    else:
        tasks_path.write_text(_INIT_TASKS.format(name=name), encoding="utf-8")
        created.append("TASKS.md")

    # 4. Симлинк fr → forgerace.py
    forgerace_py = (forgerace_dir / "forgerace.py").resolve()
    fr_path = cwd / "fr"
    if not fr_path.exists():
        fr_path.write_text(f"#!/bin/sh\nexec python3 {forgerace_py} \"$@\"\n", encoding="utf-8")
        fr_path.chmod(0o755)
        created.append("fr")

    # 5. Добавляем fr в .gitignore
    gitignore = cwd / ".gitignore"
    if gitignore.exists():
        gi_text = gitignore.read_text(encoding="utf-8")
        if "fr" not in gi_text.splitlines():
            with open(gitignore, "a", encoding="utf-8") as f:
                f.write("\n# ForgeRace launcher\nfr\n")
            print(f"  {C['green']}✓ fr добавлен в .gitignore{R}")
    else:
        gitignore.write_text("# ForgeRace launcher\nfr\n", encoding="utf-8")
        created.append(".gitignore")

    # 6. docs/discuss
    discuss_dir = cwd / "docs" / "discuss"
    if not discuss_dir.exists():
        discuss_dir.mkdir(parents=True, exist_ok=True)
        created.append("docs/discuss/")

    if created:
        print(f"  {C['green']}✓ Создано: {', '.join(created)}{R}")

    # 7. PROJECT_BRIEF.md
    brief_path = cwd / "PROJECT_BRIEF.md"
    if brief_path.exists():
        print(f"  {C['dim']}PROJECT_BRIEF.md уже существует{R}")
    else:
        _generate_brief(cwd, brief_path)

    # 8. Подсказки
    print(f"\n  {C['bold']}Быстрый старт:{R}")
    print(f"    {C['bold']}vim forgerace.toml{R}        — настрой проект")
    print(f"    {C['bold']}./fr discuss new тема '?'{R} — начни дискуссию")
    if has_frames and detected:
        agent = detected[0]
        print(f"    {C['bold']}./fr discuss chat тема{R}    — интерактивный чат")
        print(f"    {C['yellow']}  /{agent}+audit{R}             — проверь: 8 осей выживаемости")
        print(f"    {C['yellow']}  /{agent}+wild{R}              — сломай: дикие стратегии")
        print(f"    {C['yellow']}  /{agent}+price{R}             — оцени: цена выбора")
    print(f"    {C['bold']}./fr run{R}                  — запусти задачи")
    print(f"    {C['bold']}./fr help{R}                 — все команды")


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
                "  " + "│ " * indent + "├─"
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
        m_res: MergeResult = merge_to_develop(branch, t.id)
        if m_res.success:
            update_task_status(t.id, "done")
            print(f"  ✓ {t.id} → done")
        else:
            if m_res.is_test_failure:
                print(f"  ✗ {t.id} — тесты провалены (код {m_res.returncode})")
                # Используем свойства .stderr/.stdout для совместимости с subprocess.CompletedProcess
                output = m_res.stderr or m_res.stdout or ""
                stderr = output[-1000:]
                create_checkpoint_task(stderr)
            else:
                print(f"  ✗ {t.id} — ошибка мержа (конфликт?), нужен ручной разбор")
                if m_res.merge_stderr:
                    print(f"    {(m_res.merge_stderr or m_res.merge_stdout)[:300]}")

    print()
    show_status()


def _cmd_agents_list():
    """Показывает всех агентов и их статус."""
    mode_color = C['cyan'] if cfg.mode == "competitive" else C['magenta']
    print(f"  Режим: {mode_color}{C['bold']}{cfg.mode}{R}")
    print()
    for name, acfg in cfg.agents.items():
        status = f"{C['green']}ON{R}" if acfg.enabled else f"{C['red']}OFF{R}"
        print(f"  {C['bold']}{name}{R}: {status}  ({acfg.command})")
    print(f"\n  Активные: {C['bold']}{cfg.all_agent_names}{R}")


def _cmd_monitor(interval: int = 10, once: bool = False):
    """Live dashboard: progress with auto-refresh."""
    import time as _time
    import subprocess as _sp
    BAR_LEN = 15
    try:
        while True:
            # Cursor home + clear to end of screen (no flicker vs full clear)
            print("\033[H\033[J", end="")
            tasks = parse_tasks()
            teams = {}
            for t in tasks:
                d = t.discussion or ""
                if d and d != "—" and len(d) < 60 and "**" not in d and not d.startswith("- "):
                    teams.setdefault(d, []).append(t)

            now = _time.strftime("%H:%M:%S")
            # Count live processes
            try:
                procs = int(_sp.run(["pgrep", "-fc", "forgerace.py (run|discuss)"],
                                     capture_output=True, text=True).stdout.strip() or "0")
            except Exception:
                procs = 0
            # LiteLLM health check — clean env to bypass system proxy
            try:
                _clean_env = {k: v for k, v in os.environ.items()
                              if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
                _hc = _sp.run(["curl", "-s", "--connect-timeout", "2",
                               "-o", "/dev/null", "-w", "%{http_code}",
                               "http://127.0.0.1:4000/health"],
                              capture_output=True, text=True, timeout=5, env=_clean_env)
                _litellm_ok = _hc.stdout.strip() in ("200", "401")
            except Exception:
                _litellm_ok = False
            litellm_status = f"{C['green']}LiteLLM ✓{R}" if _litellm_ok else f"{C['red']}LiteLLM ✗{R}"
            if procs > 0:
                proc_str = f"{C['green']}▶ {procs} running{R}"
            else:
                proc_str = f"{C['red']}■ stopped{R}"
            print(f"  {C['cyan']}{C['bold']}ForgeRace Monitor{R}  {C['dim']}{now}{R}  {proc_str}  {litellm_status}  {C['dim']}(Ctrl+C){R}")
            print()

            # Teams table
            print(f"  {C['cyan']}{'Team':<18} {'Done':>5}  {'Progress':<{BAR_LEN}}  Status{R}")
            print(f"  {'─' * 55}")

            active_tasks = []

            # Detect actually-coding tasks from fresh logs (only when processes running)
            import re as _re
            _coding_now = set()
            if procs > 0:
                for logf in list(cfg.log_dir.glob("*.log")):
                    try:
                        if not logf.exists() or _time.time() - logf.stat().st_mtime > 60:
                            continue
                        for line in logf.read_text(errors="replace").splitlines()[-30:]:
                            m = _re.search(r"\[(TASK-\d+)/", line)
                            if m:
                                _coding_now.add(m.group(1))
                    except Exception:
                        pass

            for team_name, tt in sorted(teams.items()):
                done = sum(1 for t in tt if t.status == "done")
                total = len(tt)
                skip = sum(1 for t in tt if t.status == "skip")
                completed = done + skip

                # Hide fully done teams
                if completed == total and total > 0:
                    continue

                if False:  # was: collapsed done line
                    pass
                else:
                    ip_tasks = [t for t in tt if "progress" in t.status or t.id in _coding_now]
                    blocked_tasks = [t for t in tt if "blocked" in t.status.lower() and t.id not in _coding_now]
                    ip = len(ip_tasks)
                    blocked = len(blocked_tasks)
                    pending = total - completed - ip - blocked

                    filled = int(BAR_LEN * completed / total) if total else 0
                    bar = f"{C['green']}{'█' * filled}{C['dim']}{'░' * (BAR_LEN - filled)}{R}"

                    if ip > 0:
                        status_str = f"  {C['magenta']}coding ({ip}){R}"
                    elif blocked > 0 and procs == 0:
                        status_str = f"  {C['red']}STOPPED{R} {C['dim']}({blocked} failed){R}"
                    elif blocked > 0:
                        status_str = f"  {C['red']}blocked ({blocked}){R}"
                    elif pending > 0 and procs == 0:
                        status_str = f"  {C['yellow']}IDLE{R} {C['dim']}({pending} waiting){R}"
                    elif pending > 0:
                        status_str = f"  {C['yellow']}pending ({pending}){R}"
                    else:
                        status_str = ""

                pct = f"{done}/{total}"
                short = team_name[:15]
                pad = max(1, 18 - len(short))
                pct_pad = max(1, 6 - len(pct))
                print(f"  {C['yellow']}{C['bold']}{short}{R}{' '*pad}{pct}{' '*pct_pad}{bar}{status_str}")

                # Show each task in this team
                for t in sorted(tt, key=lambda x: x.id):
                    coding = t.id in _coding_now
                    if t.status == "done":
                        print(f"    {C['green']}✓{R} {C['dim']}{t.id}  {t.name[:45]}{R}")
                    elif t.status == "skip":
                        print(f"    {C['dim']}⊘ {t.id}  {t.name[:40]} (skip){R}")
                    elif coding:
                        print(f"    {C['magenta']}⚡{R} {C['bold']}{t.id}{R}  {t.name[:45]}")
                    elif "blocked" in t.status.lower() or (t.deps and not all(
                        any(d2.id == d and d2.status == "done" for d2 in tt) or d not in {x.id for x in tt}
                        for d in t.deps)):
                        deps = ", ".join(t.deps[:3]) if t.deps else "?"
                        print(f"    {C['red']}✗{R} {t.id}  {t.name[:35]:<35}  {C['dim']}waits: {deps}{R}")
                        active_tasks.append(t)
                    else:
                        print(f"    {C['yellow']}…{R} {t.id}  {t.name[:45]}")
                print()

            # Blocked section removed — already shown inline per team

            # Totals — sum from visible teams only (excludes completed old teams)
            visible_tasks = [t for team_tt in teams.values() for t in team_tt
                             if sum(1 for x in teams.get(t.discussion or "", []) if x.status == "done") < len(teams.get(t.discussion or "", []))]
            total_done = sum(1 for t in visible_tasks if t.status == "done")
            total_all = len(visible_tasks)
            pct = f"{total_done*100//total_all}%" if total_all else "—"
            print(f"\n  {C['bold']}Total: {C['green']}{total_done}{R}{C['bold']}/{total_all} ({pct}){R}  {C['dim']}Refresh: {interval}s{R}")

            # Per-agent activity: only when processes are running
            import re as _re
            agent_activity = {}
            now_ts = _time.time()
            _ansi_re = _re.compile(r'\x1b\[[0-9;]*m')
            if procs > 0:
              log_files = list(cfg.log_dir.glob("*.log"))
              orch = cfg.log_dir / "orchestrator.log"
              if orch.exists():
                log_files.append(orch)
              for logf in log_files:
                try:
                    if now_ts - logf.stat().st_mtime > 60:
                        continue  # skip logs inactive > 1min
                    lines = logf.read_text(errors="replace").splitlines()[-100:]
                    for line in reversed(lines):
                        line = _ansi_re.sub('', line)  # strip ANSI codes
                        m = _re.search(r"\[(TASK-\d+)/([\w,-]+)\].*?(⏳ \S+.*?—\s*(.+)|Applied edit to (.+)|📖 Read (.+)|✏️\s+\w+ (.+)|💻 Bash: (.+)|🔍 (?:Grep|Glob): (.+)|📝 Прогресс: (.+)|replace (.+)|write_file (.+))", line)
                        if m:
                            agent = m.group(2).split(",")[0]
                            if agent not in agent_activity:
                                task_id = m.group(1)
                                action = m.group(4) or m.group(5) or m.group(6) or m.group(7) or m.group(8) or m.group(9) or ""
                                agent_activity[agent] = (task_id, action.strip()[:50])
                except Exception:
                    pass

              # Also scan for review/discuss activity
              for logf in cfg.log_dir.glob("*.log"):
                try:
                    if now_ts - logf.stat().st_mtime > 60:
                        continue
                    lines = logf.read_text(errors="replace").splitlines()[-50:]
                    for line in reversed(lines):
                        line = _ansi_re.sub('', line)
                        # Review: [TASK-123/ревью] llama→gemini: APPROVED
                        m = _re.search(r"\[(TASK-\d+)/ревью\].*?(\w[\w-]*)→(\w[\w-]*)", line)
                        if m and m.group(2) not in agent_activity:
                            agent_activity[m.group(2)] = (m.group(1), "review", f"→{m.group(3)}")
                        # Review: 📋 llama ревьюит gemini
                        m = _re.search(r"📋\s+(\w[\w-]*)\s+ревьюит\s+(\w[\w-]*)", line)
                        if m and m.group(1) not in agent_activity:
                            agent_activity[m.group(1)] = (None, "review", f"→{m.group(2)}")
                except Exception:
                    pass
            # end if procs > 0

            if agent_activity:
                print(f"\n  {C['bold']}Agent Activity{R}")
                for agent, info in sorted(agent_activity.items()):
                    color = agent_color(agent)
                    acfg = cfg.agents.get(agent)
                    frame = f"+{acfg.default_frame}" if acfg and acfg.default_frame else ""
                    name = f"{agent}{frame}"
                    pad = max(1, 22 - len(name))
                    if len(info) == 3:
                        tid, role, detail = info
                        role_color = C['blue'] if role == "review" else C['magenta']
                        tid_str = f"{C['yellow']}{tid}{R} " if tid else ""
                        print(f"  {color}{name}{R}{' '*pad}{role_color}{role:<8}{R} {tid_str}{C['dim']}{detail}{R}")
                    else:
                        tid, action = info
                        print(f"  {color}{name}{R}{' '*pad}{C['magenta']}{'coding':<8}{R} {C['yellow']}{tid}{R}  {C['dim']}{action}{R}")

            if once:
                break
            _time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\n  {C['dim']}Monitor stopped.{R}")


def _cmd_feature(subcmd: str | None, args):
    """Feature branch management."""
    if subcmd == "list" or subcmd is None:
        result = run_cmd(["git", "branch", "--list", "feature/*"], cwd=cfg.root_dir, check=False)
        branches = [b.strip().lstrip("* ") for b in result.stdout.strip().split("\n") if b.strip()]
        if not branches:
            print(f"  {C['dim']}Нет feature branches. Создаются автоматически при ./fr run --team <name>{R}")
            return
        tasks = parse_tasks()
        print(f"\n  {C['bold']}Feature Branches{R}\n")
        for b in branches:
            # Count tasks linked to this feature
            team_name = b.replace("feature/", "")
            team_tasks = [t for t in tasks if team_name in (t.discussion or "")]
            done = sum(1 for t in team_tasks if t.status == "done")
            total = len(team_tasks)
            bar = f"{C['green']}{done}{R}/{total}" if total else f"{C['dim']}0{R}"
            print(f"  {C['bold']}{b}{R}  задачи: {bar}")
        print()
    elif subcmd == "score":
        _cmd_feature_score()
    elif subcmd == "merge":
        branch = getattr(args, "branch", "")
        if not branch.startswith("feature/"):
            branch = f"feature/{branch}"
        print(f"  Мерж {C['bold']}{branch}{R} → {C['bold']}{cfg.dev_branch}{R}")
        result = run_cmd(["git", "merge", branch, "--no-ff", "-m",
                          f"Merge {branch} → {cfg.dev_branch}"],
                         cwd=cfg.root_dir, check=False)
        if result.returncode == 0:
            print(f"  {C['green']}Успешно{R}")
        else:
            print(f"  {C['red']}Ошибка: {result.stderr[:200]}{R}")


def _cmd_feature_score():
    """Scoreboard: per-team metrics from logs."""
    tasks = parse_tasks()
    # Find all championship discussions
    teams = {}
    for t in tasks:
        d = t.discussion or ""
        if d and d != "—":
            teams.setdefault(d, []).append(t)

    if not teams:
        print(f"  {C['dim']}Нет задач с привязкой к дискуссиям{R}")
        return

    print(f"\n  {C['bold']}{'Команда':<40} {'Done':>5} {'Total':>6} {'Cost':>8}{R}")
    print(f"  {'─' * 62}")
    for team_name, tt in sorted(teams.items()):
        done = sum(1 for t in tt if t.status == "done")
        total = len(tt)
        # Parse cost from logs
        cost = 0.0
        for t in tt:
            for logf in cfg.log_dir.glob(f"{t.id.lower()}-*.log"):
                try:
                    text = logf.read_text(errors="replace")
                    for line in text.split("\n"):
                        if "Стоимость:" in line or "cost:" in line.lower():
                            import re as _re
                            m = _re.search(r"\$(\d+\.?\d*)", line)
                            if m:
                                cost += float(m.group(1))
                except OSError:
                    pass
        f"{done*100//total}%" if total else "—"
        cost_str = f"${cost:.2f}" if cost > 0 else "—"
        color = C['green'] if done == total and total > 0 else ""
        print(f"  {color}{team_name:<40} {done:>5} {total:>6} {cost_str:>8}{R}")
    print()

def _cmd_stats():
    """Выводит статистику задач."""
    tasks = parse_tasks()
    if not tasks:
        print(f"\n  {C['dim']}Нет задач в TASKS.md{R}")
        return

    # Статистика по статусам
    status_counts = {}
    for t in tasks:
        status_base = t.status.split(":")[0]
        status_counts[status_base] = status_counts.get(status_base, 0) + 1

    print(f"\n  {C['bold']}Статистика задач{R}")
    print(f"  {'─' * 60}")
    print(f"  {C['bold']}Всего задач:{R} {len(tasks)}")
    print(f"  {C['bold']}По статусам:{R}")
    for status, count in sorted(status_counts.items()):
        color = C['green'] if status == 'done' else C['yellow'] if status == 'in_progress' else C['red'] if status == 'blocked' else C['white']
        print(f"    {color}{status:<15}{R}: {count}")

    # Статистика по приоритетам
    priority_counts = {}
    for t in tasks:
        priority_counts[t.priority] = priority_counts.get(t.priority, 0) + 1

    print(f"\n  {C['bold']}По приоритетам:{R}")
    for priority in sorted(priority_counts.keys()):
        count = priority_counts[priority]
        print(f"    {priority}: {count}")

    # Статистика по этапам
    stage_counts = {}
    for t in tasks:
        stage_counts[t.stage] = stage_counts.get(t.stage, 0) + 1

    print(f"\n  {C['bold']}По этапам:{R}")
    for stage in sorted(stage_counts.keys()):
        count = stage_counts[stage]
        print(f"    Этап {stage}: {count}")

    # Статистика по агентам
    agent_counts = {}
    for t in tasks:
        if t.agent and t.agent != "—":
            agent_counts[t.agent] = agent_counts.get(t.agent, 0) + 1

    if agent_counts:
        print(f"\n  {C['bold']}По агентам:{R}")
        for agent, count in sorted(agent_counts.items()):
            print(f"    {agent}: {count}")

    print()

def _cmd_benchmark(agent: Optional[str] = None, format_: str = "table"):
    """Показать таблицу метрик производительности агентов."""
    store = BenchmarkStore()
    if format_ == "json":
        print(store.as_json())
    else:
        print(store.as_table())


def _cmd_mode(mode_name: str):
    """Переключает режим competitive/distributed в forgerace.toml."""
    if mode_name not in ("competitive", "distributed"):
        print(f"  {C['red']}Неизвестный режим '{mode_name}'. Доступные: competitive, distributed{R}")
        return

    toml_path = cfg.root_dir / "forgerace.toml"
    if not toml_path.exists():
        print(f"  {C['red']}forgerace.toml не найден{R}")
        return

    content = toml_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    # Ищем mode = ... в [project]
    mode_idx = None
    project_end = len(lines)
    in_project = False
    for i, line in enumerate(lines):
        if line.strip() == "[project]":
            in_project = True
            continue
        if in_project and line.strip().startswith("["):
            project_end = i
            break
        if in_project and line.strip().startswith("mode"):
            mode_idx = i

    if mode_idx is not None:
        lines[mode_idx] = f'mode = "{mode_name}"'
    else:
        # Вставляем перед концом [project]
        lines.insert(project_end, f'mode = "{mode_name}"')

    toml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    init_config(config_path=toml_path)

    mode_color = C['cyan'] if mode_name == "competitive" else C['magenta']
    print(f"  Режим: {mode_color}{C['bold']}{mode_name}{R}")
    if mode_name == "competitive":
        print("  Все агенты на каждую задачу, race-to-merge")
    else:
        print("  Задачи распределяются по агентам round-robin")


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
    section_idx = next(i for i, line in enumerate(lines) if line.strip() == section)

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
    print(f"  Активные: {C['bold']}{cfg.all_agent_names}{R}")


def _print_full_help():
    """Полная справка с примерами."""
    DIM = C['dim']
    Y = C['yellow']
    B = C['bold']
    print(f"""{B}ForgeRace{R} — мультиагентный оркестратор разработки

{Y}БЫСТРЫЙ СТАРТ:{R}
  {B}./fr init{R}                              Создать проект (авто-детект агентов + фреймы)
  {B}./fr discuss new{R} auth 'вопрос'          Создать дискуссию
  {B}./fr discuss chat{R} auth                  Интерактивный чат → /help внутри
  {B}./fr run{R}                               Запустить все готовые задачи

{Y}ЗАПУСК ЗАДАЧ:{R}
  ./fr run                               Все готовые задачи (конкурентно)
  ./fr run --task TASK-032               Конкретная задача
  ./fr run --team championship-ensemble  Задачи команды (фильтр по дискуссии)
  ./fr run --retry                       Перезапуск упавших (blocked → open)
  ./fr run --auto --max-tasks 4          Авто-цикл: разблокированные → запуск
  ./fr run --dry-run                     Показать что запустится (без запуска)

{Y}FEATURE BRANCHES:{R}
  ./fr feature list                      Все feature branches и прогресс
  ./fr feature score                     Скорборд по командам (задачи, стоимость)
  ./fr feature merge <branch>            Мерж feature branch в develop

{Y}ДИСКУССИИ:{R}
  ./fr discuss new <тема> '<вопрос>'     Создать дискуссию
  ./fr discuss chat <тема>               Интерактивный чат
  ./fr discuss list                      Список дискуссий
  ./fr discuss show <тема>               Показать дискуссию целиком
  ./fr discuss reply <тема> --agent X    Пакетный ответ агента (без чата)
  ./fr discuss round <тема>             Все агенты отвечают последовательно (medium → weak)
  ./fr discuss msg <тема> 'текст'       Добавить сообщение от techlead
  ./fr discuss resolve <тема> 'решение' Закрыть дискуссию + сгенерировать задачи
  ./fr discuss regen <тема>              Перегенерировать задачи из резолюции

  {DIM}В чате: /help — справка, /help <команда> — подробности, Tab — автодополнение{R}

{Y}КОГНИТИВНЫЕ ФРЕЙМЫ:{R}
  {DIM}Модель + фрейм = агент-специалист. Три qwen с разными фреймами = три перспективы.{R}

  В чате:    /qwen+audit                 Агент с фреймом
  Пакетно:   ./fr discuss reply тема --agent qwen+audit
  Сценарий:  /agent+audit → /agent+wild → /agent+price  {DIM}(проверь → сломай → оцени){R}

  {DIM}Фреймы настраиваются в forgerace.toml секция [frames.*]{R}

  {Y}Встроенные фреймы:{R}
    {B}reviewer{R}    — строгий код-ревьюер: соответствие задаче, корректность, безопасность
    {B}audit{R}       — 8 осей выживаемости решения
    {B}wild{R}        — дикие стратегии, инверсия, слом шаблонов
    {B}price{R}       — цена выбора, trade-off матрицы, арбитраж
    {B}theory{R}      — от принципов к реализации
    {B}evidence{R}    — от фактов к обобщению
    {B}optimizer{R}   — 4-фазный разбор: grounding → divergence → red-teaming
    {B}meta{R}        — 13 фреймворков: диалектика, теория игр, стоицизм...
    {B}vectors{R}     — 10 когнитивных векторов: дедукция, инверсия, парадокс...
    {B}adversarial{R} — red-team: баги, race conditions, edge cases
    {B}architect{R}   — структура: coupling, cohesion, data flow
    {B}user{R}        — UX: что путает, бесит, вводит в заблуждение

{Y}АГЕНТЫ И РЕЖИМ:{R}
  ./fr agents                            Список агентов и статус (ON/OFF)
  ./fr agents off claude                 Выключить claude
  ./fr agents on claude                  Включить обратно
  ./fr mode                              Текущий режим
  ./fr mode competitive                  Все агенты на каждую задачу (race)
  ./fr mode distributed                  Задачи раскидываются по агентам

{Y}ЗАДАЧИ:{R}
  ./fr task list                         Компактный список задач
  ./fr task add 'название' [--priority P1] [--description '...'] [--discussion topic]
  ./fr task edit TASK-038 --status done  Изменить поля задачи
  ./fr task archive                      Перенести done/skip в done/TASKS_дата.md

{Y}ДИАГНОСТИКА:{R}
  ./fr doctor                            Проверка и автолечение среды
  ./fr retry TASK-038                    Умный перезапуск (анализ причины, смена агента)
  ./fr retry --all                       Перезапустить все blocked задачи
  ./fr logs                              Список логов
  ./fr logs show TASK-038 [--tail 50]    Показать лог задачи
  ./fr logs follow [TASK-038]            Live-tail логов

{Y}СТАТУС И МЕРЖ:{R}
  ./fr status                            Статус задач + граф зависимостей
  ./fr merge-pending                     Промержить review-задачи в develop
  ./fr monitor                           Live dashboard прогресса

{Y}СТАТИСТИКА:{R}
  ./fr stats                             Вывести статистику задач
  ./fr models [--test]                   Проверить доступные API-модели
  ./fr benchmark                         Метрики производительности агентов

{Y}ОПЦИИ:{R}
  --config PATH                          Путь к forgerace.toml
  --root PATH                            Корневая директория проекта
  --verbose                              Подробный вывод
""")


def _cmd_models(test: bool = False, top: int = 30):
    """List and optionally test available API models."""
    import json
    import urllib.request
    import urllib.error
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Collect unique API endpoints from config
    endpoints = {}
    for name, acfg in cfg.agents.items():
        if acfg.protocol == "openai" and acfg.enabled and acfg.base_url and acfg.api_key:
            key = (acfg.base_url, acfg.api_key)
            if key not in endpoints:
                endpoints[key] = name

    if not endpoints:
        print(f"  {C['red']}Нет enabled API-агентов с protocol=openai{R}")
        return

    for (base_url, api_key), ref_agent in endpoints.items():
        print(f"\n  {C['bold']}Endpoint:{R} {base_url}")
        print(f"  {C['dim']}(агент-референс: {ref_agent}){R}")

        # Fetch model list
        url = base_url.rstrip("/") + "/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"  {C['red']}Ошибка загрузки моделей: {e}{R}")
            continue

        models = sorted([m["id"] for m in data.get("data", [])])
        print(f"  Моделей в каталоге: {C['bold']}{len(models)}{R}")

        # Show currently configured models
        configured = {}
        for aname, acfg in cfg.agents.items():
            if acfg.protocol == "openai" and acfg.base_url == base_url:
                configured[acfg.model] = aname
        if configured:
            print(f"\n  {C['bold']}Подключены:{R}")
            for model_id, aname in configured.items():
                color = agent_color(aname)
                print(f"    {color}{aname}{R} → {model_id}")

        if not test:
            # Just list interesting models
            code_keywords = ("code", "instruct", "coder", "devstral", "gpt-oss",
                             "llama-3", "llama-4", "qwen", "mistral", "deepseek",
                             "nemotron", "kimi", "glm", "gemma-4", "seed", "minimax",
                             "step-")
            skip_keywords = ("embed", "guard", "safety", "reward", "parse",
                             "translate", "clip", "deplot", "vila", "neva",
                             "gliner", "calibration")
            interesting = [m for m in models
                           if any(k in m.lower() for k in code_keywords)
                           and not any(s in m.lower() for s in skip_keywords)]
            if interesting:
                print(f"\n  {C['bold']}Потенциально интересные ({len(interesting)}):{R}")
                for m in interesting:
                    marker = f" {C['green']}← подключена{R}" if m in configured else ""
                    print(f"    {m}{marker}")
            print(f"\n  {C['dim']}Используйте --test для проверки доступности{R}")
            return

        # Test models
        test_prompt = "Review this Python function for bugs. Reply with VERDICT: APPROVED or NEEDS_WORK, then COMMENTS and SUMMARY.\n```python\ndef merge(a, b):\n    result = a\n    for k, v in b.items():\n        result[k] = v\n    return result\n```"

        # Filter to code-relevant models
        code_keywords = ("instruct", "coder", "devstral", "gpt-oss",
                         "llama-3", "llama-4", "qwen3", "qwen2.5-coder",
                         "mistral-large", "mistral-medium", "mistral-small",
                         "mistral-nemotron", "nemotron-3-super", "nemotron-ultra",
                         "kimi-k2", "glm5", "gemma-4", "seed-oss", "minimax-m2",
                         "step-3", "maverick")
        candidates = [m for m in models
                      if any(k in m.lower() for k in code_keywords)
                      and "embed" not in m.lower()
                      and "guard" not in m.lower()
                      and "safety" not in m.lower()
                      and "reward" not in m.lower()]
        candidates = candidates[:top]

        print(f"\n  {C['bold']}Тестирую {len(candidates)} моделей...{R}\n")

        def _test_one(model_id):
            body = json.dumps({
                "model": model_id,
                "messages": [{"role": "user", "content": test_prompt}],
                "max_tokens": 300,
                "temperature": 0.3,
            }).encode("utf-8")
            req = urllib.request.Request(
                base_url.rstrip("/") + "/chat/completions",
                data=body,
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                method="POST",
            )
            start = time.time()
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    rdata = json.loads(resp.read().decode("utf-8"))
                    content = rdata["choices"][0]["message"]["content"].strip()
                    elapsed = time.time() - start
                    return model_id, True, elapsed, content[:120]
            except Exception as e:
                elapsed = time.time() - start
                err = str(e)[:80]
                return model_id, False, elapsed, err

        results = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(_test_one, m): m for m in candidates}
            for f in as_completed(futures):
                mid, ok, elapsed, detail = f.result()
                results.append((mid, ok, elapsed, detail))
                status = f"{C['green']}OK{R}" if ok else f"{C['red']}FAIL{R}"
                time_str = f"{elapsed:.1f}s"
                marker = f" {C['cyan']}← {configured[mid]}{R}" if mid in configured else ""
                print(f"  {status} {C['dim']}{time_str:>5}{R}  {mid}{marker}")
                if ok:
                    print(f"         {C['dim']}{detail}{R}")

        ok_count = sum(1 for _, ok, *_ in results if ok)
        print(f"\n  {C['bold']}Итого:{R} {C['green']}{ok_count}{R}/{len(results)} моделей доступны")


def main():
    parser = argparse.ArgumentParser(
        description="ForgeRace — мультиагентный оркестратор",
        usage="forgerace <command> [options] (./fr help для справки)",
    )
    parser.add_argument("--config", type=Path, help="Путь к forgerace.toml")
    parser.add_argument("--root", type=Path, help="Корневая директория проекта")
    parser.add_argument("--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", title="commands", metavar="")
    _orig_format_help = parser.format_help
    parser.format_help = lambda: _orig_format_help().replace("commands:\n  \n", "commands:\n")

    # run
    run_p = sub.add_parser("run", help="Запустить задачи",
        epilog="Примеры:\n"
               "  ./fr run                                все готовые задачи\n"
               "  ./fr run --task TASK-032                конкретная задача\n"
               "  ./fr run --team championship-ensemble   задачи команды (по дискуссии)\n"
               "  ./fr run --retry                        перезапуск упавших\n"
               "  ./fr run --auto                         авто-цикл разблокированных\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    run_p.add_argument("--task", help="Конкретная задача (TASK-032)")
    run_p.add_argument("--team", help="Задачи команды по дискуссии (championship-ensemble-review)")
    run_p.add_argument("--retry", action="store_true", help="Перезапустить упавшие (blocked → open)")
    run_p.add_argument("--dry-run", action="store_true", help="Показать что запустится, без запуска")
    run_p.add_argument("--auto", action="store_true", help="Авто-цикл: разблокированные → запуск")
    run_p.add_argument("--max-tasks", type=int, default=None,
                        help="Макс. задач параллельно (дефолт из TOML)")

    # feature
    feat_p = sub.add_parser("feature", help="Feature branches (команды)",
        epilog="Примеры:\n"
               "  ./fr feature list                     все feature branches\n"
               "  ./fr feature score                    скорборд по командам\n"
               "  ./fr feature merge <branch>           мерж feature в develop\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    feat_sub = feat_p.add_subparsers(dest="feat_cmd")
    feat_sub.add_parser("list", help="Список feature branches")
    feat_sub.add_parser("score", help="Скорборд по командам")
    feat_merge = feat_sub.add_parser("merge", help="Мерж feature branch в develop")
    feat_merge.add_argument("branch", help="Имя feature branch")

    # monitor
    mon_p = sub.add_parser("monitor", help="Live dashboard прогресса")
    mon_p.add_argument("--interval", type=int, default=10, help="Интервал обновления (сек)")
    mon_p.add_argument("--once", action="store_true", help="Однократный вывод (без loop)")

    # discuss
    disc_p = sub.add_parser("discuss", help="Дискуссии",
        epilog="Примеры:\n"
               "  ./fr discuss new auth 'Как реализовать авторизацию?'\n"
               "  ./fr discuss chat auth      интерактивный чат\n"
               "  ./fr discuss list           список дискуссий\n"
               "  ./fr discuss show auth      показать целиком\n"
               "  ./fr discuss regen auth     перегенерировать задачи\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    disc_sub = disc_p.add_subparsers(dest="disc_cmd")

    disc_new = disc_sub.add_parser("new", help="Создать дискуссию")
    disc_new.add_argument("topic", help="Имя темы (латиницей, без пробелов)")
    disc_new.add_argument("question", help="Вопрос / начальное сообщение")
    disc_new.add_argument("--author", default="techlead", help="Автор (дефолт: techlead)")

    disc_reply = disc_sub.add_parser("reply", help="Агент отвечает в дискуссии")
    disc_reply.add_argument("topic")
    disc_reply.add_argument("--agent", required=True, help="Имя агента")

    disc_sub.add_parser("list", help="Список дискуссий")

    disc_show = disc_sub.add_parser("show", help="Показать дискуссию целиком")
    disc_show.add_argument("topic")

    disc_chat_p = disc_sub.add_parser("chat", help="Интерактивный чат (/claude /gemini /all /ok /exit)")
    disc_chat_p.add_argument("topic")

    disc_regen = disc_sub.add_parser("regen", help="Перегенерировать задачи из резолюции")
    disc_regen.add_argument("topic")

    disc_round = disc_sub.add_parser("round", help="Все агенты отвечают последовательно (medium → weak)")
    disc_round.add_argument("topic")

    disc_msg = disc_sub.add_parser("msg", help="Добавить сообщение от techlead")
    disc_msg.add_argument("topic")
    disc_msg.add_argument("message", help="Текст сообщения")

    disc_resolve = disc_sub.add_parser("resolve", help="Закрыть дискуссию + сгенерировать задачи")
    disc_resolve.add_argument("topic")
    disc_resolve.add_argument("resolution", help="Текст резолюции")

    # agents
    agents_p = sub.add_parser("agents", help="Агенты (вкл/выкл)",
        epilog="Примеры:\n"
               "  ./fr agents                 список и статус\n"
               "  ./fr agents off claude      выключить claude\n"
               "  ./fr agents on claude       включить обратно\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    agents_sub = agents_p.add_subparsers(dest="agents_cmd")
    agents_sub.add_parser("list", help="Показать агентов и их статус")
    agents_on = agents_sub.add_parser("on", help="Включить агента")
    agents_on.add_argument("agent_name", help="Имя агента (claude, gemini, qwen)")
    agents_off = agents_sub.add_parser("off", help="Выключить агента")
    agents_off.add_argument("agent_name", help="Имя агента (claude, gemini, qwen)")

    # mode
    mode_p = sub.add_parser("mode", help="Режим: competitive или distributed",
        epilog="Примеры:\n"
               "  ./fr mode                   показать текущий режим\n"
               "  ./fr mode competitive       все агенты на каждую задачу\n"
               "  ./fr mode distributed       задачи раскидываются по агентам\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    mode_p.add_argument("mode_name", nargs="?", help="competitive | distributed")

    # task
    task_p = sub.add_parser("task", help="Управление задачами")
    task_sub = task_p.add_subparsers(dest="task_cmd")
    task_sub.add_parser("list", help="Компактный список задач")
    task_add = task_sub.add_parser("add", help="Создать задачу")
    task_add.add_argument("name", help="Название задачи")
    task_add.add_argument("--priority", default="P1", choices=["P0", "P1", "P2", "P3"])
    task_add.add_argument("--depends", default="—", help="Зависимости (TASK-XXX)")
    task_add.add_argument("--files", default="—", help="Файлы (новые)")
    task_add.add_argument("--discussion", default="—", help="Привязка к дискуссии")
    task_add.add_argument("--description", default="—", help="Описание")
    task_sub.add_parser("archive", help="Перенести done/skip задачи в done/TASKS_дата.md")
    task_edit = task_sub.add_parser("edit", help="Изменить поля задачи")
    task_edit.add_argument("task_id", help="ID задачи (TASK-036)")
    task_edit.add_argument("--status", help="Новый статус")
    task_edit.add_argument("--priority", help="Новый приоритет")

    # init
    sub.add_parser("init", help="Создать forgerace.toml и TASKS.md")

    # merge-pending
    sub.add_parser("merge-pending", help="Промержить review → develop")

    # status
    sub.add_parser("status", help="Статус задач + граф зависимостей")

    # models
    models_p = sub.add_parser("models", help="Проверить доступные API-модели")
    models_p.add_argument("--test", action="store_true", help="Протестировать каждую модель запросом")
    models_p.add_argument("--top", type=int, default=30, help="Макс. моделей для теста (default: 30)")

    # benchmark
    bench_p = sub.add_parser("benchmark", help="Показать таблицу метрик производительности")
    bench_p.add_argument("--agent", help="Показать метрики только для конкретного агента")
    bench_p.add_argument("--format", choices=["table", "json"], default="table", help="Формат вывода")

    # help
    sub.add_parser("help", help="Полная справка с примерами")

    # retry
    retry_p = sub.add_parser("retry", help="Умный перезапуск задачи")
    retry_p.add_argument("task_id", nargs="?", help="ID задачи (TASK-037) или --all")
    retry_p.add_argument("--all", action="store_true", help="Перезапустить все blocked")
    retry_p.add_argument("--agent", help="Принудительно указать агента")

    # doctor
    sub.add_parser("doctor", help="Диагностика и автолечение среды")

    # logs
    logs_p = sub.add_parser("logs", help="Просмотр логов задач")
    logs_sub = logs_p.add_subparsers(dest="logs_cmd")
    logs_sub.add_parser("list", help="Список логов")
    logs_show = logs_sub.add_parser("show", help="Показать лог задачи")
    logs_show.add_argument("task_id", help="ID задачи (TASK-032 или 032)")
    logs_show.add_argument("--agent", help="Фильтр по агенту")
    logs_show.add_argument("--tail", type=int, default=50, help="Кол-во строк (default: 50)")
    logs_follow = logs_sub.add_parser("follow", help="Live-tail логов")
    logs_follow.add_argument("task_id", nargs="?", help="ID задачи")
    logs_follow.add_argument("--agent", help="Фильтр по агенту")

    # stats
    stats_p = sub.add_parser("stats", help="Вывести статистику задач")
    stats_p.add_argument("--format", choices=["text", "json"], default="text", help="Формат вывода")

    # rollback
    rollback_p = sub.add_parser("rollback", help="Восстановить состояние после задачи")
    rollback_p.add_argument("task_id", help="ID задачи (TASK-001)")

    # web-server
    web_p = sub.add_parser("web-server", help="Запустить WebServer с SSE")
    web_p.add_argument("--host", default="0.0.0.0", help="Хост для запуска сервера")
    web_p.add_argument("--port", type=int, default=8080, help="Порт для запуска сервера")

    # dashboard (lightweight web dashboard)
    dash_p = sub.add_parser("dashboard", aliases=["web"], help="Web dashboard с SSE (lightweight)")
    dash_p.add_argument("--host", default="localhost", help="Хост (default: localhost)")
    dash_p.add_argument("--port", type=int, default=8080, help="Порт (default: 8080)")

    args = parser.parse_args()

    if args.command == "help" or args.command is None:
        _print_full_help()
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
        elif args.disc_cmd == "round":
            from .discuss import discuss_round
            discuss_round(args.topic)
        elif args.disc_cmd == "msg":
            from .discuss import discuss_msg
            discuss_msg(args.topic, args.message)
        elif args.disc_cmd == "resolve":
            from .discuss import discuss_resolve
            discuss_resolve(args.topic, args.resolution)
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

    # mode
    if args.command == "mode":
        if args.mode_name:
            _cmd_mode(args.mode_name)
        else:
            mode_color = C['cyan'] if cfg.mode == "competitive" else C['magenta']
            print(f"  Режим: {mode_color}{C['bold']}{cfg.mode}{R}")
        return

    # feature
    if args.command == "feature":
        _cmd_feature(getattr(args, "feat_cmd", None), args)
        return

    if args.command == "monitor":
        _cmd_monitor(getattr(args, "interval", 10), getattr(args, "once", False))
        return

    # models
    if args.command == "models":
        _cmd_models(test=args.test, top=args.top)
        return

    # benchmark
    if args.command == "benchmark":
        _cmd_benchmark(agent=args.agent, format_=args.format)
        return

    # merge-pending
    if args.command == "merge-pending":
        merge_pending_tasks()
        return

    # status
    if args.command == "status":
        show_status()
        return

    # stats
    if args.command == "stats":
        _cmd_stats()
        return

    if args.command == "retry":
        from .retry_cmd import retry_task, retry_all_tasks
        if getattr(args, "all", False):
            count = retry_all_tasks()
            print(f"  Перезапущено {count} задач")
        elif args.task_id:
            retry_task(args.task_id)
        else:
            print(f"  {C['dim']}Укажите TASK-ID или --all{R}")
        return

    if args.command == "task":
        from .task_cmd import list_tasks, add_task
        cmd = getattr(args, "task_cmd", None)
        if cmd == "add":
            add_task(args.name, priority=args.priority, depends=args.depends,
                     files_new=args.files, description=args.description,
                     discussion=args.discussion)
        elif cmd == "archive":
            from .tasks import archive_done_tasks
            count = archive_done_tasks()
            if count:
                print(f"  {C['green']}Архивировано {count} задач в done/{R}")
            else:
                print(f"  {C['dim']}Нет задач для архивирования{R}")
        elif cmd == "edit":
            from .task_cmd import edit_task
            fields = {}
            if getattr(args, "status", None):
                fields["Статус"] = args.status
            if getattr(args, "priority", None):
                fields["Приоритет"] = args.priority
            if fields:
                edit_task(args.task_id, **fields)
            else:
                print(f"  {C['dim']}Укажите --status или --priority{R}")
        else:
            list_tasks()
        return

    if args.command == "doctor":
        from .doctor import doctor
        ok = doctor()
        sys.exit(0 if ok else 1)

    if args.command == "logs":
        from .logs_cmd import list_logs, show_log, follow_log
        cmd = getattr(args, "logs_cmd", None)
        if cmd == "show":
            show_log(args.task_id, agent=getattr(args, "agent", None),
                     tail=getattr(args, "tail", 50))
        elif cmd == "follow":
            follow_log(task_id=getattr(args, "task_id", None),
                       agent=getattr(args, "agent", None))
        else:
            list_logs()
        return

    if args.command == "rollback":
        try:
            from .checkpoint import store
        except ImportError:
            print(f"  {C['red']}Модуль checkpoint не найден — функция ещё не реализована{R}")
            return
        task_id = args.task_id
        if not task_id.startswith("TASK-"):
            print(f"  {C['red']}Некорректный ID задачи: {task_id}. Ожидается формат TASK-001{R}")
            return
        checkpoint = store.rollback(task_id)
        if checkpoint:
            print(f"  ✓ Восстановлено состояние после {task_id}")
            print(f"    Время: {checkpoint.timestamp}")
            print(f"    Коммит: {checkpoint.git_sha[:7]}")
            print(f"    Агент: {checkpoint.agent}")
            print(f"    Метрики: {checkpoint.metrics}")
        else:
            print(f"  ✗ Не удалось восстановить {task_id}")
        return

    if args.command == "web-server":
        try:
            import asyncio
            from .diagnose_engine import DiagnoseEngine
        except ImportError:
            print(f"  {C['red']}Модуль web_server/diagnose_engine не найден — функция ещё не реализована{R}")
            return
        engine = DiagnoseEngine()
        server = create_web_server(engine)
        asyncio.run(server.start())
        return

    if args.command in ("dashboard", "web"):
        try:
            from .web_dashboard import start_dashboard
        except ImportError as e:
            print(f"  {C['red']}Не удалось загрузить web_dashboard: {e}{R}")
            return
        start_dashboard(host=args.host, port=args.port)
        return

    # run
    if args.command != "run":
        return

    max_tasks = args.max_tasks or cfg.max_parallel_tasks
    log.info("=" * 60)
    log.info("ForgeRace запущен")
    log.info(f"Корень: {cfg.root_dir}")
    cli_names = [f"{agent_color(n)}{n}{R}" for n in cfg.cli_agent_names]
    api_names = [f"{agent_color(n)}{n}{R}" for n in cfg.all_agent_names if n not in cfg.cli_agent_names]
    parts = [f"код: [{', '.join(cli_names)}]"]
    if api_names:
        parts.append(f"ревью: [{', '.join(api_names)}]")
    log.info(f"Агенты: {', '.join(parts)}")
    log.info(f"Макс. задач: {max_tasks}")
    log.info("=" * 60)

    team = getattr(args, "team", None)
    if team:
        log.info(f"Команда: {C['bold']}{team}{R}")

    run_pipeline(
        specific_task=getattr(args, "task", None),
        dry_run=getattr(args, "dry_run", False),
        max_tasks=max_tasks,
        retry=getattr(args, "retry", False),
        auto=getattr(args, "auto", False),
        team=team,
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
            os.killpg(os.getpgrp(), 9)
        except ProcessLookupError:
            pass
        os._exit(1)

    # setpgrp + custom SIGINT только для run (discuss нужен нормальный Ctrl+C)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "run":
        try:
            os.setpgrp()
        except (PermissionError, OSError):
            pass  # already session leader (e.g. launched via start_new_session)
        signal.signal(signal.SIGINT, _force_exit)
        signal.signal(signal.SIGTERM, _force_exit)
    try:
        main()
    finally:
        _restore_terminal()
