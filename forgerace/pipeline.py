"""Основной пайплайн: запуск агентов, верификация, конкурентный/одиночный режим."""

import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from .agents import AgentResult, build_prompt, run_agent_process
from .config import cfg, run_hint
from .decompose import assess_and_maybe_decompose, create_checkpoint_task
from .merge import ensure_develop_branch, merge_to_develop
from .review import code_review, get_changed_files, get_diff, send_to_rework, single_review
from .tasks import (
    Task, find_ready_tasks, find_retryable_tasks, is_task_approved,
    parse_tasks, task_paths, topic_for_task, translate_slug,
    update_task_status, link_task_discussion,
)
from .utils import log, run_cmd, is_valid_path
from .worktree import cleanup_worktrees, create_worktree, remove_worktree


# --- Heartbeat ---

_active_agents: dict[str, tuple[str, Path, float]] = {}
_active_agents_lock = threading.Lock()


def _heartbeat_loop(interval: int = 15):
    """Фоновый поток: прогресс активных агентов."""
    while True:
        time.sleep(interval)
        with _active_agents_lock:
            agents = dict(_active_agents)
        for tag, (task_id, workdir, start_time) in agents.items():
            elapsed = int(time.time() - start_time)
            mins, secs = divmod(elapsed, 60)
            result = subprocess.run(
                ["git", "diff", "--name-only"],
                cwd=workdir, capture_output=True, text=True, timeout=5,
            )
            files = [f.strip() for f in (result.stdout or "").strip().split("\n") if f.strip()]
            if files:
                files_str = ", ".join(f.rsplit("/", 1)[-1] for f in files[:5])
                if len(files) > 5:
                    files_str += f" (+{len(files) - 5})"
                log.info(f"[{tag}] ⏳ {mins}m{secs:02d}s — правит: {files_str}")
            else:
                log.info(f"[{tag}] ⏳ {mins}m{secs:02d}s — читает код...")


def _start_heartbeat():
    t = threading.Thread(target=_heartbeat_loop, daemon=True)
    t.start()


def _register_agent(tag: str, task_id: str, workdir: Path):
    with _active_agents_lock:
        _active_agents[tag] = (task_id, workdir, time.time())


def _unregister_agent(tag: str):
    with _active_agents_lock:
        _active_agents.pop(tag, None)


# --- Верификация ---

def check_already_done(task: Task) -> bool:
    """Проверяет, выполнен ли критерий готовности задачи уже в develop.
    Проверяет: наличие файлов, сборку, и наличие кода в git log."""
    has_files_requirement = (task.files_new and task.files_new.strip() != "—")

    if has_files_requirement:
        all_exist = True
        for f in task.files_new.split(","):
            f = re.sub(r"\s*\(.*?\)", "", f).strip()
            if f and is_valid_path(f):
                if not (cfg.root_dir / f).exists():
                    all_exist = False
                    break
        if not all_exist:
            return False
        # Файлы есть + сборка проходит → done
        for cmd_list in cfg.build_commands:
            result = run_cmd(cmd_list, cwd=cfg.root_dir, timeout=cfg.build_timeout, check=False)
            if result.returncode != 0:
                return False
        log.info(f"[{task.id}] pre-check: все файлы существуют, сборка проходит")
        return True

    # Нет files_new — проверяем git log на коммиты с ID этой или родительской задачи
    # Это ловит подзадачи декомпозиции чей код уже вмержен
    result = run_cmd(
        ["git", "log", "--oneline", "--all", "--grep", task.id],
        cwd=cfg.root_dir, check=False,
    )
    if result.stdout.strip():
        log.info(f"[{task.id}] pre-check: найдены коммиты в git log")

    # Проверяем критерий "make check"
    if "make check" in (task.acceptance or ""):
        result = run_cmd(["make", "check"], cwd=cfg.root_dir,
                         timeout=cfg.build_timeout, check=False)
        return result.returncode == 0

    # Без files_new и без make check — не можем определить, нужен ли агент
    return False


