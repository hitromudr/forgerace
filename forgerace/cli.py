"""CLI точка входа: argparse, команды run/discuss/status/merge-pending."""

import argparse
import logging
import os
import signal
import sys
from pathlib import Path

from .config import cfg, init_config, run_hint
from .discuss import discuss_chat, discuss_create, discuss_list, discuss_reply, discuss_show
from .merge import merge_to_develop
from .utils import C, R, agent_color
from .pipeline import run_pipeline
from .tasks import parse_tasks, update_task_status
from .utils import log, run_cmd, setup_logging


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
            context_parts.append(f"## Структура файлов\n" + "\n".join(file_list))
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
    mode_color = C['cyan'] if cfg.mode == "competitive" else C['magenta']
    print(f"  Режим: {mode_color}{C['bold']}{cfg.mode}{R}")
    print()
    for name, acfg in cfg.agents.items():
        status = f"{C['green']}ON{R}" if acfg.enabled else f"{C['red']}OFF{R}"
        print(f"  {C['bold']}{name}{R}: {status}  ({acfg.command})")
    print(f"\n  Активные: {C['bold']}{cfg.agent_names}{R}")


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
        print(f"  Все агенты на каждую задачу, race-to-merge")
    else:
        print(f"  Задачи распределяются по агентам round-robin")


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
  ./fr run --retry                       Перезапуск упавших (blocked → open)
  ./fr run --auto --max-tasks 4          Авто-цикл: разблокированные → запуск
  ./fr run --dry-run                     Показать что запустится (без запуска)

{Y}ДИСКУССИИ:{R}
  ./fr discuss new <тема> '<вопрос>'     Создать дискуссию
  ./fr discuss chat <тема>               Интерактивный чат
  ./fr discuss list                      Список дискуссий
  ./fr discuss show <тема>               Показать дискуссию целиком
  ./fr discuss reply <тема> --agent X    Пакетный ответ агента (без чата)
  ./fr discuss regen <тема>              Перегенерировать задачи из резолюции

  {DIM}В чате: /help — справка, /help <команда> — подробности, Tab — автодополнение{R}

{Y}КОГНИТИВНЫЕ ФРЕЙМЫ:{R}
  {DIM}Модель + фрейм = агент-специалист. Три qwen с разными фреймами = три перспективы.{R}

  В чате:    /qwen+audit                 Агент с фреймом
  Пакетно:   ./fr discuss reply тема --agent qwen+audit
  Сценарий:  /agent+audit → /agent+wild → /agent+price  {DIM}(проверь → сломай → оцени){R}

  {DIM}Фреймы настраиваются в forgerace.toml секция [frames.*]{R}
  {DIM}Встроенные фреймы: audit, wild, price, theory, evidence, optimizer, meta, vectors{R}

{Y}АГЕНТЫ И РЕЖИМ:{R}
  ./fr agents                            Список агентов и статус (ON/OFF)
  ./fr agents off claude                 Выключить claude
  ./fr agents on claude                  Включить обратно
  ./fr mode                              Текущий режим
  ./fr mode competitive                  Все агенты на каждую задачу (race)
  ./fr mode distributed                  Задачи раскидываются по агентам

{Y}СТАТУС И МЕРЖ:{R}
  ./fr status                            Статус задач + граф зависимостей
  ./fr merge-pending                     Промержить review-задачи в develop

{Y}ОПЦИИ:{R}
  --config PATH                          Путь к forgerace.toml
  --root PATH                            Корневая директория проекта
  --verbose                              Подробный вывод
""")


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
               "  ./fr run                    все готовые задачи\n"
               "  ./fr run --task TASK-032     конкретная задача\n"
               "  ./fr run --retry            перезапуск упавших\n"
               "  ./fr run --auto             авто-цикл разблокированных\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    run_p.add_argument("--task", help="Конкретная задача (TASK-032)")
    run_p.add_argument("--retry", action="store_true", help="Перезапустить упавшие (blocked → open)")
    run_p.add_argument("--dry-run", action="store_true", help="Показать что запустится, без запуска")
    run_p.add_argument("--auto", action="store_true", help="Авто-цикл: разблокированные → запуск")
    run_p.add_argument("--max-tasks", type=int, default=None,
                        help="Макс. задач параллельно (дефолт из TOML)")

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

    # init
    sub.add_parser("init", help="Создать forgerace.toml и TASKS.md")

    # merge-pending
    sub.add_parser("merge-pending", help="Промержить review → develop")

    # status
    sub.add_parser("status", help="Статус задач + граф зависимостей")

    # help
    sub.add_parser("help", help="Полная справка с примерами")

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
