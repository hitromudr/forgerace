import re
import time
import random
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from .utils import log, run_cmd, C, R
from .config import cfg
from .agents import AgentResult, run_agent_process, is_agent_disabled
from .tasks import Task, task_paths, update_task_status

_DIFF_MAX = 20000
_DIFF_EXCLUDE = ["*.yaml", "*.yml", "*.json", "*.lock", "*.log", "*.csv", "*.bin"]


def get_diff(result: AgentResult, task: Task | None = None) -> str:
    """Получает diff агента относительно develop. Исключает бинарники и дампы."""
    exclude_args = []
    for pattern in _DIFF_EXCLUDE:
        exclude_args.extend([":(exclude)" + pattern])

    diff_result = run_cmd(
        ["git", "diff", cfg.dev_branch, "--"] + (task_paths(task) if task else []) + exclude_args,
        cwd=result.workdir, check=False,
    )
    diff_text = diff_result.stdout or ""
    if len(diff_text) > _DIFF_MAX:
        diff_text = diff_text[:_DIFF_MAX] + "\n... (обрезано)"
    return diff_text


def get_changed_files(result: AgentResult, task: Task | None = None) -> list[str]:
    """Список изменённых файлов относительно develop."""
    paths = task_paths(task) if task else []
    if paths:
        diff_result = run_cmd(
            ["git", "diff", "--name-only", cfg.dev_branch, "--"] + paths,
            cwd=result.workdir, check=False,
        )
    else:
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

    # Если все доступные агенты участвовали, выбираем кого-то другого
    if len(passed) > 1:
        return passed[1].agent_type

    return passed[0].agent_type


def _build_review_prompt(reviewer: str, author: str, diff: str, task: Task,
                         files_context: str, tier: str) -> str:
    """Build review prompt adapted to model tier."""
    diff_block = f"```diff\n{diff}\n```"

    if tier == "weak":
        return f"""Code review задачи {task.id}: {task.name}.
Автор: {author}
{files_context}

## Diff
{diff_block}

## Правила (СТРОГО)
1. APPROVED — если код решает задачу и сборка проходит. Стиль и "улучшения" НЕ являются причиной для NEEDS_WORK.
2. NEEDS_WORK — ТОЛЬКО если есть конкретный баг, код не соответствует задаче, или отсутствует ключевой функционал.
3. НЕ придирайся к стилю, именованию, комментариям, структуре файлов.
4. НЕ предлагай "улучшения", "рефакторинг", "расширение".

## Формат ответа (строго)
VERDICT: APPROVED | NEEDS_WORK | REJECTED
IS_TERMINAL: TRUE | FALSE
COMMENTS: конкретные баги или "код соответствует задаче"
SUMMARY: одна строка
"""
    elif tier == "medium":
        return f"""Code review задачи {task.id}: {task.name}.
Автор: {author}
{files_context}

## Diff
{diff_block}

## Правила ревью
1. Проверь соответствие описанию задачи.
2. Проверь на баги и ошибки логики.
3. Стиль и "улучшения" — в COMMENTS, но НЕ причина для NEEDS_WORK если код работает.
4. NEEDS_WORK — только при конкретных багах или неполной реализации.

## Формат ответа
VERDICT: APPROVED | NEEDS_WORK | REJECTED
IS_TERMINAL: TRUE | FALSE (TRUE если фундаментально неверно)
COMMENTS: твои замечания
SUMMARY: краткое резюме одной строкой

Важно: в поле VERDICT пиши ТОЛЬКО одно слово.
"""
    else:
        return f"""Сделай code review для задачи {task.id}: {task.name}.
Автор: {author}
{files_context}

## Diff
{diff_block}

## Правила ревью
1. Проверь соответствие описанию задачи.
2. Проверь на баги, утечки, плохой стиль.
3. Оцени, насколько решение полное.

## Формат ответа
VERDICT: APPROVED | NEEDS_WORK | REJECTED
IS_TERMINAL: TRUE | FALSE (TRUE если решение фундаментально неверно и доработка не поможет)
COMMENTS: твои замечания
SUMMARY: краткое резюме одной строкой

Важно: в поле VERDICT пиши ТОЛЬКО одно слово.
"""


