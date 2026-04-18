"""Основной пайплайн: запуск агентов, верификация, конкурентный/одиночный режим."""

import atexit
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

# Optional dependency for CPU/IO metrics
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

from .agents import AgentResult, build_prompt, run_agent_process, build_post_mortem_prompt, run_text_agent
from .benchmark import BenchmarkStore
from .config import cfg, run_hint, run_hook
from .cost import TokenUsage
from .decompose import assess_and_maybe_decompose, create_checkpoint_task, get_task_complexity
from .merge import merge_to_develop
from .review import code_review, get_changed_files, get_diff, send_to_rework, single_review
from .tasks import (
    Task, find_ready_tasks, find_retryable_tasks, is_task_approved,
    parse_tasks, task_paths, topic_for_task, translate_slug,
    update_task_status, link_task_discussion,
)
from .types import MergeResult
from .utils import log, run_cmd, is_valid_path, C, R, agent_color, parse_pytest_output
from .worktree import cleanup_worktrees, create_worktree


# --- Heartbeat ---

@dataclass
class AgentStatus:
    """Статус активного агента для heartbeat и dashboard."""
    task_id: str
    agent_type: str
    workdir: Path
    start_time: float
    usage: TokenUsage = field(default_factory=TokenUsage)
    elapsed_sec: int = 0
    files_changed: list[str] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        return self.usage.estimated_usd


_active_agents: dict[str, AgentStatus] = {}
_active_agents_lock = threading.Lock()
_perf_stats: dict[int, list[dict]] = {}
_heartbeat_stop_event = threading.Event()
_heartbeat_thread: threading.Thread | None = None

# --- Round-robin ---
_rr_agent_index = 0


def _heartbeat_loop(interval: int = 15):
    """Фоновый поток: прогресс активных агентов."""
    while not _heartbeat_stop_event.is_set():
        time.sleep(interval)
        with _active_agents_lock:
            agents = dict(_active_agents)
        
        max_diff_duration = 0.0
        
        for tag, status in agents.items():
            status.elapsed_sec = int(time.time() - status.start_time)
            mins, secs = divmod(status.elapsed_sec, 60)
            try:
                if not status.workdir.exists():
                    continue
                
                t0 = time.time()
                result = subprocess.run(
                    ["git", "diff", "--name-only"],
                    cwd=status.workdir, capture_output=True, text=True, timeout=5,
                )
                duration = time.time() - t0
                max_diff_duration = max(max_diff_duration, duration)
                
                files = [f.strip() for f in (result.stdout or "").strip().split("\n") if f.strip()]
                status.files_changed = files
                
                if files:
                    files_str = ", ".join(f.rsplit("/", 1)[-1] for f in files[:5])
                    if len(files) > 5:
                        files_str += f" (+{len(files) - 5})"
                    log.info(f"[{tag}] ⏳ {mins}m{secs:02d}s (${status.cost_usd:.2f}) — правит: {files_str}")
                else:
                    log.info(f"[{tag}] ⏳ {mins}m{secs:02d}s (${status.cost_usd:.2f}) — читает код...")
            except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
                continue
                
        # Агрегация системных метрик (снаружи цикла, один раз за heartbeat)
        if HAS_PSUTIL:
            active_count = len(agents)
            if active_count not in _perf_stats:
                _perf_stats[active_count] = []
            
            perf_metrics = {
                "cpu_percent": psutil.cpu_percent(),
                "io_read": psutil.disk_io_counters().read_bytes if hasattr(psutil, "disk_io_counters") else 0
            }
            
            _perf_stats[active_count].append({
                "time": time.time(),
                "diff_duration": max_diff_duration,
                "cpu": perf_metrics["cpu_percent"],
                "io_read": perf_metrics["io_read"],
            })
            # Ограничиваем размер истории
            if len(_perf_stats[active_count]) > 100:
                _perf_stats[active_count] = _perf_stats[active_count][-100:]


def _start_heartbeat():
    global _heartbeat_thread
    t = threading.Thread(target=_heartbeat_loop, daemon=True)
    _heartbeat_thread = t
    t.start()

def _stop_heartbeat():
    """Останавливает фоновый поток heartbeat."""
    global _heartbeat_thread
    if _heartbeat_thread and _heartbeat_thread.is_alive():
        _heartbeat_stop_event.set()
        _heartbeat_thread.join(timeout=5)
        _heartbeat_thread = None


def _register_agent(tag: str, task_id: str, workdir: Path, agent_type: str):
    with _active_agents_lock:
        _active_agents[tag] = AgentStatus(
            task_id=task_id,
            agent_type=agent_type,
            workdir=workdir,
            start_time=time.time()
        )
        pass  # dashboard integration TBD


def _unregister_agent(tag: str):
    """Удаляет агента из _active_agents.

    Потокобезопасно: удаление под lock.
    """
    with _active_agents_lock:
        _active_agents.pop(tag, None)


def _create_checkpoint_task_with_log(stderr: str):
    """Обертка над create_checkpoint_task с доп. логированием (TASK-178)."""
    tasks_before = parse_tasks()
    already_exists = any("make check" in (t.acceptance or "") and t.status != "done" for t in tasks_before)

    create_checkpoint_task(stderr)

    if not already_exists:
        # Находим только что созданную задачу
        tasks_after = parse_tasks()
        new_task = next((t for t in reversed(tasks_after) if "Чекпоинт" in t.name), None)
        if new_task:
            log.info(f"🛠️ [{new_task.id}] Создана задача‑фикc: {new_task.name}")


# --- Верификация ---

def _log_test_results(result: subprocess.CompletedProcess | MergeResult, task: Task | None, cmd_str: str):
    """Парсит и логирует результаты тестов (TASK-177)."""
    output = (result.stdout or "") + (result.stderr or "")
    failed_tests = parse_pytest_output(output)
    task_id_prefix = f"[{task.id}] " if task else ""
    if failed_tests:
        tests_str = ", ".join(failed_tests)
        log.warning(f"{task_id_prefix}❌ Упали тесты: {tests_str}")
    elif result.returncode == 0:
        # Логируем успех только если это явно тесты (pytest или check_command)
        is_pytest = "pytest" in cmd_str.lower()
        is_check = cfg.check_command and cfg.check_command in cmd_str
        if is_pytest or is_check:
            log.info(f"✅ {task_id_prefix}Все тесты прошли")
    else:
        # returncode != 0, но failed_tests пуст. Логируем общую ошибку для тестов.
        is_pytest = "pytest" in cmd_str.lower()
        is_check = cfg.check_command and cfg.check_command in cmd_str
        if is_pytest or is_check:
            log.warning(f"❌ {task_id_prefix}Команда тестов завершилась с ошибкой (код {result.returncode}), но список упавших тестов пуст")