def verify_build(workdir: Path, task: Task | None = None) -> tuple[bool, str]:
    """Проверяет сборку в worktree."""
    if task and task.files_new and task.files_new.strip() != "—":
        missing = []
        for f in task.files_new.split(","):
            f = f.strip()
            if f and is_valid_path(f) and not (workdir / f).exists():
                missing.append(f)
        if missing:
            return False, f"Файлы задачи не созданы: {', '.join(missing)}"

    diff = run_cmd(["git", "diff", "--stat", cfg.dev_branch], cwd=workdir, check=False)
    has_changes = bool((diff.stdout or "").strip())
    status = run_cmd(["git", "status", "--porcelain"], cwd=workdir, check=False)
    has_new_files = bool((status.stdout or "").strip())

    if not has_changes and not has_new_files:
        if task and "make check" in (task.acceptance or ""):
            result = run_cmd(["make", "check"], cwd=workdir, timeout=cfg.build_timeout, check=False)
            if result.returncode == 0:
                return True, ""
            return False, f"make check failed:\n{result.stderr}\n{result.stdout}"
        return False, "Агент не внёс никаких изменений"

    for cmd in cfg.build_commands:
        result = run_cmd(cmd, cwd=workdir, timeout=cfg.build_timeout, check=False)
        if result.returncode != 0:
            return False, f"{' '.join(cmd)} failed:\n{result.stderr}\n{result.stdout}"

    return True, ""


def verify_design_task(workdir: Path, task: Task) -> tuple[bool, str]:
    """Проверяет дизайн-задачу (наличие файлов)."""
    for f in task.files_new.split(","):
        f = f.strip()
        if f and is_valid_path(f) and not (workdir / f).exists():
            return False, f"Файл не создан: {f}"
    return True, ""


def collect_metrics(workdir: Path, task: Task) -> dict:
    """Собирает метрики реализации."""
    metrics = {"binary_size": 0, "unsafe_count": 0, "code_lines": 0}

    task_files = []
    for files_str in (task.files_new, task.files_modify):
        if files_str and files_str.strip() != "—":
            for f in files_str.split(","):
                f = re.sub(r"\s*\(.*?\)", "", f).strip()
                if f and is_valid_path(f):
                    task_files.append(f)

    for f in task_files:
        filepath = workdir / f
        if filepath.exists() and filepath.is_file():
            content = filepath.read_text(encoding="utf-8", errors="ignore")
            metrics["unsafe_count"] += content.count("unsafe")

    paths = task_paths(task) if task else ["src/"]
    diff_result = run_cmd(
        ["git", "diff", "--numstat", cfg.dev_branch, "--"] + paths,
        cwd=workdir, check=False,
    )
    if diff_result.returncode == 0:
        for line in diff_result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                try:
                    metrics["code_lines"] += int(parts[0])
                except ValueError:
                    pass

    target_dir = workdir / cfg.binary_glob_dir
    if target_dir.exists():
        bins = []
        for pattern in cfg.binary_globs:
            bins.extend(target_dir.glob(pattern))
        if bins:
            metrics["binary_size"] = max(b.stat().st_size for b in bins if b.is_file())

    return metrics


# --- Запуск одного агента ---

