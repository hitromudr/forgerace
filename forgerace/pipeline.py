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
from .utils import log, run_cmd, is_valid_path, C, R, agent_color
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
            try:
                if not workdir.exists():
                    continue
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
            except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
                continue


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

    # Проверяем критерий через check_command из конфига
    if "make check" in (task.acceptance or "") and cfg.check_command:
        result = run_cmd(
            ["bash", "-c", cfg.check_command], cwd=cfg.root_dir,
            timeout=cfg.build_timeout, check=False)
        if result.returncode == 0:
            log.info(f"[{task.id}] pre-check: check_command проходит")
            return True
        return False

    # Без files_new и без make check — не можем определить, выполнена ли задача
    # НЕ закрываем по наличию files_modify или git log — это ложные срабатывания
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
        if task and "make check" in (task.acceptance or "") and cfg.check_command:
            result = run_cmd(
                ["bash", "-c", cfg.check_command], cwd=workdir,
                timeout=cfg.build_timeout, check=False)
            if result.returncode == 0:
                return True, ""
            return False, f"check_command failed:\n{result.stderr}\n{result.stdout}"
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
    metrics = {"binary_size": 0, "code_lines": 0}

    # Считаем lines: сначала по файлам задачи, fallback — весь diff
    paths = task_paths(task) if task else []
    for attempt_paths in ([paths, []] if paths else [[]]):
        diff_cmd = ["git", "diff", "--numstat", cfg.dev_branch]
        if attempt_paths:
            diff_cmd += ["--"] + attempt_paths
        diff_result = run_cmd(diff_cmd, cwd=workdir, check=False)
        if diff_result.returncode == 0:
            for line in diff_result.stdout.strip().splitlines():
                parts = line.split("\t")
                if len(parts) == 3:
                    try:
                        metrics["code_lines"] += int(parts[0])
                    except ValueError:
                        pass
        if metrics["code_lines"] > 0:
            break

    target_dir = workdir / cfg.binary_glob_dir if cfg.binary_glob_dir else None
    if target_dir and target_dir.exists():
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
    try:
        workdir = create_worktree(agent_num, branch)
    except (RuntimeError, Exception) as e:
        log.error(f"[{task.id}/{agent_type}] ✗ Не удалось создать worktree: {e}")
        return AgentResult(agent_type=agent_type, branch=branch,
                           workdir=cfg.agents_dir / f"agent-{agent_num}", success=False)

    tag = f"{task.id}/{agent_type}"
    log.info(f"  ▶ [{tag}] agent-{agent_num}")
    _register_agent(tag, task.id, workdir)

    is_design = task.files_new.startswith("docs/")
    error_log = ""
    from .cost import TokenUsage
    total_usage = TokenUsage()

    for attempt in range(1, cfg.max_retries + 1):
        log.info(f"[{tag}] Попытка {attempt}/{cfg.max_retries}")

        prompt = build_prompt(task, error_log)
        result = run_agent_process(agent_type, workdir, task, prompt,
                                   cancel_event=cancel_event)
        if hasattr(result, "usage") and result.usage:
            total_usage.accumulate(result.usage)

        agent_log = cfg.log_dir / f"{task.id.lower()}-{agent_type}-attempt{attempt}.log"
        agent_log.write_text(
            f"=== STDOUT ===\n{result.stdout or '(empty)'}\n\n=== STDERR ===\n{result.stderr or '(empty)'}\n",
            encoding="utf-8",
        )

        if result.returncode != 0:
            stderr = result.stderr or result.stdout or "Агент упал без вывода"
            combined = f"{result.stdout or ''}\n{stderr}".lower()
            log.warning(f"[{tag}] Агент завершился с ошибкой (код {result.returncode})")
            # NO_EDIT_ABORT / CANCELLED — не ретраим, агент зацикливается
            if stderr in ("NO_EDIT_ABORT", "CANCELLED", "PROGRESS_TIMEOUT"):
                log.error(f"[{tag}] ✗ {stderr} — прекращаю попытки")
                break
            # Quota/auth — ретрай бесполезен
            if any(kw in combined for kw in ("quota exceeded", "rate limit", "unauthorized",
                                              "authentication", "api key", "401", "429")):
                log.error(f"[{tag}] ✗ Квота/авторизация — пропускаю агента")
                break
            error_log = stderr
            continue

        # Коммит — добавляем ВСЕ изменения, не только task_paths
        # (агент мог создать файлы вне указанных путей)
        run_cmd(["git", "add", "-A"], cwd=workdir, check=False)
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
            # Quota/auth в stdout — не ретраить
            combined = f"{stdout_tail}\n{stderr_tail}".lower()
            if any(kw in combined for kw in ("quota exceeded", "rate limit", "unauthorized",
                                              "authentication", "api key", "401", "429")):
                log.error(f"[{tag}] ✗ Квота/авторизация — пропускаю агента")
                break
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
                success=True, usage=total_usage, **metrics,
            )
        else:
            log.warning(f"[{tag}] ✗ сборка провалена:\n{error_log[-500:]}")

    # Проверяем: был ли агент отменён — тогда тихо выходим (уже залогировано в retry loop)
    if cancel_event and cancel_event.is_set():
        _unregister_agent(tag)
        return AgentResult(agent_type=agent_type, branch=branch, workdir=workdir, success=False, usage=total_usage)
    log.error(f"[{tag}] ✗ BLOCKED после {cfg.max_retries} попыток")
    _unregister_agent(tag)
    return AgentResult(agent_type=agent_type, branch=branch, workdir=workdir, success=False, usage=total_usage)


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

    race_winner = None

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

            if race_winner:
                continue  # уже есть победитель, просто собираем результаты

            if not result.success:
                continue

            passed.append(result)
            log.info(f"[{task.id}/{result.agent_type}] lines={result.code_lines}, bin={result.binary_size}")
            if result.code_lines > 500:
                log.warning(f"[{task.id}/{result.agent_type}] ⚠ раздутый diff ({result.code_lines} строк) — возможно переписал файлы целиком")

            # Race: первый финишировавший → ревью ВСЕМИ остальными
            reviewers = [n for n in agent_names if n != result.agent_type]
            if not reviewers:
                reviewers = [result.agent_type]
            diff = get_diff(result, task)
            changed = get_changed_files(result, task)
            log.info(f"[{task.id}] 📝 Ревью: {', '.join(reviewers)} → {result.agent_type}")

            # Ревью параллельно, доработка начинается сразу при NEEDS_WORK
            rework_comments = []
            with ThreadPoolExecutor(max_workers=len(reviewers)) as review_pool:
                review_futures = {}
                for rev in reviewers:
                    f = review_pool.submit(single_review, rev, result.agent_type, diff, task,
                                           build_passed=True, changed_files=changed,
                                           workdir=result.workdir)
                    review_futures[f] = rev
                verdicts = {}
                rework_started = False
                for f in as_completed(review_futures):
                    rev = review_futures[f]
                    rv = f.result()
                    verdicts[rev] = rv
                    log.info(f"[{task.id}] 📋 {rev} ревьюит {result.agent_type}: {rv['verdict']}")
                    log.info(f"[{task.id}] {rv.get('summary', rv.get('comments', '')[:200])}")
                    # Собираем замечания для доработки
                    if rv["verdict"] != "APPROVED":
                        comments = rv.get("comments", rv.get("summary", ""))
                        if comments:
                            rework_comments.append(comments)

            # APPROVED только если ВСЕ ревьюеры одобрили
            all_approved = all(v["verdict"] == "APPROVED" for v in verdicts.values())
            if all_approved:
                log.info(f"[{task.id}] ✅ Ревью пройдено: {result.agent_type}")
                log.info(f"[{task.id}] 🏆 победитель: {result.agent_type}")
                cancel_event.set()  # сигнал остальным агентам на завершение
                # Мержим СРАЗУ, не ждём остальных
                if merge_to_develop(result.branch, task.id):
                    update_task_status(task.id, "done", agent=result.agent_type, branch=result.branch)
                    log.info(f"[{task.id}] ✓ done (вмержен в {cfg.dev_branch})")
                else:
                    update_task_status(task.id, f"review:{result.agent_type}",
                                      agent=result.agent_type, branch=result.branch)
                    log.warning(f"[{task.id}] ⚠ review (мерж не удался)")
                race_winner = result
            else:
                # Сразу отправляем на доработку — не ждём других агентов
                all_comments = "\n\n".join(rework_comments)
                if all_comments:
                    log.info(f"[{task.id}/{result.agent_type}] 🔧 отправлен на доработку сразу")
                    send_to_rework(result, task, all_comments)
                log.info(f"[{task.id}] ⏳ {result.agent_type} NEEDS_WORK, доработка запущена")

    # Все futures завершены — cleanup worktree безопасен
    if race_winner:
        cleanup_worktrees(all_results)
        return True

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
        log.info(f"[{task.id}/{r.agent_type}] lines={r.code_lines}, bin={r.binary_size}")

    # Цикл: ревью → доработка
    best_result = None
    prev_summary = None
    repeat_count = 0
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

        # Детекция зацикливания: одинаковое замечание 2 раунда подряд → эскалация
        cur_summary = rv.get("summary", "").strip()
        if cur_summary and cur_summary == prev_summary:
            repeat_count += 1
            if repeat_count >= 1:
                log.warning(f"[{task.id}] ⚠ Ревьюер зациклился (одно замечание {repeat_count + 1} раунда подряд) → эскалация")
                _escalate_review_stall(task, passed, rv)
                update_task_status(task.id, "blocked")
                cleanup_worktrees(all_results)
                return False
        else:
            repeat_count = 0
        prev_summary = cur_summary

        if rv.get("verdict") == "error" or not rv.get("comments", "").strip():
            log.warning(f"[{task.id}] ⚠ Ревью ошибка/без замечаний — пропускаю раунд")
            continue

        if len(passed) >= 2 and "reviews" in rv:
            # Параллельная доработка — не ждём медленных
            rework_items = []
            for agent_result in passed:
                agent_comments = rv["reviews"].get(agent_result.agent_type, {}).get("comments", "")
                if agent_comments.strip():
                    rework_items.append((agent_result, agent_comments))
            if rework_items:
                with ThreadPoolExecutor(max_workers=len(rework_items)) as rework_pool:
                    rework_futures = {}
                    for agent_result, agent_comments in rework_items:
                        log.info(f"[{task.id}/{agent_result.agent_type}] 🔧 отправлен на доработку")
                        f = rework_pool.submit(send_to_rework, agent_result, task, agent_comments)
                        rework_futures[f] = agent_result.agent_type
                    for f in as_completed(rework_futures):
                        f.result()  # дождаться завершения
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
    if not result.success:
        log.error(f"[{task.id}/{agent_type}] ✗ не написал код → BLOCKED")
        update_task_status(task.id, "blocked")
        cleanup_worktrees([result])
        return False

    log.info(f"[{task.id}/{result.agent_type}] lines={result.code_lines}, bin={result.binary_size}")

    all_agent_names = cfg.agent_names
    reviewer = next((n for n in all_agent_names if n != agent_type), agent_type)
    diff = get_diff(result, task)
    if not diff:
        log.error(f"[{task.id}] ✗ пустой diff → BLOCKED")
        update_task_status(task.id, "blocked")
        cleanup_worktrees([result])
        return False

    best_result = result
    prev_summary = None
    repeat_count = 0
    for review_round in range(1, cfg.max_review_rounds + 1):
        log.info(f"[{task.id}] 📝 Code review (раунд {review_round}/{cfg.max_review_rounds})...")
        log.info(f"[{task.id}] Ревьюер: {reviewer} → {agent_type}")
        rv = single_review(reviewer, agent_type, get_diff(best_result, task), task,
                           build_passed=True, changed_files=get_changed_files(best_result, task),
                           workdir=best_result.workdir)
        log.info(f"[{task.id}] 📋 {reviewer} ревьюит {agent_type}: {rv['verdict']}")
        log.info(f"[{task.id}] {rv.get('summary', rv.get('comments', '')[:200])}")

        if rv["verdict"] == "APPROVED":
            log.info(f"[{task.id}] ✅ Ревью пройдено: {agent_type}")
            break

        # Детекция зацикливания: одинаковое замечание 2 раунда подряд → эскалация
        cur_summary = rv.get("summary", "").strip()
        if cur_summary and cur_summary == prev_summary:
            repeat_count += 1
            if repeat_count >= 1:
                log.warning(f"[{task.id}] ⚠ Ревьюер зациклился (одно замечание {repeat_count + 1} раунда подряд) → эскалация")
                _escalate_review_stall(task, [best_result], rv)
                update_task_status(task.id, "blocked")
                cleanup_worktrees([result])
                return False
        else:
            repeat_count = 0
        prev_summary = cur_summary

        comments = rv.get("comments", "")
        if not comments.strip() or rv.get("verdict") == "error":
            log.warning(f"[{task.id}] ⚠ Ревью без замечаний или ошибка — пропускаю")
            continue
        log.info(f"[{task.id}/{agent_type}] 🔧 отправлен на доработку")
        send_to_rework(best_result, task, comments)
    else:
        log.info(f"[{task.id}] 📝 Финальное ревью...")
        rv = single_review(reviewer, agent_type, get_diff(best_result, task), task,
                           build_passed=True, changed_files=get_changed_files(best_result, task),
                           workdir=best_result.workdir)
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