def check_already_done(task: Task) -> bool:
    """Проверяет, выполнен ли критерий готовности задачи уже в develop.
    Only for tasks with NEW files — modify-only tasks always need work."""
    has_files_requirement = (task.files_new and task.files_new.strip() not in ("—", "")
                             and task.files_new.strip() != "forgerace/cli.py")  # generic modify
    if not has_files_requirement:
        return False  # modify-only tasks can't be pre-checked

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
            _log_test_results(result, task, " ".join(cmd_list))
            if result.returncode != 0:
                return False
        log.info(f"[{task.id}] pre-check: все файлы существуют, сборка проходит")
        return True

    # Проверяем критерий через check_command из конфига
    if "make check" in (task.acceptance or "") and cfg.check_command:
        result = run_cmd(
            ["bash", "-c", cfg.check_command], cwd=cfg.root_dir,
            timeout=cfg.build_timeout, check=False)
        _log_test_results(result, task, cfg.check_command)
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

    # Reject changes to protected orchestrator files
    _PROTECTED = {"TASKS.md", "forgerace.toml", "litellm_config.yaml", ".gitignore", "CLAUDE.md"}
    changed_files = run_cmd(["git", "diff", "--name-only", cfg.dev_branch], cwd=workdir, check=False)
    for f in (changed_files.stdout or "").strip().split("\n"):
        if f.strip() in _PROTECTED:
            # Revert protected file
            run_cmd(["git", "checkout", cfg.dev_branch, "--", f.strip()], cwd=workdir, check=False)
            log.warning(f"⚠ Reverted protected file: {f.strip()}")

    if not has_changes and not has_new_files:
        if task and "make check" in (task.acceptance or "") and cfg.check_command:
            result = run_cmd(
                ["bash", "-c", cfg.check_command], cwd=workdir,
                timeout=cfg.build_timeout, check=False)
            _log_test_results(result, task, cfg.check_command)
            if result.returncode == 0:
                return True, ""
            return False, f"check_command failed:\n{result.stderr}\n{result.stdout}"
        return False, "Агент не внёс никаких изменений"

    # Авто-фикс линтером (lint_fix) — до build_commands
    if cfg.lint_commands:
        for cmd in cfg.lint_commands:
            run_cmd(cmd, cwd=workdir, timeout=cfg.build_timeout, check=False)
        # Коммитим изменения линтера (если есть)
        lint_status = run_cmd(["git", "status", "--porcelain"], cwd=workdir, check=False)
        if (lint_status.stdout or "").strip():
            run_cmd(["git", "add", "-A"], cwd=workdir, check=False)
            run_cmd(["git", "commit", "-m", "style: auto-fix lint"], cwd=workdir, check=False)
            log.info("  🔧 Lint auto-fix applied")

    for cmd in cfg.build_commands:
        result = run_cmd(cmd, cwd=workdir, timeout=cfg.build_timeout, check=False)
        _log_test_results(result, task, " ".join(cmd))
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


def _get_usage_cost(usage: TokenUsage, agent_type: str) -> float:
    """Считает стоимость в USD на основе типа агента."""
    p = cfg.pricing
    # Если стоимость уже посчитана (пришла из API), возвращаем её
    if usage.estimated_usd > 0:
        return usage.estimated_usd

    # Иначе считаем по тарифам из конфига
    if agent_type == "gemini":
        return usage.calc_cost(p.gemini_input, p.gemini_output)
    if agent_type == "qwen":
        return usage.calc_cost(p.qwen_input, p.qwen_output)
    if agent_type == "codex":
        return usage.calc_cost(p.codex_input, p.codex_output)
    return usage.calc_cost(p.claude_input, p.claude_output)


def _log_total_cost(task_id: str, results: list[AgentResult]):
    total_cost = sum(_get_usage_cost(r.usage, r.agent_type) for r in results)
    log.info(f"[{task_id}] Итоговая стоимость: ${total_cost:.2f}")


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
    _register_agent(tag, task.id, workdir, agent_type)

    is_design = task.files_new.startswith("docs/")
    error_log = ""
    total_usage = TokenUsage()

    for attempt in range(1, cfg.max_retries + 1):
        log.info(f"[{tag}/код] попытка {attempt}/{cfg.max_retries}")

        prompt = build_prompt(task, error_log, agent_type=agent_type)
        result = run_agent_process(agent_type, workdir, task, prompt,
                                   cancel_event=cancel_event)
        if hasattr(result, "usage") and result.usage:
            total_usage.accumulate(result.usage)
            with _active_agents_lock:
                if tag in _active_agents:
                    _active_agents[tag].usage = total_usage

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
            # Quota/auth — check stderr only (stdout may contain code with these words)
            stderr_lower = (result.stderr or "").lower()
            if any(kw in stderr_lower for kw in ("quota exceeded", "rate limit",
                                                   "api key", "429")):
                from .agents import _disabled_agents
                _disabled_agents.add(agent_type)
                log.error(f"[{tag}] ✗ Квота/авторизация — агент отключён до конца прогона")
                break
            error_log = stderr
            continue

        # Restore protected files: unmark assume-unchanged, restore from base branch
        for pf in ("TASKS.md", "forgerace.toml", "litellm_config.yaml",
                    "CLAUDE.md", ".gitignore"):
            run_cmd(["git", "update-index", "--no-assume-unchanged", pf],
                    cwd=workdir, check=False)
            run_cmd(["git", "checkout", cfg.dev_branch, "--", pf],
                    cwd=workdir, check=False)
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
            # Quota/auth — check stderr only (stdout may contain code with these words)
            if any(kw in (stderr_tail or "").lower() for kw in ("quota exceeded", "rate limit",
                                                                  "api key", "429")):
                from .agents import _disabled_agents
                _disabled_agents.add(agent_type)
                log.error(f"[{tag}] ✗ Квота/авторизация — агент отключён до конца прогона")
                break
            if stdout_tail:
                log.warning(f"[{tag}] stdout (хвост): {stdout_tail[:200]}")
            if stderr_tail:
                log.warning(f"[{tag}] stderr: {stderr_tail[:200]}")

        if ok:
            log.info(f"[{tag}/код] ✓ сборка ок, ждём ревью")
            _unregister_agent(tag)
            metrics = collect_metrics(workdir, task)
            cost = _get_usage_cost(total_usage, agent_type)
            log.info(f"[{tag}] Стоимость: ${cost:.2f}")
            return AgentResult(
                agent_type=agent_type, branch=branch, workdir=workdir,
                success=True, usage=total_usage, **metrics,
            )
        else:
            log.warning(f"[{tag}/код] ✗ сборка провалена:\n{error_log[-500:]}")

    # Проверяем: был ли агент отменён — тогда тихо выходим (уже залогировано в retry loop)
    if cancel_event and cancel_event.is_set():
        _unregister_agent(tag)
        cost = _get_usage_cost(total_usage, agent_type)
        log.info(f"[{tag}] Стоимость: ${cost:.2f}")
        return AgentResult(agent_type=agent_type, branch=branch, workdir=workdir, success=False, usage=total_usage)
    log.error(f"[{tag}] ✗ BLOCKED после {cfg.max_retries} попыток")
    _unregister_agent(tag)
    cost = _get_usage_cost(total_usage, agent_type)
    log.info(f"[{tag}] Стоимость: ${cost:.2f}")
    return AgentResult(agent_type=agent_type, branch=branch, workdir=workdir, success=False, usage=total_usage)


# --- Конкурентный режим ---