def run_single_agent(task: Task, agent_num: int, agent_type: str,
                     cancel_event: "threading.Event | None" = None) -> AgentResult:
    """Запускает одного агента на задачу. cancel_event — для отмены при race."""
    slug = translate_slug(task.name)
    branch = f"task/{task.id.lower()}-{slug}-{agent_type}"
    run_cmd(["git", "branch", "-D", branch], cwd=cfg.root_dir, check=False)
    workdir = create_worktree(agent_num, branch)

    tag = f"{task.id}/{agent_type}"
    log.info(f"  ▶ [{tag}] agent-{agent_num}")
    _register_agent(tag, task.id, workdir)

    is_design = task.files_new.startswith("docs/")
    error_log = ""

    for attempt in range(1, cfg.max_retries + 1):
        log.info(f"[{tag}] Попытка {attempt}/{cfg.max_retries}")

        prompt = build_prompt(task, error_log)
        result = run_agent_process(agent_type, workdir, task, prompt,
                                   cancel_event=cancel_event)

        agent_log = cfg.log_dir / f"{task.id.lower()}-{agent_type}-attempt{attempt}.log"
        agent_log.write_text(
            f"=== STDOUT ===\n{result.stdout or '(empty)'}\n\n=== STDERR ===\n{result.stderr or '(empty)'}\n",
            encoding="utf-8",
        )

        if result.returncode != 0:
            stderr = result.stderr or result.stdout or "Агент упал без вывода"
            log.warning(f"[{tag}] Агент завершился с ошибкой (код {result.returncode})")
            # NO_EDIT_ABORT / CANCELLED — не ретраим, агент зацикливается
            if stderr in ("NO_EDIT_ABORT", "CANCELLED", "PROGRESS_TIMEOUT"):
                log.error(f"[{tag}] ✗ {stderr} — прекращаю попытки")
                break
            error_log = stderr
            continue

        # Коммит
        for p in task_paths(task):
            run_cmd(["git", "add", p], cwd=workdir, check=False)
        diff_stat = run_cmd(["git", "diff", "--cached", "--stat"], cwd=workdir, check=False)
        if diff_stat.stdout.strip():
            run_cmd(
                ["git", "commit", "-m", f"{task.id}: {task.name} [{agent_type}] (попытка {attempt})"],
                cwd=workdir, check=False,
            )

        if is_design:
            ok, error_log = verify_design_task(workdir, task)
        else:
            ok, error_log = verify_build(workdir, task)

        if not ok and "не внёс никаких изменений" in error_log:
            stdout_tail = (result.stdout or "")[-500:].strip()
            stderr_tail = (result.stderr or "")[-300:].strip()
            if stdout_tail:
                log.warning(f"[{tag}] stdout (хвост): {stdout_tail[:200]}")
            if stderr_tail:
                log.warning(f"[{tag}] stderr: {stderr_tail[:200]}")

        if ok:
            log.info(f"[{tag}] ✓ сборка пройдена")
            _unregister_agent(tag)
            metrics = collect_metrics(workdir, task)
            return AgentResult(
                agent_type=agent_type, branch=branch, workdir=workdir,
                success=True, **metrics,
            )
        else:
            log.warning(f"[{tag}] ✗ сборка провалена:\n{error_log[-500:]}")

    log.error(f"[{tag}] ✗ BLOCKED после {cfg.max_retries} попыток")
    _unregister_agent(tag)
    return AgentResult(agent_type=agent_type, branch=branch, workdir=workdir, success=False)


# --- Конкурентный режим ---

