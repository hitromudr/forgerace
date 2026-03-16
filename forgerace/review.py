"""Крест-на-крест ревью, одиночное ревью, парсинг вердикта."""

import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from .agents import AgentResult, build_prompt, run_agent_process, run_reviewer
from .config import cfg
from .tasks import Task, task_paths
from .utils import log, run_cmd


def get_diff(result: AgentResult, task: Task | None = None) -> str:
    """Получает diff агента относительно develop (по файлам задачи)."""
    paths = task_paths(task) if task else ["src/"]
    diff_result = run_cmd(
        ["git", "diff", cfg.dev_branch, "--"] + paths,
        cwd=result.workdir, check=False,
    )
    diff_text = (diff_result.stdout or "").strip()
    if len(diff_text) > 6000:
        diff_text = diff_text[:6000] + "\n... (обрезано)"
    return diff_text


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


def single_review(reviewer: str, author: str, diff: str, task: Task) -> dict:
    """Один ревьюер проверяет одного автора. Возвращает parsed review."""
    prompt = f"""Ты ревьюер кода {cfg.project_context}. Ты проверяешь реализацию агента {author}.

Задача: {task.id} — {task.name}
Описание: {task.description}
Критерий готовности: {task.acceptance}

Проверь:
1. **Корректность**: баги, UB, гонки данных, use-after-free?
2. **Соответствие задаче**: реализовано ли то, что нужно по описанию и критерию готовности?
3. **Качество**: unsafe обоснован? Мёртвый код? Архитектура?

Формат ответа — строго:
VERDICT: APPROVED или NEEDS_WORK
COMMENTS: <что конкретно проверено и какие проблемы найдены. При APPROVED — перечисли что проверил и почему ок. При NEEDS_WORK — конкретные замечания. ПУСТЫЕ КОММЕНТАРИИ ЗАПРЕЩЕНЫ.>
SUMMARY: <итог в 1-2 строки>

APPROVED = код готов к мержу, ты ЛИЧНО проверил каждый пункт выше.
NEEDS_WORK = нужны правки.
ВАЖНО:
- APPROVED без обоснования в COMMENTS будет отклонён. Ты должен доказать что проверил код.
- НЕЛЬЗЯ допускать "считаем что X реализовано в коде, не попавшем в diff". Если чего-то нет в diff — это NEEDS_WORK.
- Оценивай ТОЛЬКО то, что видишь в diff. Не додумывай.
Пиши на русском.

### Diff от {author}
```diff
{diff}
```"""

    try:
        review_text = run_reviewer(reviewer, prompt)
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
    """Крест-на-крест ревью. Возвращает {verdict, best, reason, comments, full_text}."""
    diffs = {}
    for r in passed:
        diff = get_diff(r, task)
        if diff:
            diffs[r.agent_type] = diff

    if not diffs:
        return {"verdict": "error", "reason": "Нет diff для ревью"}

    all_agent_names = cfg.agent_names
    author_names = list(diffs.keys())

    if len(author_names) >= 2:
        log.info(f"    Ревью крест-на-крест: {author_names[0]}→{author_names[1]}, {author_names[1]}→{author_names[0]}")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(single_review, author_names[1], author_names[0], diffs[author_names[0]], task): author_names[0],
                pool.submit(single_review, author_names[0], author_names[1], diffs[author_names[1]], task): author_names[1],
            }
            reviews = {}
            for f in as_completed(futures):
                author = futures[f]
                reviews[author] = f.result()
    else:
        author = author_names[0]
        reviewer = next((n for n in all_agent_names if n != author), author)
        log.info(f"    Ревьюер: {reviewer} → {author}")
        reviews = {author: single_review(reviewer, author, diffs[author], task)}

    # Логируем результаты
    full_text_parts = []
    for author, rv in reviews.items():
        header = f"📋 {rv['reviewer']} ревьюит {author}: {rv['verdict']}"
        log.info(f"    {header}")
        log.info(f"    {rv.get('summary', rv.get('comments', '')[:200])}")
        full_text_parts.append(f"=== {rv['reviewer']} ревьюит {author} ===\n{rv['full_text']}")

    full_text = "\n\n".join(full_text_parts)

    # Определяем лучшего
    if len(reviews) >= 2:
        a, b = list(reviews.keys())
        rv_a, rv_b = reviews[a], reviews[b]
        a_approved = rv_a["verdict"] == "APPROVED"
        b_approved = rv_b["verdict"] == "APPROVED"

        if a_approved and not b_approved:
            best, verdict, reason = a, "APPROVED", f"{rv_b['reviewer']} нашёл проблемы в {b}, а {a} прошёл ревью"
            comments = rv_b.get("comments", "")
        elif b_approved and not a_approved:
            best, verdict, reason = b, "APPROVED", f"{rv_a['reviewer']} нашёл проблемы в {a}, а {b} прошёл ревью"
            comments = rv_a.get("comments", "")
        elif a_approved and b_approved:
            best, verdict = a, "APPROVED"
            reason = f"Оба прошли ревью, выбран {a}"
            comments = ""
        else:
            best = a
            verdict, reason = "NEEDS_WORK", "Оба требуют доработки"
            comments = reviews[best].get("comments", "")
    else:
        author = list(reviews.keys())[0]
        rv = reviews[author]
        best = author if rv["verdict"] != "error" else "none"
        verdict = rv["verdict"]
        comments = rv.get("comments", "")
        reason = rv.get("summary", "")

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
Исправь ВСЕ указанные проблемы. Не трогай файлы, которые не относятся к задаче.

## Правила
{cfg.agent_rules}
"""
    run_result = run_agent_process(result.agent_type, result.workdir, task, prompt)
    review_file.unlink(missing_ok=True)

    # Коммитим правки
    for p in task_paths(task):
        run_cmd(["git", "add", p], cwd=result.workdir, check=False)
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