def execute_task_competitive(task: Task, task_idx: int) -> bool:
    """Конкурентное выполнение: агенты параллельно, race-to-merge."""
    log.info(f"═══ {task.id}: {task.name} (конкурентный режим) ═══")

    # Pre-check: критерий готовности уже выполнен в develop?
    if check_already_done(task):
        log.info(f"[{task.id}] ✅ Критерий готовности уже выполнен в develop — пропускаю")
        update_task_status(task.id, "done", agent="pre-check")
        run_hook(cfg.hook_on_complete, task.id, "done", "pre-check")
        return True

    from .agents import is_agent_disabled
    # If task has assigned agent(s), use only them (leader's choice)
    if task.agent and task.agent not in ("—", ""):
        # Parse: "@gemini", "gemini", "@qwen-api+theory" → "qwen-api"
        raw = [a.strip().lstrip("@").split("+")[0]
               for a in task.agent.replace(",", " ").split()
               if a.strip() not in ("—", "")]
        # Only use CLI agents (can write code); skip API-only agents
        assigned = [a for a in raw
                    if a in cfg.cli_agent_names and not is_agent_disabled(a)]
        if assigned:
            agent_names = assigned
            log.info(f"[{task.id}] Назначенные агенты: {agent_names} [причина: явное указание]")
        else:
            # Fallback: assigned agent can't code → competitive with all CLI agents
            agent_names = [n for n in cfg.cli_agent_names if not is_agent_disabled(n)]
            if raw:
                log.warning(f"[{task.id}] Назначенные {raw} не могут кодить → competitive [причина: порог сложности (fallback)]")
    else:
        agent_names = [n for n in cfg.cli_agent_names if not is_agent_disabled(n)]
    if not agent_names:
        log.error(f"[{task.id}] ✗ Нет доступных CLI-агентов")
        update_task_status(task.id, "blocked")
        return False

    update_task_status(task.id, f"in_progress:{','.join(agent_names)}")
    all_results = []
    passed = []
    cancel_event = threading.Event()  # сигнал отмены для проигравших

    race_winner = None

    # Инициализация метрик
    start_time = time.perf_counter()
    review_rounds = 0
    total_cost = 0.0

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

            # Race: первый финишировавший → ревью другими (или self+frame) (TASK-095)
            from .agents import is_agent_disabled
            reviewers = [n for n in cfg.agent_names
                         if n != result.agent_type and not is_agent_disabled(n)]
            if not reviewers:
                if cfg.review_frame and cfg.review_frame in cfg.frames:
                    reviewers = [f"{result.agent_type}+{cfg.review_frame}"]
                    log.info(f"    Fallback: саморевью через +{cfg.review_frame}")
                else:
                    reviewers = [result.agent_type]
            diff = get_diff(result, task)
            changed = get_changed_files(result, task)
            log.info(f"[{task.id}/ревью] {', '.join(reviewers)} проверяют {result.agent_type}")

            # Ревью параллельно, доработка начинается сразу при NEEDS_WORK/REJECTED
            rework_comments = []
            with ThreadPoolExecutor(max_workers=len(reviewers)) as review_pool:
                review_futures = {}
                for rev in reviewers:
                    f = review_pool.submit(single_review, rev, result.agent_type, diff, task,
                                           build_passed=True, changed_files=changed,
                                           workdir=result.workdir)
                    review_futures[f] = rev
                verdicts = {}
                for f in as_completed(review_futures):
                    rev = review_futures[f]
                    rv = f.result()
                    verdicts[rev] = rv
                    verdict = rv.get("verdict", "FAILED").upper()
                    log.info(f"[{task.id}/{rev}/ревью] → {result.agent_type}: {verdict}")
                    summary = rv.get('summary', rv.get('comments', '')[:200])
                    if summary:
                        log.info(f"[{task.id}/{rev}/ревью] {summary}")
                    
                    # Собираем замечания для доработки (поддержка NEEDS_WORK, NEEDS_REWORK, REJECTED) (TASK-095)
                    if verdict != "APPROVED":
                        comments = rv.get("comments", rv.get("summary", ""))
                        if comments:
                            rework_comments.append(f"### Замечания от {rev} (вердикт: {verdict})\n{comments}")

            # APPROVED только если ВСЕ ревьюеры одобрили
            all_approved = all(v.get("verdict") == "APPROVED" for v in verdicts.values())
            
            # Проверка на терминальный отказ (TASK-051)
            is_terminal = any(v.get("is_terminal") for v in verdicts.values())
            if not all_approved and is_terminal:
                log.error(f"[{task.id}/{result.agent_type}/ревью] ✗ ТЕРМИНАЛЬНЫЙ ОТКАЗ → BLOCKED")
                update_task_status(task.id, "blocked", agent=result.agent_type)
                # Сохраняем замечания для истории (TASK-095)
                all_comments = "\n\n".join(rework_comments)
                if all_comments:
                    task.last_attempts.append({
                        "comments": all_comments,
                        "timestamp": int(time.time())
                    })
                continue

            if all_approved:
                log.info(f"[{task.id}/{result.agent_type}/ревью] ✅ одобрено")
                log.info(f"[{task.id}/{result.agent_type}/мерж] 🏆 победитель")
                cancel_event.set()  # сигнал остальным агентам на завершение
                # Мержим СРАЗУ, не ждём остальных
                m_res = merge_to_develop(result.branch, task.id)
                if m_res.success:
                    update_task_status(task.id, "done", agent=result.agent_type, branch=result.branch)
                    run_hook(cfg.hook_on_complete, task.id, "done", result.agent_type)
                    log.info(f"[{task.id}] ✓ done (вмержен в {cfg.dev_branch})")
                else:
                    update_task_status(task.id, f"review:{result.agent_type}",
                                      agent=result.agent_type, branch=result.branch)
                    run_hook(cfg.hook_on_complete, task.id, f"review:{result.agent_type}", result.agent_type)
                    log.warning(f"[{task.id}] ⚠ review (мерж не удался)")
                    if m_res.is_test_failure:
                         _log_test_results(m_res, task, cfg.check_command)
                         rework_msg = f"Тесты провалены при мерже в {cfg.dev_branch}:\n{m_res.test_stderr or m_res.test_stdout}"
                         log.info(f"[{task.id}/{result.agent_type}/доработка] отправлен на исправление (тесты провалены при мерже)")
                         send_to_rework(result, task, rework_msg)
                    else:
                         rework_msg = f"Ошибка мержа в {cfg.dev_branch} (конфликт?):\n{m_res.merge_stderr or m_res.merge_stdout}"
                         log.info(f"[{task.id}/{result.agent_type}/доработка] отправлен на исправление (ошибка мержа)")
                         send_to_rework(result, task, rework_msg)
                race_winner = result
            else:
                # Проверка лимита переделок (TASK-058/095)
                max_reworks = getattr(cfg, "max_reworks", 3)
                if task.rework_count >= max_reworks:
                    log.error(f"[{task.id}/{result.agent_type}] ✗ Превышен лимит правок ({max_reworks}) → STUCK")
                    update_task_status(task.id, "stuck", agent=result.agent_type, branch=result.branch)
                    continue

                # Сразу отправляем на доработку — не ждём других агентов
                all_comments = "\n\n".join(rework_comments)
                if all_comments:
                    log.info(f"[{task.id}/{result.agent_type}/доработка] {C['yellow']}отправлен на исправление{R}")
                    send_to_rework(result, task, all_comments)
                else:
                    # Если нет комментариев, но вердикт не APPROVED, всё равно отправляем на доработку с заглушкой
                    log.warning(f"[{task.id}/{result.agent_type}/доработка] Вердикт не APPROVED, но нет комментариев. Отправляем на доработку.")
                    send_to_rework(result, task, "### Замечания\nНет конкретных комментариев от ревьюеров, но код не одобрен.")

    # Все futures завершены — cleanup worktree безопасен
    if race_winner:
        _log_total_cost(task.id, all_results)
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
        run_hook(cfg.hook_on_complete, task.id, "blocked", "none")
        _log_total_cost(task.id, all_results)
        cleanup_worktrees(all_results)
        return False

    for r in passed:
        log.info(f"[{task.id}/{r.agent_type}] lines={r.code_lines}, bin={r.binary_size}")

    # Цикл: ревью → доработка
    best_result = None
    prev_summary = None
    repeat_count = 0
    for review_round in range(1, cfg.max_review_rounds + 1):
        log.info(f"[{task.id}/ревью] раунд {review_round}/{cfg.max_review_rounds}")
        rv = code_review(passed, task)

        if rv["verdict"] == "error":
            log.error(f"[{task.id}] ✗ Ревью не удалось: {rv.get('reason', '?')}")
            update_task_status(task.id, "blocked")
            run_hook(cfg.hook_on_complete, task.id, "blocked", "none")
            _log_total_cost(task.id, all_results)
            cleanup_worktrees(all_results)
            return False

        log.info(f"[{task.id}/ревью] результат:\n{rv.get('full_text', rv.get('reason', ''))}")

        best_name = rv.get("best")
        if not best_name or best_name == "none":
            log.warning(f"[{task.id}] ⚠ Ревьюер: оба непригодны — берём {passed[0].agent_type}")
            best_name = passed[0].agent_type

        best_result = next((r for r in passed if r.agent_type.lower() == best_name), None)
        if not best_result:
            log.error(f"[{task.id}] ✗ Ревьюер выбрал '{best_name}', но такого агента нет")
            update_task_status(task.id, "blocked")
            run_hook(cfg.hook_on_complete, task.id, "blocked", "none")
            _log_total_cost(task.id, all_results)
            cleanup_worktrees(all_results)
            return False

        if rv["verdict"] == "APPROVED":
            log.info(f"[{task.id}/{best_result.agent_type}/ревью] ✅ одобрено")
            break

        # Проверка на терминальный отказ (TASK-051)
        if rv.get("is_terminal"):
            log.error(f"[{task.id}] ✗ ТЕРМИНАЛЬНЫЙ ОТКАЗ ({rv.get('verdict')}) → BLOCKED")
            update_task_status(task.id, "blocked")
            # Сохраняем замечания для истории (TASK-095)
            # Собираем все комментарии из всех ревьюеров, если они есть
            all_comments = "\n\n".join(rework_comments)
            if not all_comments:
                all_comments = rv.get("comments", rv.get("summary", ""))
            if all_comments:
                task.last_attempts.append({
                    "comments": all_comments,
                    "timestamp": int(time.time())
                })
            run_hook(cfg.hook_on_complete, task.id, "blocked", "none")
            _log_total_cost(task.id, all_results)
            cleanup_worktrees(all_results)
            return False

        # Проверка лимита переделок (TASK-058/095)
        max_reworks = getattr(cfg, "max_reworks", 3)
        if task.rework_count >= max_reworks:
            log.error(f"[{task.id}] ✗ Превышен лимит правок ({max_reworks}) → STUCK")
            update_task_status(task.id, "stuck")
            # Сохраняем замечания для истории (TASK-095)
            # Собираем все комментарии из всех ревьюеров, если они есть
            all_comments = "\n\n".join(rework_comments)
            if not all_comments:
                all_comments = rv.get("comments", rv.get("summary", ""))
            if all_comments:
                task.last_attempts.append({
                    "comments": all_comments,
                    "timestamp": int(time.time())
                })
            run_hook(cfg.hook_on_complete, task.id, "stuck", "none")
            _log_total_cost(task.id, all_results)
            cleanup_worktrees(all_results)
            return False

        # Детекция зацикливания: одинаковое замечание 2 раунда подряд → эскалация
        cur_summary = rv.get("summary", "").strip()
        if cur_summary and cur_summary == prev_summary:
            repeat_count += 1
            if repeat_count >= 1:
                log.warning(f"[{task.id}] ⚠ Ревьюер зациклился (одно замечание {repeat_count + 1} раунда подряд) → эскалация")
                _escalate_review_stall(task, passed, rv)
                update_task_status(task.id, "blocked")
                run_hook(cfg.hook_on_complete, task.id, "blocked", "none")
                _log_total_cost(task.id, all_results)
                cleanup_worktrees(all_results)
                return False
        else:
            repeat_count = 0
        prev_summary = cur_summary

        _raw_comments = rv.get("comments", rv.get("summary", ""))
        if isinstance(_raw_comments, list):
            _raw_comments = "\n".join(str(c) for c in _raw_comments)
        comments_combined = str(_raw_comments).strip()
        if rv.get("verdict") == "error" or not comments_combined:
            log.warning(f"[{task.id}] ⚠ Ревью ошибка/без замечаний — пропускаю раунд")
            continue

        if len(passed) >= 2 and "reviews" in rv:
            # Параллельная доработка — не ждём медленных
            rework_items = []
            for agent_result in passed:
                rev_obj = rv["reviews"].get(agent_result.agent_type, {})
                agent_verdict = rev_obj.get("verdict", "FAILED").upper()
                agent_comments = rev_obj.get("comments", rev_obj.get("summary", ""))
                # Собираем замечания для доработки (поддержка NEEDS_WORK, NEEDS_REWORK, REJECTED) (TASK-095)
                if agent_verdict != "APPROVED" and agent_comments.strip():
                    reviewer_tag = rev_obj.get("reviewer", "ревьюер")
                    rework_items.append((agent_result, f"### Замечания от {reviewer_tag} (вердикт: {agent_verdict})\n{agent_comments}"))
                elif agent_verdict != "APPROVED" and not agent_comments.strip():
                    # Если вердикт не APPROVED, но нет комментариев, добавляем заглушку
                    reviewer_tag = rev_obj.get("reviewer", "ревьюер")
                    rework_items.append((agent_result, f"### Замечания от {reviewer_tag} (вердикт: {agent_verdict})\nНет конкретных комментариев."))
            
            if rework_items:
                with ThreadPoolExecutor(max_workers=len(rework_items)) as rework_pool:
                    rework_futures = {}
                    for agent_result, agent_comments in rework_items:
                        log.info(f"[{task.id}/{agent_result.agent_type}/доработка] отправлен на исправление")
                        f = rework_pool.submit(send_to_rework, agent_result, task, agent_comments)
                        rework_futures[f] = (agent_result, agent_comments)
                    for f in as_completed(rework_futures):
                        agent_result, agent_comments = rework_futures[f]
                        f.result()  # дождаться завершения
        else:
            comments = rv.get("comments", rv.get("summary", ""))
            # Собираем замечания от ревьюера (поддержка NEEDS_WORK, NEEDS_REWORK, REJECTED) (TASK-095)
            if rv.get("verdict") != "APPROVED" and comments.strip():
                reviewer_tag = rv.get("reviewer", "ревьюер")
                verdict_tag = rv.get("verdict", "NEEDS_REWORK")
                comments = f"### Замечания от {reviewer_tag} (вердикт: {verdict_tag})\n{comments}"
            
            log.info(f"[{task.id}/{best_result.agent_type}/доработка] {C['yellow']}отправлен на исправление{R}")
            send_to_rework(best_result, task, comments)
            passed = [best_result]
    else:
        # Финальное ревью
        log.info(f"[{task.id}] 📝 Финальное ревью после {cfg.max_review_rounds} раундов...")
        rv = code_review(passed, task)
        log.info(f"[{task.id}/ревью] результат:\n{rv.get('full_text', rv.get('reason', ''))}")

        best_name = rv.get("best")
        if best_name and best_name != "none":
            best_result = next((r for r in passed if r.agent_type.lower() == best_name), None)
        if best_result and rv.get("verdict") == "APPROVED":
            log.info(f"[{task.id}/{best_result.agent_type}/ревью] ✅ одобрено (финал)")
        else:
            log.error(f"[{task.id}] ✗ не прошёл ревью за {cfg.max_review_rounds}+1 раундов → BLOCKED")
            update_task_status(task.id, "blocked")
            # Сохраняем финальные замечания для истории (TASK-095)
            # Собираем все комментарии из всех ревьюеров, если они есть
            all_comments = "\n\n".join(rework_comments)
            if not all_comments:
                all_comments = rv.get("comments", rv.get("summary", ""))
            if all_comments:
                task.last_attempts.append({
                    "comments": all_comments,
                    "timestamp": int(time.time())
                })
            run_hook(cfg.hook_on_complete, task.id, "blocked", "none")
            _log_total_cost(task.id, all_results)
            cleanup_worktrees(all_results)
            return False

    # Мерж
    log.info(f"[{task.id}/{best_result.agent_type}/мерж] 🏆 победитель")
    m_res = merge_to_develop(best_result.branch, task.id)
    if m_res.success:
        update_task_status(task.id, "done", agent=best_result.agent_type, branch=best_result.branch)
        run_hook(cfg.hook_on_complete, task.id, "done", best_result.agent_type)
        log.info(f"[{task.id}] ✓ done (вмержен в {cfg.dev_branch})")
    else:
        update_task_status(task.id, f"review:{best_result.agent_type}",
                          agent=best_result.agent_type, branch=best_result.branch)
        run_hook(cfg.hook_on_complete, task.id, f"review:{best_result.agent_type}", best_result.agent_type)
        log.warning(f"[{task.id}] ⚠ review (мерж не удался)")
        if m_res.is_test_failure:
             _log_test_results(m_res, task, cfg.check_command)
             rework_msg = f"Тесты провалены при мерже в {cfg.dev_branch}:\n{m_res.test_stderr or m_res.test_stdout}"
             log.info(f"[{task.id}/{best_result.agent_type}/доработка] отправлен на исправление (тесты провалены при мерже)")
             send_to_rework(best_result, task, rework_msg)
        else:
             rework_msg = f"Ошибка мержа в {cfg.dev_branch} (конфликт?):\n{m_res.merge_stderr or m_res.merge_stdout}"
             log.info(f"[{task.id}/{best_result.agent_type}/доработка] отправлен на исправление (ошибка мержа)")
             send_to_rework(best_result, task, rework_msg)

    _log_total_cost(task.id, all_results)
    cleanup_worktrees(all_results)
    return True


