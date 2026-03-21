"""Крест-на-крест ревью, одиночное ревью, парсинг вердикта."""

import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from .agents import AgentResult, build_prompt, run_agent_process, run_reviewer
from .config import cfg
from .tasks import Task, task_paths
from .utils import log, run_cmd


def get_diff(result: AgentResult, task: Task | None = None) -> str:
    """Получает diff агента относительно develop. Сначала по файлам задачи, fallback — весь diff."""
    paths = task_paths(task) if task else []
    if paths:
        diff_result = run_cmd(
            ["git", "diff", cfg.dev_branch, "--"] + paths,
            cwd=result.workdir, check=False,
        )
        diff_text = (diff_result.stdout or "").strip()
        if diff_text:
            if len(diff_text) > 6000:
                diff_text = diff_text[:6000] + "\n... (обрезано)"
            return diff_text

    # Fallback: полный diff (агент мог создать файлы вне task_paths)
    diff_result = run_cmd(
        ["git", "diff", cfg.dev_branch],
        cwd=result.workdir, check=False,
    )
    diff_text = (diff_result.stdout or "").strip()
    if len(diff_text) > 6000:
        diff_text = diff_text[:6000] + "\n... (обрезано)"
    return diff_text


def get_changed_files(result: AgentResult, task: Task | None = None) -> list[str]:
    """Список изменённых файлов относительно develop."""
    paths = task_paths(task) if task else []
    if paths:
        diff_result = run_cmd(
            ["git", "diff", "--name-only", cfg.dev_branch, "--"] + paths,
            cwd=result.workdir, check=False,
        )
        files = [f.strip() for f in (diff_result.stdout or "").strip().splitlines() if f.strip()]
        if files:
            return files
    # Fallback: все изменённые файлы
    diff_result = run_cmd(
        ["git", "diff", "--name-only", cfg.dev_branch],
        cwd=result.workdir, check=False,
    )
    return [f.strip() for f in (diff_result.stdout or "").strip().splitlines() if f.strip()]


def pick_reviewer(passed: list[AgentResult]) -> str:
    """Выбирает ревьюера: не автор кода."""
    all_agent_names = cfg.agent_names
    author_names = {r.agent_type for r in passed}

    non_authors = [n for n in all_agent_names if n not in author_names]
    if non_authors:
        return random.choice(non_authors)

    if len(passed) >= 2:
        return passed[1].agent_type

    return passed[0].agent_type