def single_review(reviewer: str, author: str, diff: str, task: Task,
                   build_passed: bool = True, build_log: str = "",
                   changed_files: list[str] | None = None,
                   workdir: Path | None = None) -> dict:
    """Один ревьюер проверяет одного автора. Запускается как полноценный агент в worktree автора."""
    from .pipeline import run_text_agent

    files_context = f"Файлы: {', '.join(changed_files)}" if changed_files else ""

    # Determine reviewer tier
    actual_reviewer = reviewer.split("+")[0]
    acfg = cfg.agents.get(actual_reviewer)
    tier = acfg.tier if acfg else "strong"
    prompt = _build_review_prompt(reviewer, author, diff, task, files_context, tier)
    try:
        # Пытаемся запустить ревьюера (до 3 раз при пустых ответах)
        review_text = ""
        actual_reviewer = reviewer.split("+")[0]
        for _retry in range(3):
            review_text = run_text_agent(prompt, agent_name=reviewer)
            if review_text:
                break
            if is_agent_disabled(actual_reviewer):
                break  # quota, no point retrying
            log.warning(f"[{reviewer}] пустой ответ, retry {_retry + 1}/3...")
        if not review_text:
            return {"verdict": "FAILED", "reviewer": reviewer, "author": author,
                    "full_text": "", "comments": "", "summary": "Пустой ответ от ревьюера"}

        verdict_match = re.search(r"\**VERDICT\**:\s*\**(\w+)\**", review_text, re.IGNORECASE)
        terminal_match = re.search(r"\**IS_TERMINAL\**:\s*\**(\w+)\**", review_text, re.IGNORECASE)
        comments_match = re.search(r"\**COMMENTS\**:\s*(.+?)(?=\n\**SUMMARY\**:|\Z)", review_text, re.IGNORECASE | re.DOTALL)
        summary_match = re.search(r"\**SUMMARY\**:\s*(.+)", review_text, re.IGNORECASE)

        # Если ответ не содержит VERDICT или содержит битый JSON — технический сбой
        if not verdict_match:
            return {"verdict": "FAILED", "reviewer": reviewer, "author": author,
                    "full_text": review_text, "comments": "", "summary": "Ответ не содержит VERDICT"}

        verdict = verdict_match.group(1).upper() if verdict_match else "FAILED"
        is_terminal = terminal_match.group(1).upper() == "TRUE" if terminal_match else False
        comments = comments_match.group(1).strip() if comments_match else ""

        # REJECTED может быть терминальным
        if verdict == "REJECTED":
            # Если ревьюер явно сказал IS_TERMINAL: TRUE или в комментариях есть ключевые слова
            comments_lower = comments.lower()
            terminal_keywords = ["невозможно", "бессмыслен", "невыполним", "противоречит"]
            if any(kw in comments_lower for kw in terminal_keywords):
                is_terminal = True

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
            "is_terminal": is_terminal,
            "reviewer": reviewer,
            "author": author,
            "full_text": review_text,
            "comments": comments,
            "summary": summary_match.group(1).strip() if summary_match else "",
        }
    except Exception as e:
        return {"verdict": "FAILED", "reviewer": reviewer, "author": author,
                "full_text": "", "comments": "", "summary": f"Техническая ошибка: {e}"}


def is_review_successful(rv: dict) -> bool:
    """
    Проверяет, был ли ответ ревьюера успешным (технически).
    Успешным считается любой вердикт, кроме FAILED и error.
    """
    v = rv.get("verdict")
    return v is not None and v not in ("FAILED", "error")


