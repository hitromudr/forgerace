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


def get_changed_files(result: AgentResult, task: Task | None = None) -> list[str]:
    """Список изменённых файлов относительно develop."""
    paths = task_paths(task) if task else ["src/"]
    diff_result = run_cmd(
        ["git", "diff", "--name-only", cfg.dev_branch, "--"] + paths,
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
                   changed_files: list[str] | None = None) -> dict:
    """Один ревьюер проверяет одного автора. Возвращает parsed review."""

    # Контекст сборки — ревьюер ДОЛЖЕН знать результат
    build_section = ""
    if build_passed:
        build_section = "## Результат сборки\n✅ `cargo build` — PASSED\n✅ `cargo test` — PASSED\nКод компилируется и тесты проходят. НЕ пиши замечания о том что код не компилируется.\n"
    elif build_log:
        build_section = f"## Результат сборки\n❌ Сборка провалена:\n```\n{build_log[-1500:]}\n```\n"

    # Список изменённых файлов — ревьюер видит полную картину
    files_section = ""
    if changed_files:
        files_section = "## Изменённые файлы\n" + "\n".join(f"- {f}" for f in changed_files) + "\n"

    prompt = f"""Ты ревьюер кода {cfg.project_context}. Ты проверяешь реализацию агента {author}.

Задача: {task.id} — {task.name}
Описание: {task.description}
Критерий готовности: {task.acceptance}

{build_section}
{files_section}
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
- Сборка и тесты уже проверены автоматически (см. "Результат сборки" выше). Не дублируй эту проверку — фокусируйся на логике, корректности и соответствии задаче.
- Оценивай ТОЛЬКО то, что видишь в diff и контексте выше. Не додумывай.
- Файлы из `.gitignore` (`.env`, `venv/`, `__pycache__/`) НЕ могут быть изменены агентом — не требуй их правки. Агент может править только tracked файлы (`.env.example`, `env_configs/`, и т.д.).
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
    for r in passed:
        diff = get_diff(r, task)
        if diff:
            diffs[r.agent_type] = diff
            files_map[r.agent_type] = get_changed_files(r, task)

    if not diffs:
        return {"verdict": "error", "reason": "Нет diff для ревью"}

    all_agent_names = cfg.agent_names
    author_names = list(diffs.keys())

    # Назначаем ревьюеров: каждый автор ревьюится следующим в списке (round-robin)
    review_pairs = []
    for i, author in enumerate(author_names):
        # Ревьюер — следующий агент (циклически), пропуская себя
        others = [n for n in author_names if n != author]
        if not others:
            others = [next((n for n in all_agent_names if n != author), author)]
        reviewer = others[i % len(others)]
        review_pairs.append((reviewer, author))

    pairs_str = ", ".join(f"{rev}→{auth}" for rev, auth in review_pairs)
    log.info(f"    Ревью крест-на-крест: {pairs_str}")

    with ThreadPoolExecutor(max_workers=len(review_pairs)) as pool:
        futures = {}
        for reviewer, author in review_pairs:
            f = pool.submit(single_review, reviewer, author,
                            diffs[author], task, build_passed=True,
                            changed_files=files_map.get(author))
            futures[f] = author
        reviews = {}
        for f in as_completed(futures):
            author = futures[f]
            reviews[author] = f.result()

    # Логируем результаты
    full_text_parts = []
    for author, rv in reviews.items():
        header = f"📋 {rv['reviewer']} ревьюит {author}: {rv['verdict']}"
        log.info(f"    {header}")
        log.info(f"    {rv.get('summary', rv.get('comments', '')[:200])}")
        full_text_parts.append(f"=== {rv['reviewer']} ревьюит {author} ===\n{rv['full_text']}")

    full_text = "\n\n".join(full_text_parts)

    # Определяем лучшего: кто получил APPROVED
    approved = [a for a, rv in reviews.items() if rv["verdict"] == "APPROVED"]
    needs_work = [a for a, rv in reviews.items() if rv["verdict"] == "NEEDS_WORK"]

    if len(approved) == 1:
        best = approved[0]
        verdict = "APPROVED"
        loser_comments = [reviews[a].get("comments", "") for a in needs_work]
        reason = f"{best} прошёл ревью, остальные требуют доработки"
        comments = "\n\n".join(loser_comments)
    elif len(approved) > 1:
        best = approved[0]  # первый из approved
        verdict = "APPROVED"
        reason = f"{len(approved)} прошли ревью, выбран {best}"
        comments = ""
    elif needs_work:
        best = needs_work[0]
        verdict = "NEEDS_WORK"
        reason = "Все требуют доработки"
        comments = reviews[best].get("comments", "")
    else:
        best = author_names[0]
        verdict = reviews[best].get("verdict", "error")
        reason = reviews[best].get("summary", "")
        comments = reviews[best].get("comments", "")

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
