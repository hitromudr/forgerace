"""Merger agent — LLM-based decision over competitive coder variants.

Replaces the previous Python `calculate_consensus` (majority-vote of
reviewers) with an LLM call that sees diffs, reviews and the task spec,
and returns a structured decision: merge / rework / fail.

Inputs that reach merger have already passed `verify_build` (pytest +
import) in their worktree, so the merger is choosing between *correct*
implementations on quality / spec-fit, not on whether code runs.
"""

import re
from dataclasses import dataclass

from .utils import log


_MERGER_AGENT_DEFAULT = "gpt-oss"
_DIFF_LIMIT_PER_AGENT = 6000  # chars per candidate, keeps prompt bounded


@dataclass
class MergeDecision:
    decision: str          # "merge" | "rework" | "fail"
    winner: str | None     # agent name to merge, or None
    reason: str            # short justification, surfaced in logs/status
    raw_response: str = "" # full LLM text, for debugging


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, +{len(text) - limit} chars]"


def build_merger_prompt(task, candidates: list, reviews_by_author: dict,
                         diffs_by_author: dict) -> str:
    """Compose the merger's prompt.

    candidates: list of AgentResult (or any obj with .agent_type, .branch,
                .code_lines).
    reviews_by_author: dict[agent_type, list[review_dict]] from single_review.
    diffs_by_author: dict[agent_type, str].
    """
    task_block = (
        f"ЗАДАЧА: {task.id} — {task.name}\n\n"
        f"ОПИСАНИЕ:\n{task.description}\n"
    )

    candidates_block = []
    for c in candidates:
        author = c.agent_type
        diff = diffs_by_author.get(author, "")
        reviews = reviews_by_author.get(author, [])

        review_lines = []
        for rv in reviews:
            verdict = rv.get("verdict", "FAILED")
            reviewer = rv.get("reviewer", "?")
            comments = (rv.get("comments") or "").strip()
            comments_short = _truncate(comments, 300)
            review_lines.append(f"- {reviewer}: {verdict} — {comments_short}")
        reviews_text = "\n".join(review_lines) if review_lines else "(нет ревью)"

        candidates_block.append(
            f"=== КАНДИДАТ: {author} ===\n"
            f"ВЕТКА: {c.branch}\n"
            f"СТРОК ИЗМЕНЕНО: {getattr(c, 'code_lines', 0)}\n"
            f"DIFF:\n```\n{_truncate(diff, _DIFF_LIMIT_PER_AGENT)}\n```\n"
            f"РЕВЬЮ:\n{reviews_text}\n"
        )

    rules = (
        "ПРАВИЛА:\n"
        "1. Все кандидаты УЖЕ прошли pytest в своём worktree — выбирай по "
        "соответствию задаче, минимализму и корректности, а не по факту запуска.\n"
        "2. Учитывай конкретные замечания ревьюеров. Игнорируй общие фразы "
        "без указания на код.\n"
        "3. Если хотя бы один кандидат явно лучше — DECISION=merge, WINNER=<его имя>.\n"
        "4. Если все кандидаты содержат серьёзные дефекты — DECISION=rework, WINNER=none.\n"
        "5. Если задача в принципе невыполнима / противоречит коду — DECISION=fail.\n"
    )

    output_format = (
        "ОТВЕЧАЙ СТРОГО В ФОРМАТЕ (никакого markdown, без тройных кавычек):\n"
        "DECISION: merge | rework | fail\n"
        "WINNER: <agent_name> | none\n"
        "REASON: <2-3 короткие фразы почему>\n"
    )

    return (
        "Ты — merger-агент в системе ForgeRace. Твоя задача — выбрать "
        "одну из N реализаций задачи для слияния в develop, "
        "либо отправить все на доработку, либо отклонить задачу.\n\n"
        + task_block + "\n"
        + "\n".join(candidates_block) + "\n"
        + rules + "\n"
        + output_format
    )