# --- Одиночный режим ---

def execute_task_single(task: Task, task_idx: int, agent_type: str) -> bool:
    """Выполнение одним агентом + ревью другим."""
    log.info(f"═══ {task.id}: {task.name} ({agent_type}, ревью другим) ═══")
    start_time = time.perf_counter()
    review_rounds = 0

    # Pre-check: критерий готовности уже выполнен в develop?
    if check_already_done(task):
        log.info(f"[{task.id}] ✅ Критерий готовности уже выполнен в develop — пропускаю")
        update_task_status(task.id, "done", agent="pre-check")
        run_hook(cfg.hook_on_complete, task.id, "done", "pre-check")
        return True

    update_task_status(task.id, f"in_progress:{agent_type}")

    result = run_single_agent(task, task_idx, agent_type)
    if not result.success:
        log.error(f"[{task.id}/{agent_type}] ✗ не написал код → BLOCKED")
        update_task_status(task.id, "blocked")
        run_hook(cfg.hook_on_complete, task.id, "blocked", "none")
        _log_total_cost(task.id, [result])
        cleanup_worktrees([result])
        return False

    log.info(f"[{task.id}/{result.agent_type}] lines={result.code_lines}, bin={result.binary_size}")

    from .agents import is_agent_disabled
    reviewers = [n for n in cfg.agent_names if n != agent_type and not is_agent_disabled(n)]
    if not reviewers:
        if cfg.review_frame and cfg.review_frame in cfg.frames:
            reviewers = [f"{agent_type}+{cfg.review_frame}"]
        else:
            reviewers = [agent_type]

    # Счетчик раундов ревью
    review_rounds = 0
    
    diff = get_diff(result, task)
    if not diff:
        log.error(f"[{task.id}] ✗ пустой diff → BLOCKED")
        update_task_status(task.id, "blocked")
        run_hook(cfg.hook_on_complete, task.id, "blocked", "none")
        _log_total_cost(task.id, [result])
        cleanup_worktrees([result])
        return False

    best_result = result
    prev_summary = None
    repeat_count = 0
    for review_round in range(1, cfg.max_review_rounds + 1):
        review_rounds += 1
        log.info(f"[{task.id}/ревью] раунд {review_round}/{cfg.max_review_rounds}, проверяют {', '.join(reviewers)}")
        
        rework_comments = []
        verdicts = {}
        with ThreadPoolExecutor(max_workers=len(reviewers)) as review_pool:
            review_futures = {}
            for rev in reviewers:
                f = review_pool.submit(single_review, rev, agent_type, get_diff(best_result, task), task,
                                       build_passed=True, changed_files=get_changed_files(best_result, task),
                                       workdir=best_result.workdir)
                review_futures[f] = rev
            
            for f in as_completed(review_futures):
                rev = review_futures[f]
                rv = f.result()
                verdicts[rev] = rv
                verdict = rv.get("verdict", "FAILED").upper()
                log.info(f"[{task.id}/{rev}/ревью] → {agent_type}: {verdict}")
                summary = rv.get('summary', rv.get('comments', '')[:200])
                if summary:
                    log.info(f"[{task.id}/{rev}/ревью] {summary}")
                
                # Собираем замечания для доработки (поддержка NEEDS_WORK, NEEDS_REWORK, REJECTED) (TASK-095)
                if verdict != "APPROVED":
                    comments = rv.get("comments", rv.get("summary", ""))
                    if comments:
                        rework_comments.append(f"### Замечания от {rev} (вердикт: {verdict})\n{comments}")
                    # Также сохраняем full_text для детального анализа если нужно
                    # if rv.get("full_text"):
                    #     rework_comments.append(f"### Полный текст от {rev}\n{rv['full_text']}")

        all_approved = all(v.get("verdict") == "APPROVED" for v in verdicts.values())
        if all_approved:
            log.info(f"[{task.id}/{agent_type}/ревью] ✅ одобрено")
            break

        # Проверка на терминальный отказ (TASK-052)
        if any(v.get("is_terminal") for v in verdicts.values()):
            log.error(f"[{task.id}] ✗ ТЕРМИНАЛЬНЫЙ ОТКАЗ → BLOCKED")
            update_task_status(task.id, "blocked")
            # Сохраняем замечания для истории (TASK-095)
            all_comments = "\n\n".join(rework_comments)
            if not all_comments:
                # Fallback к первому найденному комментарию если список пуст
                for v in verdicts.values():
                    c = v.get("comments", v.get("summary", ""))
                    if c:
                        all_comments = c
                        break
            if all_comments:
                task.last_attempts.append({
                    "comments": all_comments,
                    "timestamp": int(time.time())
                })
            run_hook(cfg.hook_on_complete, task.id, "blocked", "none")
            _log_total_cost(task.id, [result])
            cleanup_worktrees([result])
            return False

        # Проверка лимита переделок (TASK-058/095)
        max_reworks = getattr(cfg, "max_reworks", 3)
        if task.rework_count >= max_reworks:
            log.error(f"[{task.id}] ✗ Превышен лимит правок ({max_reworks}) → STUCK")
            update_task_status(task.id, "stuck")
            # Сохраняем замечания для истории (TASK-095)
            all_comments = "\n\n".join(rework_comments)
            if not all_comments:
                # Fallback к первому найденному комментарию если список пуст
                for v in verdicts.values():
                    c = v.get("comments", v.get("summary", ""))
                    if c:
                        all_comments = c
                        break
            if all_comments:
                task.last_attempts.append({
                    "comments": all_comments,
                    "timestamp": int(time.time())
                })
            run_hook(cfg.hook_on_complete, task.id, "stuck", "none")
            _log_total_cost(task.id, [result])
            cleanup_worktrees([result])
            return False

        # Детекция зацикливания (по первому ревьюеру для простоты)
        first_rv = list(verdicts.values())[0]
        cur_summary = first_rv.get("summary", "").strip()
        if cur_summary and cur_summary == prev_summary:
            repeat_count += 1
            if repeat_count >= 1:
                log.warning(f"[{task.id}/ревью] ⚠ зациклился ({repeat_count + 1} раунда одно замечание)")
                _escalate_review_stall(task, [best_result], first_rv)
                update_task_status(task.id, "blocked")
                run_hook(cfg.hook_on_complete, task.id, "blocked", "none")
                _log_total_cost(task.id, [result])
                cleanup_worktrees([result])
                return False
        else:
            repeat_count = 0
        prev_summary = cur_summary

        all_comments = "\n\n".join(rework_comments)
        if not all_comments.strip():
            log.warning(f"[{task.id}/ревью] ⚠ без замечаний или ошибка — пропускаю")
            continue
        log.info(f"[{task.id}/{agent_type}/доработка] {C['yellow']}отправлен на исправление{R}")
        send_to_rework(best_result, task, all_comments)
    else:
        log.info(f"[{task.id}/ревью] финальный раунд, проверяют {', '.join(reviewers)}")
        verdicts = {}
        with ThreadPoolExecutor(max_workers=len(reviewers)) as review_pool:
            review_futures = {}
            for rev in reviewers:
                f = review_pool.submit(single_review, rev, agent_type, get_diff(best_result, task), task,
                                       build_passed=True, changed_files=get_changed_files(best_result, task),
                                       workdir=best_result.workdir)
                review_futures[f] = rev
            for f in as_completed(review_futures):
                rev = review_futures[f]
                verdicts[rev] = f.result()

        all_approved = all(v.get("verdict") == "APPROVED" for v in verdicts.values())
        if not all_approved:
            log.error(f"[{task.id}/{agent_type}/ревью] ✗ не прошёл → BLOCKED")
            update_task_status(task.id, "blocked")
            # Сохраняем финальные замечания для истории (TASK-095)
            rework_comments = []
            for rev, rv in verdicts.items():
                if rv.get("verdict") != "APPROVED":
                    comments = rv.get("comments", rv.get("summary", ""))
                    if comments:
                        rework_comments.append(f"### Замечания от {rev} (вердикт: {rv.get('verdict')})\n{comments}")
                    else:
                        rework_comments.append(f"### Замечания от {rev} (вердикт: {rv.get('verdict')})\nНет конкретных комментариев.")
            all_comments = "\n\n".join(rework_comments)
            if all_comments:
                task.last_attempts.append({
                    "comments": all_comments,
                    "timestamp": int(time.time())
                })
            run_hook(cfg.hook_on_complete, task.id, "blocked", "none")
            _log_total_cost(task.id, [result])
            cleanup_worktrees([result])
            return False
        log.info(f"[{task.id}/{agent_type}/ревью] ✅ одобрено (финал)")

    # Мерж
    log.info(f"[{task.id}/{agent_type}/мерж] 🏆 победитель")
    m_res = merge_to_develop(best_result.branch, task.id)
    if m_res.success:
        update_task_status(task.id, "done", agent=agent_type, branch=best_result.branch)
        run_hook(cfg.hook_on_complete, task.id, "done", agent_type)
        log.info(f"[{task.id}] ✓ done (вмержен в {cfg.dev_branch})")
    else:
        update_task_status(task.id, f"review:{agent_type}", agent=agent_type, branch=best_result.branch)
        run_hook(cfg.hook_on_complete, task.id, f"review:{agent_type}", agent_type)
        log.warning(f"[{task.id}] ⚠ review (мерж не удался)")
        if m_res.is_test_failure:
             _log_test_results(m_res, task, cfg.check_command)
             rework_msg = f"Тесты провалены при мерже в {cfg.dev_branch}:\n{m_res.test_stderr or m_res.test_stdout}"
             log.info(f"[{task.id}/{agent_type}/доработка] отправлен на исправление (тесты провалены при мерже)")
             send_to_rework(best_result, task, rework_msg)
        else:
             rework_msg = f"Ошибка мержа в {cfg.dev_branch} (конфликт?):\n{m_res.merge_stderr or m_res.merge_stdout}"
             log.info(f"[{task.id}/{agent_type}/доработка] отправлен на исправление (ошибка мержа)")
             send_to_rework(best_result, task, rework_msg)

    # Логируем метрики
    total_time = time.perf_counter() - start_time
    log.info(f"[{task.id}] Время выполнения: {total_time:.2f}s, раундов ревью: {review_rounds}")

    _log_total_cost(task.id, [result])
    cleanup_worktrees([result])
    return True