def single_review(reviewer: str, author: str, diff: str, task: Task,
                   build_passed: bool = True, build_log: str = "",
                   changed_files: list[str] | None = None,
                   workdir: "Path | None" = None) -> dict:
    """Один ревьюер проверяет одного автора. Запускается как полноценный агент в worktree автора."""
    from pathlib import Path

    prompt = f"""Ты ревьюер кода. Ты проверяешь реализацию агента {author} для задачи {task.id}.

## Задача
{task.id} — {task.name}
Описание: {task.description}
Критерий готовности: {task.acceptance}

## Что делать
1. Прочитай изменённые файлы (используй Read/Grep/Glob).
2. Проверь что код РЕАЛЬНО написан и соответствует задаче.
3. Если нужно — запусти тесты (Bash).
4. Напиши вердикт.

ВАЖНО:
- НЕ правь файлы. Только анализируй.
- НЕ описывай что ты "будешь делать" или "проверишь" — полные файлы и diff уже приложены ниже. Читай их и сразу пиши вердикт.
- НЕ используй инструменты (Read/Bash/Grep) — всё что нужно уже в промпте.
- Файл существует если он есть в секции "Полные файлы" ниже. Не пиши "файл не найден" если видишь его содержимое.
- ОБЯЗАТЕЛЬНО закончи выводом VERDICT/COMMENTS/SUMMARY.

## Формат ответа — строго:
VERDICT: APPROVED или NEEDS_WORK
COMMENTS: <что проверено и какие проблемы. При APPROVED — докажи что проверил. При NEEDS_WORK — конкретные замечания.>
SUMMARY: <итог в 1-2 строки>

APPROVED = код готов к мержу.
NEEDS_WORK = нужны правки.
- Файлы из `.gitignore` НЕ могут быть изменены — не требуй их правки.
Пиши на русском.
"""

    try:
        # Собираем контекст: полные файлы из worktree + diff
        files_content = ""

        # Способ 1: читаем из worktree
        if workdir and workdir.exists() and changed_files:
            for f in changed_files[:5]:
                fpath = workdir / f
                if fpath.exists() and fpath.stat().st_size < 10000:
                    try:
                        content = fpath.read_text(encoding="utf-8", errors="ignore")
                        files_content += f"\n### {f}\n```\n{content}\n```\n"
                    except Exception:
                        pass

        # Способ 2: если worktree не сработал — извлекаем из git show
        if not files_content and workdir and workdir.exists():
            try:
                name_result = run_cmd(
                    ["git", "diff", "--name-only", cfg.dev_branch],
                    cwd=workdir, check=False)
                git_files = [f.strip() for f in (name_result.stdout or "").splitlines() if f.strip()]
                for f in git_files[:5]:
                    show_result = run_cmd(
                        ["git", "show", f"HEAD:{f}"],
                        cwd=workdir, check=False)
                    if show_result.returncode == 0 and show_result.stdout:
                        content = show_result.stdout
                        if len(content) < 10000:
                            files_content += f"\n### {f}\n```\n{content}\n```\n"
            except Exception:
                pass

        full_prompt = prompt
        if files_content:
            full_prompt += f"\n## Полные файлы (код РЕАЛЬНО существует в репозитории)\n{files_content}"
        full_prompt += f"\n## Diff от {author}\n```diff\n{diff[:4000]}\n```"

        review_text = run_reviewer(reviewer, full_prompt)
        if not review_text:
            return {"verdict": "error", "reviewer": reviewer, "author": author,
                    "full_text": "", "comments": "", "summary": "Пустой ответ"}

        verdict_match = re.search(r"\**VERDICT\**:\s*\**(\w+)\**", review_text, re.IGNORECASE)
        comments_match = re.search(r"\**COMMENTS\**:\s*(.+?)(?=\n\**SUMMARY\**:|\Z)", review_text, re.IGNORECASE | re.DOTALL)
        summary_match = re.search(r"\**SUMMARY\**:\s*(.+)", review_text, re.IGNORECASE)

        verdict = verdict_match.group(1).upper() if verdict_match else "NEEDS_WORK"
        comments = comments_match.group(1).strip() if comments_match else ""

        # APPROVED без обоснования — невалидное ревью
        if verdict == "APPROVED" and len(comments) < 20:
            log.warning(f"[{reviewer}] APPROVED без обоснования — понижаю до NEEDS_WORK")
            verdict = "NEEDS_WORK"
            comments = "Ревьюер не обосновал APPROVED. Требуется повторное ревью с конкретным анализом."

        # NEEDS_WORK с ложным замечанием "не компилируется" — если сборка прошла, отклоняем
        if verdict == "NEEDS_WORK":
            build_fail_phrases = [
                "не компилируется", "не собирается", "ошибка компиляции",
                "compilation error", "does not compile", "build fails",
            ]
            comments_lower = comments.lower()
            has_build_claim = any(p in comments_lower for p in build_fail_phrases)
            if has_build_claim and build_passed:
                log.warning(f"[{reviewer}] NEEDS_WORK утверждает что не компилируется, но сборка прошла — повышаю до APPROVED")
                verdict = "APPROVED"
                comments = f"(автокоррекция: ревьюер ложно заявил о проблемах компиляции, сборка прошла)\n{comments}"

        return {
            "verdict": verdict,
            "reviewer": reviewer,
            "author": author,
            "full_text": review_text,
            "comments": comments,
            "summary": summary_match.group(1).strip() if summary_match else "",
        }
    except Exception as e:
        return {"verdict": "error", "reviewer": reviewer, "author": author,
                "full_text": "", "comments": "", "summary": f"Ошибка: {e}"}