def _escalate_review_stall(task: Task, results: list, last_rv: dict):
    """Эскалация: ревьюер зациклился. Выводит саммари от всех агентов."""
    print(f"\n{C['red']}{C['bold']}{'═' * 60}{R}")
    print(f"  {C['red']}{C['bold']}⚠ ЭСКАЛАЦИЯ: ревьюер зациклился на {task.id}{R}")
    print(f"{C['red']}{C['bold']}{'═' * 60}{R}")
    print(f"\n  {C['bold']}Задача:{R} {task.id} — {task.name}")
    print(f"  {C['bold']}Описание:{R} {task.description[:200]}")
    print(f"  {C['bold']}Критерий:{R} {task.acceptance[:200]}")
    print()

    # Что сделал каждый агент
    for r in results:
        color = agent_color(r.agent_type)
        print(f"  {color}{C['bold']}@{r.agent_type}{R}:")
        print(f"    Файлов изменено: {r.code_lines} строк")
        if r.branch:
            print(f"    Ветка: {C['dim']}{r.branch}{R}")

    # Замечание ревьюера
    reviewer = last_rv.get("reviewer", "?")
    summary = last_rv.get("summary", last_rv.get("comments", "")[:300])
    print(f"\n  {C['bold']}Замечание ({agent_color(reviewer)}@{reviewer}{R}{C['bold']}):{R}")
    print(f"    {summary}")

    print(f"\n  {C['yellow']}Решение требуется от techlead.{R}")
    print(f"  Варианты: исправить задачу, поменять ревьюера, или approve вручную.")
    print(f"{C['red']}{C['bold']}{'═' * 60}{R}\n")