def execute_task_competitive(task: Task, task_idx: int) -> bool:
    """Конкурентное выполнение: агенты параллельно, race-to-merge."""
    log.info(f"═══ {task.id}: {task.name} (конкурентный режим) ═══")

    # Pre-check: критерий готовности уже выполнен в develop?
    if check_already_done(task):
        log.info(f"[{task.id}] ✅ Критерий готовности уже выполнен в develop — пропускаю")
        update_task_status(task.id, "done", agent="pre-check")
        return True

    update_task_status(task.id, "in_progress:both")

    agent_names = cfg.agent_names
    all_results = []
    passed = []
    cancel_event = threading.Event()  # сигнал отмены для проигравших

    with ThreadPoolExecutor(max_workers=len(agent_names)) as pool:
        futures = {}
        for i, agent_name in enumerate(agent_names):
            agent_num = task_idx * len(agent_names) + i - (len(agent_names) - 1)
            f = pool.submit(run_single_agent, task, agent_num, agent_name,
                            cancel_event=cancel_event)
            futures[f] = agent_name

        for future in as_completed(futures):
            agent_name = futures[future]
            result = future.result()
            all_results.append(result)

            if not result.success:
                continue
            if not result.code_lines and not get_diff(result, task):
                continue

            passed.append(result)
            log.info(f"[{task.id}/{result.agent_type}] unsafe={result.unsafe_count}, lines={result.code_lines}, bin={result.binary_size}")

            # Race: первый финишировавший → ревью
            reviewer = next((n for n in agent_names if n != result.agent_type), result.agent_type)
            log.info(f"[{task.id}] 📝 Ревью: {reviewer} → {result.agent_type}")
            rv = single_review(reviewer, result.agent_type, get_diff(result, task), task,
                               build_passed=True, changed_files=get_changed_files(result, task))
            log.info(f"[{task.id}] 📋 {reviewer} ревьюит {result.agent_type}: {rv['verdict']}")
            log.info(f"[{task.id}] {rv.get('summary', rv.get('comments', '')[:200])}")

            if rv["verdict"] == "APPROVED":
                log.info(f"[{task.id}] ✅ Ревью пройдено: {result.agent_type}")
                log.info(f"[{task.id}] 🏆 победитель: {result.agent_type}")
                cancel_event.set()  # убиваем проигравших агентов
                if merge_to_develop(result.branch, task.id):
                    update_task_status(task.id, "done", agent=result.agent_type, branch=result.branch)
                    log.info(f"[{task.id}] ✓ done (вмержен в {cfg.dev_branch})")
                else:
                    update_task_status(task.id, f"review:{result.agent_type}",
                                      agent=result.agent_type, branch=result.branch)
                    log.warning(f"[{task.id}] ⚠ review (мерж не удался)")
                cleanup_worktrees(all_results)
                return True

            log.info(f"[{task.id}] ⏳ {result.agent_type} NEEDS_WORK, ждём второго...")

    # Оба завершились, никто не получил APPROVED
    if not passed:
        # Оба агента заблокировались — проблема в задаче, не в агентах
        no_edit = all((r.success is False and not get_diff(r, task)) for r in all_results)
        if no_edit:
            log.error(f"[{task.id}] ✗ ОБА агента не написали код — задача требует уточнения или уже выполнена")
            log.error(f"[{task.id}]   → Проверьте: описание задачи, конфликт с существующим кодом, тесты среды")
        else:
            log.error(f"[{task.id}] ✗ ни один агент не написал рабочий код → BLOCKED")
        update_task_status(task.id, "blocked")
        cleanup_worktrees(all_results)
        return False

    for r in passed:
        log.info(f"[{task.id}/{r.agent_type}] unsafe={r.unsafe_count}, lines={r.code_lines}, bin={r.binary_size}")

    # Цикл: ревью → доработка
    best_result = None
    for review_round in range(1, cfg.max_review_rounds + 1):
        log.info(f"[{task.id}] 📝 Code review (раунд {review_round}/{cfg.max_review_rounds})...")
        rv = code_review(passed, task)

        if rv["verdict"] == "error":
            log.error(f"[{task.id}] ✗ Ревью не удалось: {rv.get('reason', '?')}")
            update_task_status(task.id, "blocked")
            cleanup_worktrees(all_results)
            return False

        log.info(f"[{task.id}] 📝 Результат ревью:\n{rv.get('full_text', rv.get('reason', ''))}")

        best_name = rv.get("best")
        if not best_name or best_name == "none":
            log.warning(f"[{task.id}] ⚠ Ревьюер: оба непригодны — берём {passed[0].agent_type}")
            best_name = passed[0].agent_type

        best_result = next((r for r in passed if r.agent_type.lower() == best_name), None)
        if not best_result:
            log.error(f"[{task.id}] ✗ Ревьюер выбрал '{best_name}', но такого агента нет")
            update_task_status(task.id, "blocked")
            cleanup_worktrees(all_results)
            return False

        if rv["verdict"] == "APPROVED":
            log.info(f"[{task.id}] ✅ Ревью пройдено: {best_result.agent_type}")
            break

        if rv.get("verdict") == "error" or not rv.get("comments", "").strip():
            log.warning(f"[{task.id}] ⚠ Ревью ошибка/без замечаний — пропускаю раунд")
            continue

        if len(passed) >= 2 and "reviews" in rv:
            for agent_result in passed:
                agent_comments = rv["reviews"].get(agent_result.agent_type, {}).get("comments", "")
                if agent_comments.strip():
                    log.info(f"[{task.id}/{agent_result.agent_type}] 🔧 отправлен на доработку")
                    send_to_rework(agent_result, task, agent_comments)
        else:
            comments = rv.get("comments", "")
            log.info(f"[{task.id}/{best_result.agent_type}] 🔧 отправлен на доработку")
            send_to_rework(best_result, task, comments)
            passed = [best_result]
    else:
        # Финальное ревью
        log.info(f"[{task.id}] 📝 Финальное ревью после {cfg.max_review_rounds} раундов...")
        rv = code_review(passed, task)
        log.info(f"[{task.id}] 📝 Результат ревью:\n{rv.get('full_text', rv.get('reason', ''))}")

        best_name = rv.get("best")
        if best_name and best_name != "none":
            best_result = next((r for r in passed if r.agent_type.lower() == best_name), None)
        if best_result and rv.get("verdict") == "APPROVED":
            log.info(f"[{task.id}] ✅ Ревью пройдено (финал): {best_result.agent_type}")
        else:
            log.error(f"[{task.id}] ✗ не прошёл ревью за {cfg.max_review_rounds}+1 раундов → BLOCKED")
            update_task_status(task.id, "blocked")
            cleanup_worktrees(all_results)
            return False

    # Мерж
    log.info(f"[{task.id}] 🏆 победитель: {best_result.agent_type}")
    if merge_to_develop(best_result.branch, task.id):
        update_task_status(task.id, "done", agent=best_result.agent_type, branch=best_result.branch)
        log.info(f"[{task.id}] ✓ done (вмержен в {cfg.dev_branch})")
    else:
        update_task_status(task.id, f"review:{best_result.agent_type}",
                          agent=best_result.agent_type, branch=best_result.branch)
        log.warning(f"[{task.id}] ⚠ review (мерж не удался)")

    cleanup_worktrees(all_results)
    return True