def code_review(passed: list[AgentResult], task: Task) -> dict:
    """Крест-на-крест ревью для N агентов. Каждый ревьюится другим (round-robin)."""
    diffs = {}
    files_map = {}
    workdir_map = {}
    for r in passed:
        diff = get_diff(r, task)
        if diff:
            diffs[r.agent_type] = diff
            files_map[r.agent_type] = get_changed_files(r, task)
            workdir_map[r.agent_type] = r.workdir

    if not diffs:
        return {"verdict": "error", "reason": "Нет diff для ревью"}

    all_agent_names = cfg.agent_names
    author_names = list(diffs.keys())

    # Все против одного: каждый автор ревьюится ВСЕМИ остальными
    review_pairs = []
    for author in author_names:
        others = [n for n in all_agent_names if n != author]
        if not others:
            others = [author]
        for reviewer in others:
            review_pairs.append((reviewer, author))

    pairs_str = ", ".join(f"{rev}→{auth}" for rev, auth in review_pairs)
    log.info(f"    Ревью крест-на-крест: {pairs_str}")

    with ThreadPoolExecutor(max_workers=len(review_pairs)) as pool:
        futures = {}
        for reviewer, author in review_pairs:
            f = pool.submit(single_review, reviewer, author,
                            diffs[author], task, build_passed=True,
                            changed_files=files_map.get(author),
                            workdir=workdir_map.get(author))
            futures[f] = (reviewer, author)
        # reviews_by_author: {author: [{reviewer, verdict, comments, ...}, ...]}
        reviews_by_author: dict[str, list[dict]] = {a: [] for a in author_names}
        reviews = {}  # backward compat: {author: last_review}
        for f in as_completed(futures):
            reviewer, author = futures[f]
            rv = f.result()
            reviews_by_author[author].append(rv)
            reviews[author] = rv  # для send_to_rework

    # Логируем результаты
    full_text_parts = []
    for author in author_names:
        for rv in reviews_by_author[author]:
            header = f"📋 {rv['reviewer']} ревьюит {author}: {rv['verdict']}"
            log.info(f"    {header}")
            log.info(f"    {rv.get('summary', rv.get('comments', '')[:200])}")
            full_text_parts.append(f"=== {rv['reviewer']} ревьюит {author} ===\n{rv['full_text']}")

    full_text = "\n\n".join(full_text_parts)

    # Определяем лучшего: APPROVED = все НЕ-error ревьюеры одобрили
    def _real_reviews(author: str) -> list[dict]:
        """Ревью без ошибок (таймауты и т.п. не считаются)."""
        return [rv for rv in reviews_by_author[author] if rv["verdict"] != "error"]

    def _all_approved(author: str) -> bool:
        real = _real_reviews(author)
        return len(real) > 0 and all(rv["verdict"] == "APPROVED" for rv in real)

    def _approval_count(author: str) -> int:
        return sum(1 for rv in reviews_by_author[author] if rv["verdict"] == "APPROVED")

    fully_approved = [a for a in author_names if _all_approved(a)]

    if fully_approved:
        best = fully_approved[0]
        verdict = "APPROVED"
        reason = f"{best} одобрен всеми ревьюерами"
        comments = ""
    else:
        # Никто не получил полного одобрения — берём с максимумом approve
        best = max(author_names, key=_approval_count)
        verdict = "NEEDS_WORK"
        # Собираем замечания от тех кто не одобрил лучшего
        nw_comments = [rv.get("comments", "") for rv in reviews_by_author[best]
                       if rv["verdict"] != "APPROVED" and rv.get("comments", "").strip()]
        comments = "\n\n".join(nw_comments)
        reason = f"{best} получил {_approval_count(best)}/{len(reviews_by_author[best])} одобрений"

    return {
        "full_text": full_text,
        "reviewer": "cross-review",
        "best": best,
        "verdict": verdict,
        "comments": comments,
        "reason": reason,
        "reviews": reviews,
    }


def send_to_rework(result: AgentResult, task: Task, comments: str) -> bool:
    """Отправляет замечания ревью агенту на доработку. Возвращает True если сборка ок."""
    from .pipeline import verify_build  # lazy import to avoid circular

    tag = f"{task.id}/{result.agent_type}"
    log.info(f"[{tag}] 🔧 Доработка по замечаниям ревью")

    review_file = result.workdir / "REVIEW_COMMENTS.md"
    review_file.write_text(comments, encoding="utf-8")

    if result.agent_type == "claude":
        comments_section = "Прочитай файл REVIEW_COMMENTS.md в корне рабочей директории — там полные замечания."
    else:
        comments_section = comments

    prompt = f"""Ты получил замечания code review для задачи {task.id} — {task.name}.

## Замечания ревьюера
{comments_section}

## Что делать
1. Сначала ОЦЕНИ каждое замечание: согласен ли ты? Есть ли ошибки в рассуждениях ревьюера?
   - Если замечание валидно — исправь.
   - Если замечание ошибочно (ревьюер не понял код, ссылается на несуществующее, противоречит задаче) — НЕ исправляй, оставь комментарий в коде почему.
   - Если замечание косметическое и не влияет на работоспособность — исправь если просто, иначе пропусти.
2. Не трогай файлы, которые не относятся к задаче.

## Правила
{cfg.agent_rules}
"""
    run_result = run_agent_process(result.agent_type, result.workdir, task, prompt)
    review_file.unlink(missing_ok=True)

    # Коммитим ВСЕ правки (агент мог создать файлы вне task_paths)
    run_cmd(["git", "add", "-A"], cwd=result.workdir, check=False)
    diff = run_cmd(["git", "diff", "--cached", "--stat"], cwd=result.workdir, check=False)
    if diff.stdout.strip():
        run_cmd(
            ["git", "commit", "-m", f"{task.id}: правки по ревью [{result.agent_type}]"],
            cwd=result.workdir, check=False,
        )

    ok, error = verify_build(result.workdir, task)
    if not ok:
        log.warning(f"[{tag}] ✗ сборка после доработки: {error[-300:]}")
        return False

    log.info(f"[{tag}] ✓ сборка после доработки пройдена")
    return True