def preflight_check() -> bool:
    """Проверяет develop на проблемы, пробует собрать через cfg.build_commands."""
    # Проверяем merge conflict маркеры (ищем в src/ если есть, иначе в корне)
    src_dir = cfg.root_dir / "src"
    search_dir = "src/" if src_dir.exists() else "."
    marker = "<" + "<<<<<<"  # split to avoid self-match
    result = run_cmd(["grep", "-rlI", "--exclude-dir=.agents", "--exclude-dir=.git",
                       "--exclude-dir=__pycache__", marker, search_dir],
                      cwd=cfg.root_dir, check=False)
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

Правила маршрутизации (это НЕ баги):
- Одна задача в батче → ВСЕГДА конкурентный режим (оба агента), независимо от сложности. Модели безлимитные.
- Много задач → сложные конкурентно, простые распределённо.
- CANCELLED = агент отменён потому что конкурент победил. Это штатное поведение, НЕ ошибка.

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
        from .agents import run_text_agent
        review = run_text_agent(prompt, timeout=60)
        if review:
            header = f"{C['yellow']}{C['bold']}📋 Ревью прогона:{R}"
            print(f"\n{header}\n{review}\n")
    except Exception as e:
        log.debug(f"Ревью прогона пропущено: {e}")


def _print_flow_guide(tasks: list[Task]):
    """Показывает гайд: что делать дальше в зависимости от состояния задач."""
    hint = run_hint().rsplit(" ", 1)[0]  # без "run"

    if not tasks:
        # Пустой TASKS.md — полный гайд
        print(f"""
  📋 TASKS.md пуст. Флоу работы с ForgeRace:

  1. Добавь задачи в TASKS.md:

     ### TASK-001: Название задачи
     - **Статус**: open
     - **Приоритет**: P1
     - **Зависимости**: —
     - **Файлы (новые)**: path/to/new_file.py
     - **Файлы (modify)**: path/to/existing.py
     - **Описание**: Что сделать
     - **Критерий готовности**: Что должно работать
     - **Дискуссия**: —
     - **Агент**: —
     - **Ветка**: —

  2. (Опционально) Обсуди архитектуру перед реализацией:
     {hint} discuss new my-topic 'Как лучше реализовать X?'
     {hint} discuss chat my-topic

  3. Запусти:  {hint} run

  4. Статус:   {hint} status""")
        return

    # Есть задачи, но нечего запускать — покажем что происходит
    by_status: dict[str, list[Task]] = {}
    for t in tasks:
        s = t.status.split(":")[0]
        by_status.setdefault(s, []).append(t)

    DIM = C["dim"]
    BOLD = C["bold"]
    print()
    failed = by_status.get("failed", [])
    in_progress = by_status.get("in_progress", [])
    review = by_status.get("review", [])
    done = by_status.get("done", [])

    if failed:
        print(f"  {C['red']}❌ Упавшие ({len(failed)}):{R}")
        for t in failed:
            print(f"     {BOLD}{t.id}{R}: {t.name}")
        print(f"     {DIM}→{R} {hint} run --retry")

    if in_progress:
        print(f"  {C['cyan']}▶ В работе ({len(in_progress)}):{R}")
        for t in in_progress:
            a = t.status.split(":", 1)[1] if ":" in t.status else "?"
            print(f"     {BOLD}{t.id}{R}: {t.name} {agent_color(a)}[{a}]{R}")

    if review:
        print(f"  {C['yellow']}⏳ На ревью ({len(review)}):{R}")
        for t in review:
            print(f"     {BOLD}{t.id}{R}: {t.name}")
        print(f"     {DIM}→{R} {hint} merge-pending")

    # Open задачи заблокированные зависимостями
    done_ids = {t.id for t in tasks if t.status == "done"}
    open_blocked = by_status.get("open", [])
    if open_blocked:
        unmet = [(t, [d for d in t.deps if d not in done_ids]) for t in open_blocked]
        unmet = [(t, deps) for t, deps in unmet if deps]
        if unmet:
            print(f"  {DIM}⏸ Open, ждут зависимости ({len(unmet)}):{R}")
            for t, deps in unmet[:5]:
                print(f"     {BOLD}{t.id}{R}: {t.name} {DIM}(ждёт: {', '.join(deps)}){R}")
            if len(unmet) > 5:
                print(f"     {DIM}...и ещё {len(unmet) - 5}{R}")

    if not failed and not in_progress and not review and not open_blocked:
        print(f"  {DIM}ℹ Все {len(done)} задач выполнены. Добавь новые в TASKS.md.{R}")
        print(f"     {DIM}→{R} {hint} run")