# --- Одиночный режим ---

def execute_task_single(task: Task, task_idx: int, agent_type: str) -> bool:
    """Выполнение одним агентом + ревью другим."""
    log.info(f"═══ {task.id}: {task.name} ({agent_type}, ревью другим) ═══")

    # Pre-check: критерий готовности уже выполнен в develop?
    if check_already_done(task):
        log.info(f"[{task.id}] ✅ Критерий готовности уже выполнен в develop — пропускаю")
        update_task_status(task.id, "done", agent="pre-check")
        return True

    update_task_status(task.id, f"in_progress:{agent_type}")

    result = run_single_agent(task, task_idx, agent_type)
    if not result.success or result.code_lines == 0:
        if not result.success or not get_diff(result, task):
            log.error(f"[{task.id}/{agent_type}] ✗ не написал код → BLOCKED")
            update_task_status(task.id, "blocked")
            cleanup_worktrees([result])
            return False

    log.info(f"[{task.id}/{result.agent_type}] unsafe={result.unsafe_count}, lines={result.code_lines}, bin={result.binary_size}")

    all_agent_names = cfg.agent_names
    reviewer = next((n for n in all_agent_names if n != agent_type), agent_type)
    diff = get_diff(result, task)
    if not diff:
        log.error(f"[{task.id}] ✗ пустой diff → BLOCKED")
        update_task_status(task.id, "blocked")
        cleanup_worktrees([result])
        return False

    best_result = result
    for review_round in range(1, cfg.max_review_rounds + 1):
        log.info(f"[{task.id}] 📝 Code review (раунд {review_round}/{cfg.max_review_rounds})...")
        log.info(f"[{task.id}] Ревьюер: {reviewer} → {agent_type}")
        rv = single_review(reviewer, agent_type, get_diff(best_result, task), task,
                           build_passed=True, changed_files=get_changed_files(best_result, task))
        log.info(f"[{task.id}] 📋 {reviewer} ревьюит {agent_type}: {rv['verdict']}")
        log.info(f"[{task.id}] {rv.get('summary', rv.get('comments', '')[:200])}")

        if rv["verdict"] == "APPROVED":
            log.info(f"[{task.id}] ✅ Ревью пройдено: {agent_type}")
            break

        comments = rv.get("comments", "")
        if not comments.strip() or rv.get("verdict") == "error":
            log.warning(f"[{task.id}] ⚠ Ревью без замечаний или ошибка — пропускаю")
            continue
        log.info(f"[{task.id}/{agent_type}] 🔧 отправлен на доработку")
        send_to_rework(best_result, task, comments)
    else:
        log.info(f"[{task.id}] 📝 Финальное ревью...")
        rv = single_review(reviewer, agent_type, get_diff(best_result, task), task,
                           build_passed=True, changed_files=get_changed_files(best_result, task))
        if rv["verdict"] != "APPROVED":
            log.error(f"[{task.id}] ✗ не прошёл ревью → BLOCKED")
            update_task_status(task.id, "blocked")
            cleanup_worktrees([result])
            return False
        log.info(f"[{task.id}] ✅ Ревью пройдено (финал): {agent_type}")

    # Мерж
    log.info(f"[{task.id}] 🏆 победитель: {agent_type}")
    if merge_to_develop(best_result.branch, task.id):
        update_task_status(task.id, "done", agent=agent_type, branch=best_result.branch)
        log.info(f"[{task.id}] ✓ done (вмержен в {cfg.dev_branch})")
    else:
        update_task_status(task.id, f"review:{agent_type}", agent=agent_type, branch=best_result.branch)
        log.warning(f"[{task.id}] ⚠ review (мерж не удался)")

    cleanup_worktrees([result])
    return True