# --- Планировщик ---

def _cleanup_task_branches(task: Task):
    """Удаляет worktree и ветки предыдущего прогона."""
    slug = translate_slug(task.name)
    for d in cfg.agents_dir.glob("agent-*"):
        if d.is_dir():
            run_cmd(["git", "worktree", "remove", str(d), "--force"], cwd=cfg.root_dir, check=False)
    for agent_type in cfg.all_agent_names:
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
    print("  Варианты: исправить задачу, поменять ревьюера, или approve вручную.")
    print(f"{C['red']}{C['bold']}{'═' * 60}{R}\n")


def _ensure_litellm_proxy():
    """Auto-start LiteLLM proxy if agents need it and it's not running."""
    import urllib.request
    # Check if any enabled agent uses localhost proxy
    proxy_url = ""
    for name, acfg in cfg.agents.items():
        if not acfg.enabled:
            continue
        for arg in acfg.args:
            if "127.0.0.1:4000" in arg or "localhost:4000" in arg:
                proxy_url = "http://127.0.0.1:4000"
                break
        if not proxy_url and acfg.env:
            for v in acfg.env.values():
                if "127.0.0.1:4000" in str(v) or "localhost:4000" in str(v):
                    proxy_url = "http://127.0.0.1:4000"
                    break
        if proxy_url:
            break

    if not proxy_url:
        return  # no agents need proxy

    # Check if proxy is already running (must unset HTTP_PROXY to bypass system proxy)
    _saved = {k: os.environ.pop(k, None) for k in
              ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy")}
    try:
        req = urllib.request.Request(f"{proxy_url}/health")
        req.add_header("Authorization", "Bearer fr-local-dev")
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                return  # proxy is running
    except Exception:
        pass
    finally:
        for k, v in _saved.items():
            if v is not None:
                os.environ[k] = v

    # Try to start proxy
    litellm_bin = Path.home() / ".local/share/pipx/venvs/litellm/bin/litellm"
    config_file = cfg.root_dir / "litellm_config.yaml"
    if not litellm_bin.exists():
        log.warning("LiteLLM не установлен — агенты через proxy не будут работать")
        log.warning("  Установи: pipx install 'litellm[proxy]'")
        return
    if not config_file.exists():
        log.warning(f"litellm_config.yaml не найден в {cfg.root_dir}")
        return

    log.info("Запускаю LiteLLM proxy (localhost:4000)...")
    import subprocess as _sp
    env = {**os.environ, "no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"}
    _sp.Popen(
        [str(litellm_bin), "--config", str(config_file), "--port", "4000", "--host", "127.0.0.1"],
        stdout=open(cfg.log_dir / "litellm.log", "w"),
        stderr=_sp.STDOUT,
        env=env,
    )
    # Wait for startup
    import time as _time
    for _ in range(15):
        _time.sleep(1)
        _sv = {k: os.environ.pop(k, None) for k in
               ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy")}
        try:
            req = urllib.request.Request(f"{proxy_url}/health")
            req.add_header("Authorization", "Bearer fr-local-dev")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    log.info("LiteLLM proxy запущен")
                    return
        except Exception:
            pass
        finally:
            for k, v in _sv.items():
                if v is not None:
                    os.environ[k] = v
    log.warning("LiteLLM proxy не стартовал за 15с — агенты через proxy могут не работать")


def preflight_check() -> bool:
    """Проверяет текущую ветку на проблемы, пробует собрать через cfg.build_commands."""
    # Проверяем merge conflict маркеры через git
    result = run_cmd(["git", "diff", "--check"], cwd=cfg.root_dir, check=False)
    if result.returncode != 0 and result.stdout.strip():
        conflict_files = set()
        for line in result.stdout.strip().split("\n"):
            if ":conflict" in line.lower() or "leftover" in line.lower():
                conflict_files.add(line.split(":")[0])
        if conflict_files:
            # Auto-resolve TASKS.md
            if "TASKS.md" in conflict_files:
                import re as _re
                content = cfg.tasks_file.read_text(encoding="utf-8")
                orig = content
                while "<" + "<<<<<<" + " " in content:
                    content = _re.sub(r"<{7} [^\n]*\n(.*?)={7}\n.*?>{7} [^\n]*\n",
                                      r"\1", content, count=1, flags=_re.DOTALL)
                if content != orig:
                    cfg.tasks_file.write_text(content, encoding="utf-8")
                    log.warning("⚠ Auto-resolved conflict markers in TASKS.md")
                conflict_files.discard("TASKS.md")
            if conflict_files:
                log.error(f"⚠ Merge conflict маркеры в: {list(conflict_files)}")
                log.error("  Разреши конфликты вручную перед запуском")
                return False

    # LiteLLM proxy check: if any agent uses localhost proxy, ensure it's running
    _ensure_litellm_proxy()

    if not cfg.build_commands:
        return True

    # Пробуем первую build-команду для проверки
    for cmd in cfg.build_commands:
        result = run_cmd(cmd, cwd=cfg.root_dir, check=False)
        _log_test_results(result, None, " ".join(cmd))
        if result.returncode != 0:
            stderr = (result.stderr or "") + (result.stdout or "")
            log.error(f"⚠ {cfg.dev_branch} не собирается! {' '.join(cmd)} failed")
            log.error(stderr[-500:])
            # TASK-138: Создаём задачу на починку если сборка сломана
            _create_checkpoint_task_with_log(stderr)
            return False

    # TASK-138: Проверка тестов перед запуском
    if cfg.check_command:
        log.info(f"🔍 Предварительная проверка тестов ({cfg.check_command})...")
        check_result = run_cmd(
            ["bash", "-c", cfg.check_command], cwd=cfg.root_dir,
            timeout=cfg.build_timeout * 2, check=False)
        _log_test_results(check_result, None, cfg.check_command)
        if check_result.returncode != 0:
            stderr = (check_result.stderr or check_result.stdout or "")[-1000:]
            log.error("❌ Предварительная проверка тестов провалена! Создаю задачу на исправление.")
            _create_checkpoint_task_with_log(stderr)
            return False
        log.info("✅ Предварительная проверка тестов пройдена")

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
        _print_flow_guide(tasks)

    print(f"{'═' * 60}\n")


# --- Главный пайплайн ---

def _filter_by_team(tasks: list, team: str) -> list:
    """Filter tasks by discussion name (substring match)."""
    return [t for t in tasks if team in (t.discussion or "")]


def run_pipeline(
    specific_task: Optional[str] = None,
    dry_run: bool = False,
    max_tasks: int | None = None,
    retry: bool = False,
    auto: bool = False,
    team: Optional[str] = None,
):
    """Основной цикл оркестратора."""
    if max_tasks is None:
        max_tasks = cfg.max_parallel_tasks

    if not dry_run and not preflight_check():
        return

    if not dry_run:
        _start_heartbeat()
        atexit.register(_stop_heartbeat)

    def _parse():
        """Parse tasks with optional team filter."""
        t = parse_tasks()
        return _filter_by_team(t, team) if team else t

    tasks = _parse()
    if team:
        log.info(f"Фильтр --team={team}: {len(tasks)} задач")
        # Feature branch: each team works on its own branch, not develop
        feature_branch = f"feature/{team}"
        original_dev_branch = cfg.dev_branch
        # Create feature branch from develop if it doesn't exist
        check = run_cmd(["git", "rev-parse", "--verify", feature_branch],
                        cwd=cfg.root_dir, check=False)
        if check.returncode != 0:
            run_cmd(["git", "branch", feature_branch, cfg.dev_branch], cwd=cfg.root_dir)
            log.info(f"Создана feature branch: {feature_branch} от {cfg.dev_branch}")
        else:
            log.info(f"Feature branch: {feature_branch}")
        # Redirect all worktree/merge operations to feature branch
        cfg.dev_branch = feature_branch

    # Автозакрытие чекпоинт-задач если check_command проходит
    if cfg.check_command:
        for t in tasks:
            if t.status != "done" and "make check" in (t.acceptance or "") and "чекпоинт" in t.name.lower():
                check_result = run_cmd(["bash", "-c", cfg.check_command],
                                       cwd=cfg.root_dir, timeout=cfg.build_timeout, check=False)
                _log_test_results(check_result, t, cfg.check_command)
                if check_result.returncode == 0:
                    log.info(f"[{t.id}] ✅ check_command проходит — чекпоинт автозакрыт")
                    update_task_status(t.id, "done", agent="auto-check")
                    run_hook(cfg.hook_on_complete, t.id, "done", "auto-check")
                break  # проверяем один раз

    tasks = _parse()
    done_count = sum(1 for t in tasks if t.status in ("done", "skip"))
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
        tasks = _parse()
        ready = find_ready_tasks(tasks)
    else:
        done_ids = {t.id for t in tasks if t.status == "done"}
        # Auto-reset stale in_progress tasks (no log activity > 30 min)
        for t in tasks:
            if t.status.startswith("in_progress"):
                task_logs = list(cfg.log_dir.glob(f"{t.id.lower()}-*-attempt*.log"))
                stale = False
                if task_logs:
                    newest = max(f.stat().st_mtime for f in task_logs)
                    stale = (time.time() - newest) > 1800  # 30 min
                else:
                    stale = True  # no logs = no agent ever ran
                if stale:
                    log.warning(f"[{t.id}] ⚠ in_progress > 30мин без активности → open")
                    update_task_status(t.id, "open")
                    t.status = "open"
        tasks = _parse()
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
            tasks = _parse()

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
                tasks = _parse()
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
            print("  Обсуди подход и утверди через /ok")
            print(f"{'═' * 60}\n")
            discuss_chat(topic)

        # После дискуссий — выходим. Пользователь запустит run отдельно.
        log.info("Дискуссии завершены. Запусти ./fr run для выполнения задач.")
        tasks = _parse()
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

    # Декомпозиция — каждый агент оценивает свои задачи (round-robin по агентам)
    # Квота детектится здесь: если агент не ответил — он disabled
    final_ready = []
    decomposed = False
    agent_list = list(cfg.agent_names)
    with ThreadPoolExecutor(max_workers=len(approved)) as pool:
        futures = {}
        for i, t in enumerate(approved):
            agent = agent_list[i % len(agent_list)] if agent_list else ""
            f = pool.submit(assess_and_maybe_decompose, t, agent)
            futures[f] = t
        for f in as_completed(futures):
            t = futures[f]
            if f.result():
                decomposed = True
            else:
                final_ready.append(t)

    if decomposed:
        tasks = _parse()
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
            run_hook(cfg.hook_on_complete, t.id, "done", "pre-check")
        else:
            actually_ready.append(t)
    ready = actually_ready

    if not ready:
        log.info("Все задачи уже выполнены (pre-check)")
        tasks = _parse()
        _print_next_steps(tasks, max_tasks, auto)
        return

    log.info(f"Утверждены и готовы: {[t.id for t in ready]}")

    batch = ready[:max_tasks]
    from .agents import is_agent_disabled
    # CLI agents only for task execution (API agents can't write code in worktree)
    agent_names = [n for n in cfg.cli_agent_names if not is_agent_disabled(n)]
    if not agent_names:
        log.error("Нет доступных CLI-агентов для выполнения задач")
        return

    is_competitive = cfg.mode == "competitive"
    # Для распределения простых задач выбираем надежных агентов
    reliable_agents = [n for n in agent_names if "goose" not in n]
    if not reliable_agents:
        reliable_agents = agent_names  # fallback

    global _rr_agent_index

    # Считаем total_procs для max_workers (пессимистично)
    total_procs = 0
    for task in batch:
        complexity = get_task_complexity(task.id)
        has_assigned = task.agent and task.agent not in ("—", "")
        # Конкурентный если: глобально competitive И (задача сложная ИЛИ агент назначен)
        # НО: если всего одна задача в батче, тоже competitive (согласно CLAUDE.md)
        if (is_competitive or len(batch) == 1) and (complexity > 3 or has_assigned):
            total_procs += len(agent_names)
        else:
            total_procs += 1

    log.info(f"Режим: {'конкурентный' if is_competitive else 'распределённый'} — {len(batch)} задач, {len(agent_names)} агентов")

    with ThreadPoolExecutor(max_workers=max(total_procs, 1)) as pool:
        futures = {}
        for idx, task in enumerate(batch, 1):
            complexity = get_task_complexity(task.id)
            has_assigned = task.agent and task.agent not in ("—", "")

            # Решаем: использовать одного агента или конкурентный режим
            use_single = False
            reason = ""
            
            if not is_competitive:
                use_single = True
                reason = "режим: distributed"
            elif len(batch) > 1 and complexity <= 3 and not has_assigned:
                use_single = True
                reason = "round-robin (простая задача)"
            
            if use_single:
                if has_assigned:
                    # Пытаемся взять первого назначенного CLI-агента
                    raw = [a.strip().lstrip("@").split("+")[0]
                           for a in task.agent.replace(",", " ").split()
                           if a.strip() not in ("—", "")]
                    assigned = [a for a in raw if a in cfg.cli_agent_names and not is_agent_disabled(a)]
                    if assigned:
                        agent = assigned[0]
                        reason = "явное указание"
                    else:
                        agent = reliable_agents[_rr_agent_index % len(reliable_agents)]
                        _rr_agent_index += 1
                        reason = "round-robin (назначенный недоступен)"
                else:
                    agent = reliable_agents[_rr_agent_index % len(reliable_agents)]
                    _rr_agent_index += 1
                    if not reason:
                        reason = "round-robin"
                
                log.info(f"  {task.id} (сложность {complexity}) → {agent} [причина: {reason}]")
                future = pool.submit(execute_task_single, task, idx, agent)
            else:
                display_agents = " vs ".join(agent_names)
                if has_assigned:
                    reason = "явное указание"
                    # Пытаемся распарсить для лога
                    raw = [a.strip().lstrip("@").split("+")[0]
                           for a in task.agent.replace(",", " ").split()
                           if a.strip() not in ("—", "")]
                    assigned = [a for a in raw if a in cfg.cli_agent_names and not is_agent_disabled(a)]
                    if assigned:
                        display_agents = " vs ".join(assigned)
                elif complexity > 3:
                    reason = "порог сложности"
                elif len(batch) == 1:
                    reason = "одна задача в батче"
                else:
                    reason = "режим: competitive"
                
                log.info(f"  {task.id} (сложность {complexity}) → {display_agents} [причина: {reason}]")
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
                run_hook(cfg.hook_on_complete, task.id, "blocked", "none")

    # Проверка тестов после завершения пачки задач (TASK-138)
    if cfg.check_command:
        log.info(f"🔍 Запускаю проверку тестов ({cfg.check_command})...")
        check_result = run_cmd(
            ["bash", "-c", cfg.check_command], cwd=cfg.root_dir,
            timeout=cfg.build_timeout * 2, check=False)
        _log_test_results(check_result, None, cfg.check_command)
        if check_result.returncode != 0:
            stderr = (check_result.stderr or check_result.stdout or "")[-1000:]
            log.error("❌ Проверка тестов провалена! Создаю задачу на исправление.")
            _create_checkpoint_task_with_log(stderr)
        else:
            log.info("✅ Проверка тестов пройдена")

    if cfg.review_run_log:
        review_run_log()

    # Коммитим статусы (только если НЕ на feature branch — иначе засирает develop)
    if not team:
        status_diff = run_cmd(["git", "diff", "--stat", "TASKS.md"], cwd=cfg.root_dir, check=False)
        if status_diff.stdout.strip():
            run_cmd(["git", "add", "TASKS.md"], cwd=cfg.root_dir, check=False)
            run_cmd(["git", "commit", "-m", "update: статусы задач после прогона"], cwd=cfg.root_dir, check=False)

    # Restore original dev_branch if we were on a feature branch
    if team:
        cfg.dev_branch = original_dev_branch
        log.info(f"Feature branch {feature_branch} готова. Мерж в {original_dev_branch}:")
        log.info(f"  git merge {feature_branch}")

    tasks = _parse()
    _print_next_steps(tasks, max_tasks, auto)

    log.info("ForgeRace завершён")
    os.system("stty sane 2>/dev/null")
    os._exit(0)