def parse_merger_response(text: str) -> MergeDecision | None:
    """Extract DECISION/WINNER/REASON from merger LLM output.

    Tolerates the same bold-markdown patterns as parse_review_text,
    since we use the same family of free-tier models that wrap labels
    in **...**. Returns None when DECISION can't be located.
    """
    decision_match = re.search(
        r"\*{0,2}DECISION\*{0,2}:\*{0,2}\s*\*{0,2}(\w+)",
        text, re.IGNORECASE,
    )
    winner_match = re.search(
        r"\*{0,2}WINNER\*{0,2}:\*{0,2}\s*\*{0,2}([\w@+\-./]+)",
        text, re.IGNORECASE,
    )
    reason_match = re.search(
        r"\*{0,2}REASON\*{0,2}:\*{0,2}\s*(.+)",
        text, re.IGNORECASE | re.DOTALL,
    )

    if not decision_match:
        return None

    decision = decision_match.group(1).lower()
    if decision not in ("merge", "rework", "fail"):
        return None

    winner_raw = winner_match.group(1).strip() if winner_match else "none"
    winner_clean = winner_raw.lstrip("@").strip("*").strip(".")
    winner: str | None = None if winner_clean.lower() in ("none", "null", "") else winner_clean

    reason = reason_match.group(1).strip() if reason_match else ""
    # Reason often has a trailing label from a different field — clip it
    reason = re.split(r"\n\s*\*{0,2}(?:DECISION|WINNER)\*{0,2}:", reason)[0].strip()

    if decision == "merge" and winner is None:
        # Inconsistent: merge but no winner → treat as rework
        return MergeDecision(
            decision="rework",
            winner=None,
            reason=f"merge без winner — деградация до rework. Исходный reason: {reason}",
            raw_response=text,
        )

    return MergeDecision(
        decision=decision,
        winner=winner,
        reason=reason,
        raw_response=text,
    )


def merger_decide(task, candidates: list, reviews_by_author: dict,
                   diffs_by_author: dict, agent_name: str | None = None) -> MergeDecision:
    """Run the merger LLM and return its decision.

    On any failure (no agent, parse fail, exception) falls back to a
    conservative `rework` decision so the pipeline doesn't merge blindly.
    """
    from .agents import run_text_agent  # late import to avoid cycles

    if not candidates:
        return MergeDecision(
            decision="fail",
            winner=None,
            reason="merger: нет кандидатов для решения",
        )

    # Even on a single candidate (race-to-merge winner) the merger runs:
    # it's the gate-keeper that checks spec-fit, not just a tie-breaker.

    name = agent_name or _MERGER_AGENT_DEFAULT
    prompt = build_merger_prompt(task, candidates, reviews_by_author, diffs_by_author)

    try:
        response = run_text_agent(prompt, agent_name=name, tag=f"{task.id}/merger")
    except Exception as e:
        log.error(f"[{task.id}/merger] вызов LLM упал: {e}")
        return MergeDecision(
            decision="rework",
            winner=None,
            reason=f"merger LLM error: {e}",
        )

    if not response:
        log.warning(f"[{task.id}/merger] пустой ответ от {name} → rework")
        return MergeDecision(
            decision="rework",
            winner=None,
            reason=f"merger ({name}) вернул пустой ответ",
        )

    parsed = parse_merger_response(response)
    if parsed is None:
        preview = response.replace("\n", " ⏎ ")[:500]
        log.warning(f"[{task.id}/merger] не удалось распарсить DECISION. raw[:500]: {preview}")
        return MergeDecision(
            decision="rework",
            winner=None,
            reason="merger ответ без DECISION",
            raw_response=response,
        )

    # Validate winner is one of the candidates
    if parsed.decision == "merge":
        candidate_names = {c.agent_type for c in candidates}
        if parsed.winner not in candidate_names:
            log.warning(
                f"[{task.id}/merger] winner '{parsed.winner}' "
                f"не из числа кандидатов {candidate_names} → rework"
            )
            return MergeDecision(
                decision="rework",
                winner=None,
                reason=f"merger назвал неизвестного winner '{parsed.winner}'. "
                       f"Исходный reason: {parsed.reason}",
                raw_response=response,
            )

    return parsed