def calculate_consensus(reviews_by_author: dict[str, list[dict]], min_reviewers: int = 1) -> tuple[str, str, str]:
    """
    Определяет лучшего автора и общий вердикт на основе набора ревью.
    Использует веса моделей: gemini=1.5, остальное=1.0.
    
    Returns:
        (best, verdict, reason)
    """
    weights = {"gemini": 1.5}
    best = None
    best_score = -999.0
    
    author_stats = {}
    
    for author, reviews in reviews_by_author.items():
        total_reviews = len(reviews)
        
        if total_reviews < min_reviewers:
            author_stats[author] = {
                "score": -1.0, 
                "verdict": "NEEDS_WORK", 
                "reason": f"Недостаточно ревьюеров ({total_reviews} < {min_reviewers})"
            }
            continue
            
        total_weight_score = 0.0
        has_rejected = False
        for rv in reviews:
            v = rv.get("verdict")
            if v == "FAILED":
                continue
            
            reviewer_name = rv.get("reviewer", "").split("+")[0]
            weight = weights.get(reviewer_name, 1.0)
            
            if v == "APPROVED":
                total_weight_score += 1.0 * weight
            elif v == "NEEDS_WORK":
                total_weight_score += 0.0 * weight
            elif v == "REJECTED":
                total_weight_score -= 1.0 * weight
                has_rejected = True
            elif v == "error":
                total_weight_score -= 0.5 * weight
        
        # Средневзвешенный балл
        avg_score = total_weight_score / total_reviews if total_reviews > 0 else -1.0
        
        verdict = "APPROVED" if avg_score > 0 and not has_rejected else "NEEDS_WORK"
        author_stats[author] = {"score": avg_score, "verdict": verdict, "reviews_count": total_reviews}
        
        if avg_score > best_score:
            best_score = avg_score
            best = author
            
    if not best:
        # Fallback if no one has enough reviews
        best = list(reviews_by_author.keys())[0] if reviews_by_author else "none"
        return best, "NEEDS_WORK", "Недостаточно ревьюеров для принятия решения"
        
    res = author_stats[best]
    if res["score"] <= 0:
        return best, "NEEDS_WORK", f"Лучший автор ({best}) требует доработки (рейтинг {res['score']})"
        
    return best, res["verdict"], f"Автор {best} выбран как лучший (наивысший взвешенный рейтинг {res['score']})"


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
        return {
            "verdict": "error",
            "reason": "Нет diff для ревью",
            "best": "none",
            "author_success_counts": {},
            "reviews": {},
            "comments": [],
            "reviewer": "cross-review",
            "full_text": "Нет diff для ревью"
        }

    all_agent_names = cfg.agent_names
    author_names = list(diffs.keys())

    # Round-robin: каждый автор ревьюится ОДНИМ другим агентом (не N²)
    available = [n for n in all_agent_names if not is_agent_disabled(n)]
    if not available:
        log.error("    ✗ Все агенты отключены (квота) — ревью невозможно, задача → blocked")
        best = author_names[0]
        return {
            "full_text": "(все ревьюеры отключены по квоте)",
            "reviewer": "none",
            "best": best,
            "verdict": "error",
            "comments": [],
            "reason": "нет ревьюеров",
            "reviews": {},
            "author_success_counts": {a: 0 for a in author_names},
        }

    review_pairs = []
    for i, author in enumerate(author_names):
        others = [n for n in available if n != author]
        if others:
            reviewer = others[i % len(others)]
        elif cfg.review_frame and cfg.review_frame in cfg.frames:
            # Fallback: self-review
            reviewer = f"{author}+{cfg.review_frame}"
        else:
            reviewer = author
        review_pairs.append((reviewer, author))

    # reviews_by_author: {author: [{reviewer, verdict, comments, ...}, ...]}
    reviews_by_author: dict[str, list[dict]] = {a: [] for a in author_names}
    reviews = {}  # backward compat: {author: last_review}
    author_success_counts: dict[str, int] = {a: 0 for a in author_names}

    with ThreadPoolExecutor(max_workers=len(review_pairs)) as pool:
        futures = {}
        for reviewer, author in review_pairs:
            f = pool.submit(single_review, reviewer, author,
                            diffs[author], task, build_passed=True,
                            changed_files=files_map.get(author),
                            workdir=workdir_map.get(author))
            futures[f] = (reviewer, author)

        for f in as_completed(futures):
            reviewer, author = futures[f]
            rv = f.result()
            reviews_by_author[author].append(rv)
            reviews[author] = rv

            # Подсчет успешных ответов внутри цикла as_completed гарантирует точность
            # при параллельной обработке (инкремент выполняется в основном потоке)
            if is_review_successful(rv):
                author_success_counts[author] += 1

    # Логируем результаты
    full_text_parts = []
    for author in author_names:
        for rv in reviews_by_author[author]:
            header = f"📋 {rv['reviewer']} ревьюит {author}: {rv['verdict']}"
            log.info(f"    {header}")
            full_text_parts.append(f"=== {rv['reviewer']} ревьюит {author} ===\n{rv['full_text']}")

    full_text = "\n\n".join(full_text_parts)

    min_reviewers = getattr(cfg, 'min_reviewers', 1)
    best, verdict, reason = calculate_consensus(reviews_by_author, min_reviewers)

    comments = []
    for rv in reviews_by_author[best]:
        if rv["verdict"] != "APPROVED" and rv.get("comments", "").strip():
            comments.append(rv["comments"])

    return {
        "full_text": full_text,
        "reviewer": "cross-review",
        "best": best,
        "verdict": verdict,
        "comments": comments,
        "reason": reason,
        "reviews": reviews,
        "author_success_counts": author_success_counts,
    }


def send_to_rework(result: AgentResult, task: Task, comments: str) -> bool:
    """Отправляет замечания ревью агенту на доработку. Возвращает True если сборка ок."""
    from .pipeline import verify_build  # lazy import to avoid circular

    tag_rework = f"{task.id}/{result.agent_type}/доработка"
    from .utils import C, R
    log.info(f"[{tag_rework}] {C['yellow']}правки по замечаниям ревью{R}")

    if not result.workdir or not result.workdir.exists():
        log.error(f"[{tag_rework}] workdir не существует: {result.workdir}")
        return False

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
    run_agent_process(result.agent_type, result.workdir, task, prompt)
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
    tag_rework = f"{task.id}/{result.agent_type}/доработка"
    if not ok:
        log.warning(f"[{tag_rework}] ✗ сборка провалена: {error[-300:]}")
        return False

    log.info(f"[{tag_rework}] ✓ сборка ок")

    # Обновляем статус задачи и сохраняем текст замечаний
    task.rework_count += 1
    task.last_attempts.append({
        "comments": comments,
        "diff": diff.stdout.strip(),
        "timestamp": int(time.time())
    })
    from .tasks import update_task_status
    update_task_status(task.id, f"in_progress:{result.agent_type}", agent=result.agent_type, branch=result.branch)

    return True