# --- Планировщик ---

def _cleanup_task_branches(task: Task):
    """Удаляет worktree и ветки предыдущего прогона."""
    slug = translate_slug(task.name)
    for d in cfg.agents_dir.glob("agent-*"):
        if d.is_dir():
            run_cmd(["git", "worktree", "remove", str(d), "--force"], cwd=cfg.root_dir, check=False)
    for agent_type in cfg.agent_names:
        branch = f"task/{task.id.lower()}-{slug}-{agent_type}"
        run_cmd(["git", "branch", "-D", branch], cwd=cfg.root_dir, check=False)


def preflight_check() -> bool:
    """Проверяет develop на проблемы, пробует собрать через cfg.build_commands."""
    # Проверяем merge conflict маркеры (ищем в src/ если есть, иначе в корне)
    src_dir = cfg.root_dir / "src"
    search_dir = "src/" if src_dir.exists() else "."
    result = run_cmd(["grep", "-rl", "<<<<<<<", search_dir], cwd=cfg.root_dir, check=False)
    if result.stdout.strip():
        conflicted = result.stdout.strip().split("\n")
        log.error(f"⚠ Merge conflict маркеры в: {conflicted}")
        log.error("  Разреши конфликты вручную перед запуском")
        return False

    if not cfg.build_commands:
        return True

    # Пробуем первую build-команду для проверки
    for cmd in cfg.build_commands:
        result = run_cmd(cmd, cwd=cfg.root_dir, check=False)
        if result.returncode != 0:
            stderr = result.stderr or ""
            if "error" in stderr.lower():
                log.error(f"⚠ develop не собирается! {' '.join(cmd)} failed")
                log.error(stderr[-500:])
                return False

    return True


def review_run_log():
    """Автопроверка: Claude анализирует лог прогона."""
    log_file = cfg.log_dir / "orchestrator.log"
    if not log_file.exists():
        return

    lines = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    recent = "\n".join(lines[-200:])

    prompt = f"""Ты — ревьюер процесса оркестратора мультиагентной разработки.

Проанализируй лог последнего прогона и выдай КРАТКИЙ вердикт (3-5 строк):
- Есть ли ошибки процесса (не кода, а именно флоу оркестратора)?
- Корректно ли выбран победитель? Метрики адекватны?
- Есть ли повторяющиеся паттерны ошибок?

Если всё ок — напиши "✓ Прогон чистый". Если нет — опиши проблему и предложи фикс.
НЕ пиши код. Только диагноз.

--- ЛОГ ---
{recent}
--- КОНЕЦ ---
"""

    try:
        result = run_cmd(
            ["claude", "-p", prompt, "--output-format", "text"],
            cwd=cfg.root_dir, timeout=60, check=False,
        )
        review = (result.stdout or "").strip()
        if review:
            log.info(f"📋 Ревью прогона:\n{review}")
    except Exception as e:
        log.debug(f"Ревью прогона пропущено: {e}")