def _print_next_steps(tasks: list[Task], max_tasks: int, auto: bool):
    """Выводит следующие шаги."""
    done_ids = {t.id for t in tasks if t.status == "done"}
    blocked = [t for t in tasks if t.status.startswith("blocked")]
    ready = find_ready_tasks(tasks)

    DIM = C["dim"]
    BOLD = C["bold"]
    print(f"\n{C['yellow']}{BOLD}{'═' * 60}{R}")
    print(f"  {C['yellow']}{BOLD}СЛЕДУЮЩИЕ ШАГИ{R}")
    print(f"{C['yellow']}{BOLD}{'═' * 60}{R}")

    has_action = False

    if ready:
        has_action = True
        if auto:
            log.info(f"Есть ещё задачи: {[t.id for t in ready]}. Запускаю следующий цикл.")
            run_pipeline(max_tasks=max_tasks, auto=True)
            return
        print(f"\n  {C['green']}▶ Готовы к запуску ({len(ready)}):{R}")
        for t in ready:
            print(f"    {BOLD}{t.id}{R}: {t.name}")
        print(f"\n    {DIM}→{R} {run_hint()}")

    if blocked:
        has_action = True
        deps_ready = [t for t in blocked if all(d in done_ids for d in t.deps)]
        deps_waiting = [t for t in blocked if not all(d in done_ids for d in t.deps)]
        if deps_ready:
            print(f"\n  {C['yellow']}🔄 Blocked, но зависимости готовы:{R}")
            for t in deps_ready:
                print(f"    {BOLD}{t.id}{R}: {t.name}")
            print(f"\n    {DIM}→{R} {run_hint()}")
        if deps_waiting:
            print(f"\n  {DIM}⏸ Blocked, ждут зависимости:{R}")
            for t in deps_waiting:
                waiting = [d for d in t.deps if d not in done_ids]
                print(f"    {BOLD}{t.id}{R}: {t.name} {DIM}(ждёт: {', '.join(waiting)}){R}")

    if not has_action:
        all_done = tasks and all(t.status == "done" for t in tasks)
        if all_done:
            print("\n  ✅ Все задачи выполнены!")
            check_cmd = cfg.check_command or "make check"
            print(f"\n  🔍 Запускаю {check_cmd}...")
            check_result = run_cmd(
                ["bash", "-c", check_cmd], cwd=cfg.root_dir, timeout=300, check=False)
            if check_result.returncode == 0:
                print(f"  ✅ check PASSED — этап закрыт")
            else:
                stderr = (check_result.stderr or check_result.stdout or "")[-500:]
                print(f"  ❌ check FAILED — создаю задачу на фикс...")
                create_checkpoint_task(stderr)
        else:
            _print_flow_guide(tasks)

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

    # Автозакрытие чекпоинт-задач если check_command проходит
    if cfg.check_command:
        for t in tasks:
            if t.status != "done" and "make check" in (t.acceptance or "") and "чекпоинт" in t.name.lower():
                check_result = run_cmd(["bash", "-c", cfg.check_command],
                                       cwd=cfg.root_dir, timeout=cfg.build_timeout, check=False)
                if check_result.returncode == 0:
                    log.info(f"[{t.id}] ✅ check_command проходит — чекпоинт автозакрыт")
                    update_task_status(t.id, "done", agent="auto-check")
                break  # проверяем один раз

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
            # Лимит авто-retry: считаем run_count по логам
            max_auto_retries = 3
            retryable_stuck = []
            for t in stuck:
                attempt_logs = list(cfg.log_dir.glob(f"{t.id.lower()}-*-attempt*.log"))
                run_count = len(set(f.name.split("-attempt")[0] for f in attempt_logs)) if attempt_logs else 0
                if run_count < max_auto_retries * cfg.max_retries:
                    retryable_stuck.append(t)
                else:
                    log.warning(f"[{t.id}] ⚠ Превышен лимит авто-retry ({run_count} попыток) — пропускаю. Исправь задачу вручную.")
            if retryable_stuck:
                log.info(f"Авто-retry застрявших: {[t.id for t in retryable_stuck]}")
                for t in retryable_stuck:
                    _cleanup_task_branches(t)
                    update_task_status(t.id, "open")
                    t.status = "open"
            tasks = parse_tasks()

        ready = find_ready_tasks(tasks)
        if not ready:
            retryable = find_retryable_tasks(tasks)
            # Тот же лимит
            retryable = [t for t in retryable
                         if len(list(cfg.log_dir.glob(f"{t.id.lower()}-*-attempt*.log"))) < 3 * cfg.max_retries]
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
                for agent_name in cfg.agent_names:
                    print(f"[{agent_name.capitalize()} думает...]")
                    discuss_reply(topic, agent_name)
            print(f"\n{'═' * 60}")
            print(f"  {t.id}: {t.name}")
            print(f"  Обсуди подход и утверди через /ok")
            print(f"{'═' * 60}\n")
            discuss_chat(topic)

        # После дискуссий — выходим. Пользователь запустит run отдельно.
        log.info("Дискуссии завершены. Запусти ./fr run для выполнения задач.")
        tasks = parse_tasks()
        _print_next_steps(tasks, max_tasks, auto)
        log.info("ForgeRace завершён")
        os._exit(0)

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

    # Pre-check ДО параллелизации — последовательно
    actually_ready = []
    for t in ready:
        if check_already_done(t):
            log.info(f"[{t.id}] ✅ Критерий готовности уже выполнен в develop — пропускаю")
            update_task_status(t.id, "done", agent="pre-check")
        else:
            actually_ready.append(t)
    ready = actually_ready

    if not ready:
        log.info("Все задачи уже выполнены (pre-check)")
        tasks = parse_tasks()
        _print_next_steps(tasks, max_tasks, auto)
        return

    log.info(f"Утверждены и готовы: {[t.id for t in ready]}")

    batch = ready[:max_tasks]
    agent_names = cfg.agent_names

    # Все задачи — конкурентный режим (все модели на каждую задачу)
    total_procs = len(batch) * len(agent_names)
    log.info(f"Запускаю: {len(batch)} задач × {len(agent_names)} агентов = {total_procs} процессов")

    with ThreadPoolExecutor(max_workers=max(total_procs, 1)) as pool:
        futures = {}
        for idx, task in enumerate(batch, 1):
            log.info(f"  {task.id} → конкурентный ({' vs '.join(agent_names)})")
            future = pool.submit(execute_task_competitive, task, idx)
            futures[future] = task

        for future in as_completed(futures):
            task = futures[future]
            try:
                ok = future.result()
                status = "done" if ok else "BLOCKED"
                log.info(f"■ {task.id} → {status}")
            except Exception as e:
                log.error(f"■ {task.id} → ОШИБКА: {e}")
                update_task_status(task.id, "blocked")

    if cfg.review_run_log:
        review_run_log()

    # Коммитим статусы
    status_diff = run_cmd(["git", "diff", "--stat", "TASKS.md"], cwd=cfg.root_dir, check=False)
    if status_diff.stdout.strip():
        run_cmd(["git", "add", "TASKS.md"], cwd=cfg.root_dir, check=False)
        run_cmd(["git", "commit", "-m", "update: статусы задач после прогона"], cwd=cfg.root_dir, check=False)
        # git push убран — пуш делает пользователь, не оркестратор

    tasks = parse_tasks()
    _print_next_steps(tasks, max_tasks, auto)

    log.info("ForgeRace завершён")
    os.system("stty sane 2>/dev/null")
    os._exit(0)