def _print_next_steps(tasks: list[Task], max_tasks: int, auto: bool):
    """Выводит следующие шаги."""
    done_ids = {t.id for t in tasks if t.status == "done"}
    blocked = [t for t in tasks if t.status.startswith("blocked")]
    ready = find_ready_tasks(tasks)

    print(f"\n{'═' * 60}")
    print("  СЛЕДУЮЩИЕ ШАГИ")
    print(f"{'═' * 60}")

    has_action = False

    if ready:
        has_action = True
        if auto:
            log.info(f"Есть ещё задачи: {[t.id for t in ready]}. Запускаю следующий цикл.")
            run_pipeline(max_tasks=max_tasks, auto=True)
            return
        print(f"\n  ▶ Готовы к запуску ({len(ready)}):")
        for t in ready:
            print(f"    {t.id}: {t.name}")
        print(f"\n    → {run_hint()}")

    if blocked:
        has_action = True
        deps_ready = [t for t in blocked if all(d in done_ids for d in t.deps)]
        deps_waiting = [t for t in blocked if not all(d in done_ids for d in t.deps)]
        if deps_ready:
            print(f"\n  🔄 Blocked, но зависимости готовы:")
            for t in deps_ready:
                print(f"    {t.id}: {t.name}")
            print(f"\n    → {run_hint()}")
        if deps_waiting:
            print(f"\n  ⏸ Blocked, ждут зависимости:")
            for t in deps_waiting:
                waiting = [d for d in t.deps if d not in done_ids]
                print(f"    {t.id}: {t.name} (ждёт: {', '.join(waiting)})")

    if not has_action:
        all_done = all(t.status == "done" for t in tasks)
        if all_done:
            print("\n  ✅ Все задачи выполнены!")
            print("\n  🔍 Запускаю make check...")
            check_result = run_cmd(["make", "check"], cwd=cfg.root_dir, timeout=300, check=False)
            if check_result.returncode == 0:
                print("  ✅ make check PASSED — этап закрыт")
            else:
                stderr = (check_result.stderr or check_result.stdout or "")[-500:]
                print(f"  ❌ make check FAILED — создаю задачу на фикс...")
                create_checkpoint_task(stderr)
        else:
            print("\n  ℹ Нет задач для выполнения.")

    print(f"{'═' * 60}\n")


# --- Главный пайплайн ---

def run_pipeline(
    specific_task: Optional[str] = None,
    dry_run: bool = False,
    max_tasks: int | None = None,
    retry: bool = False,
    auto: bool = False,
):
    """Основной цикл оркестратора."""
    if max_tasks is None:
        max_tasks = cfg.max_parallel_tasks

    ensure_develop_branch()

    if not dry_run and not preflight_check():
        return

    if not dry_run:
        _start_heartbeat()

    tasks = parse_tasks()
    done_count = sum(1 for t in tasks if t.status == "done")
    open_count = len(tasks) - done_count
    log.info(f"Задачи: {open_count} активных, {done_count} завершённых")

    if specific_task:
        ready = [t for t in tasks if t.id == specific_task and t.status != "done"]
        if not ready:
            log.error(f"Задача {specific_task} не найдена или уже done")
            return
        for t in ready:
            _cleanup_task_branches(t)
            update_task_status(t.id, "open")
            t.status = "open"
    elif retry:
        retryable = find_retryable_tasks(tasks)
        if retryable:
            log.info(f"Retry: сбрасываю {[t.id for t in retryable]} в open")
            for t in retryable:
                _cleanup_task_branches(t)
                update_task_status(t.id, "open")
                t.status = "open"
        tasks = parse_tasks()
        ready = find_ready_tasks(tasks)
    else:
        done_ids = {t.id for t in tasks if t.status == "done"}
        stuck_statuses = ("blocked", "in_progress")
        stuck = [t for t in tasks
                 if any(t.status.startswith(s) for s in stuck_statuses)
                 and all(d in done_ids for d in t.deps)]
        if stuck:
            log.info(f"Авто-retry застрявших: {[t.id for t in stuck]}")
            for t in stuck:
                _cleanup_task_branches(t)
                update_task_status(t.id, "open")
                t.status = "open"
            tasks = parse_tasks()

        ready = find_ready_tasks(tasks)
        if not ready:
            retryable = find_retryable_tasks(tasks)
            if retryable:
                log.info(f"Авто-retry незавершённых: {[t.id for t in retryable]}")
                for t in retryable:
                    update_task_status(t.id, "open")
                    t.status = "open"
                tasks = parse_tasks()
                ready = find_ready_tasks(tasks)

    if not ready:
        log.info("Нет задач для выполнения")
        _print_next_steps(tasks, max_tasks, auto)
        return

    # Дискуссии для неутверждённых
    from .discuss import discuss_create, discuss_reply, discuss_chat

    approved = [t for t in ready if is_task_approved(t)]
    not_approved = [t for t in ready if not is_task_approved(t)]

    if not_approved and dry_run:
        log.info(f"⏸ Не утверждены: {[t.id for t in not_approved]}")

    if not_approved and not dry_run:
        log.info(f"⏸ Требуют обсуждения: {[t.id for t in not_approved]}")
        for t in not_approved:
            topic = topic_for_task(t)
            filepath = cfg.discuss_dir / f"{topic}.md"
            if not filepath.exists():
                discuss_create(topic, t.description, author="techlead")
                link_task_discussion(t.id, topic)
                log.info(f"  Создана дискуссия: {topic}")
                print("[Claude думает...]")
                discuss_reply(topic, "claude")
                print("[Gemini думает...]")
                discuss_reply(topic, "gemini")
            print(f"\n{'═' * 60}")
            print(f"  {t.id}: {t.name}")
            print(f"  Обсуди подход и утверди через /ok")
            print(f"{'═' * 60}\n")
            discuss_chat(topic)
            tasks = parse_tasks()
            if is_task_approved(next((x for x in tasks if x.id == t.id), t)):
                approved.append(t)

    if not approved:
        log.info("Нет утверждённых задач для выполнения")
        return

    # dry-run: только показываем что будет запущено, без мутаций
    if dry_run:
        log.info(f"Утверждены и готовы: {[t.id for t in approved]}")
        for t in approved:
            log.info(f"[DRY RUN] {t.id} ({t.name})")
        return

    # Декомпозиция (мутирует TASKS.md — только при реальном запуске)
    final_ready = []
    decomposed = False
    for t in approved:
        if assess_and_maybe_decompose(t):
            decomposed = True
        else:
            final_ready.append(t)

    if decomposed:
        tasks = parse_tasks()
        new_ready = find_ready_tasks(tasks)
        new_approved = [t for t in new_ready if is_task_approved(t)]
        existing_ids = {t.id for t in final_ready}
        for t in new_approved:
            if t.id not in existing_ids:
                final_ready.append(t)

    ready = final_ready
    if not ready:
        log.info("Нет задач для выполнения (все декомпозированы, ждут зависимости)")
        return

    log.info(f"Утверждены и готовы: {[t.id for t in ready]}")

    batch = ready[:max_tasks]
    agent_names = cfg.agent_names

    # Маршрутизация:
    # - Одна задача → всегда конкурентный (модели безлимитные, race даёт лучшее качество)
    # - Много задач → сложные конкурентно, простые распределённо (параллелизм важнее)
    competitive = []
    distributed = []
    if len(batch) == 1:
        competitive = list(batch)
    else:
        for t in batch:
            task_prefix = t.id.lower()
            has_failures = any(cfg.log_dir.glob(f"{task_prefix}-*-attempt*.log"))
            score = has_failures * 2 + len((t.description or "")) // 500
            if score >= cfg.max_task_complexity:
                competitive.append(t)
            else:
                distributed.append(t)

    total_procs = len(competitive) * len(agent_names) + len(distributed)
    log.info(f"Запускаю: {len(competitive)} конкурентных + {len(distributed)} распределённых = {total_procs} процессов")

    with ThreadPoolExecutor(max_workers=max(total_procs, 1)) as pool:
        futures = {}
        idx = 1

        for task in competitive:
            log.info(f"  {task.id} → конкурентный ({' vs '.join(agent_names)})")
            future = pool.submit(execute_task_competitive, task, idx)
            futures[future] = task
            idx += 1

        for i, task in enumerate(distributed):
            agent = agent_names[i % len(agent_names)]
            log.info(f"  {task.id} → {agent}")
            future = pool.submit(execute_task_single, task, idx, agent)
            futures[future] = task
            idx += 1

        for future in as_completed(futures):
            task = futures[future]
            try:
                ok = future.result()
                status = "done" if ok else "BLOCKED"
                log.info(f"■ {task.id} → {status}")
            except Exception as e:
                log.error(f"■ {task.id} → ОШИБКА: {e}")
                update_task_status(task.id, "blocked")

    review_run_log()

    # Коммитим статусы
    status_diff = run_cmd(["git", "diff", "--stat", "TASKS.md"], cwd=cfg.root_dir, check=False)
    if status_diff.stdout.strip():
        run_cmd(["git", "add", "TASKS.md"], cwd=cfg.root_dir, check=False)
        run_cmd(["git", "commit", "-m", "update: статусы задач после прогона"], cwd=cfg.root_dir, check=False)
        run_cmd(["git", "push"], cwd=cfg.root_dir, check=False)

    tasks = parse_tasks()
    _print_next_steps(tasks, max_tasks, auto)

    try:
        os.system("stty sane 2>/dev/null")
    except Exception:
        pass
