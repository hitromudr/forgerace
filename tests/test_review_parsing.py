"""Regression tests for review parsing & consensus.

Locks in the bug-fix series of commits 5a06409 (9 пилотных багов:
majority vote, FAILED filter) and 70c82c8 (gpt-oss bold markdown regex)
so future prompt or weight changes don't silently regress.
"""

from forgerace.review import (
    parse_review_text,
    calculate_consensus,
    is_review_successful,
)


# ---------------------------------------------------------------------------
# parse_review_text — plain format
# ---------------------------------------------------------------------------

def test_parse_plain_approved():
    text = (
        "VERDICT: APPROVED\n"
        "COMMENTS: код реализует требования, тесты проходят, замечаний нет\n"
        "SUMMARY: ok\n"
    )
    parsed = parse_review_text(text)
    assert parsed is not None
    assert parsed["verdict"] == "APPROVED"
    assert "тесты" in parsed["comments"]
    assert parsed["summary"] == "ok"


def test_parse_plain_needs_work():
    text = (
        "VERDICT: NEEDS_WORK\n"
        "COMMENTS: missing input validation in handle_request\n"
    )
    parsed = parse_review_text(text)
    assert parsed["verdict"] == "NEEDS_WORK"
    assert "validation" in parsed["comments"]


def test_parse_plain_rejected():
    text = (
        "VERDICT: REJECTED\n"
        "COMMENTS: задача не решена, реализация делает что-то другое\n"
    )
    parsed = parse_review_text(text)
    assert parsed["verdict"] == "REJECTED"


# ---------------------------------------------------------------------------
# parse_review_text — bold markdown (gpt-oss / devstral style)
# ---------------------------------------------------------------------------

def test_parse_bold_value():
    """**VERDICT**: **APPROVED** — gpt-oss commonly bolds both label and value."""
    text = (
        "**VERDICT**: **APPROVED**\n"
        "**COMMENTS**: реализация соответствует спецификации, всё проверено\n"
    )
    parsed = parse_review_text(text)
    assert parsed["verdict"] == "APPROVED"
    assert "соответствует" in parsed["comments"]


def test_parse_bold_label_only():
    """**VERDICT:** APPROVED — colon inside the bold span."""
    text = (
        "**VERDICT:** APPROVED\n"
        "**COMMENTS:** code matches the task description, build is green\n"
    )
    parsed = parse_review_text(text)
    assert parsed["verdict"] == "APPROVED"


def test_parse_bold_summary_separator():
    """COMMENTS must stop at SUMMARY when both are bolded."""
    text = (
        "**VERDICT**: NEEDS_WORK\n"
        "**COMMENTS**: race condition between worker threads in queue.put\n"
        "**SUMMARY**: needs locking\n"
    )
    parsed = parse_review_text(text)
    assert parsed["verdict"] == "NEEDS_WORK"
    assert "race condition" in parsed["comments"]
    assert "SUMMARY" not in parsed["comments"]
    assert parsed["summary"] == "needs locking"


# ---------------------------------------------------------------------------
# parse_review_text — failure modes
# ---------------------------------------------------------------------------

def test_parse_no_verdict_returns_none():
    """Plain text without a VERDICT field is a technical failure."""
    text = "I think the code looks fine but I'm not sure."
    assert parse_review_text(text) is None


def test_parse_empty_returns_none():
    assert parse_review_text("") is None


# ---------------------------------------------------------------------------
# parse_review_text — auto-corrections
# ---------------------------------------------------------------------------

def test_approved_short_comments_downgraded():
    """APPROVED without justification (<20 chars) → NEEDS_WORK."""
    text = "VERDICT: APPROVED\nCOMMENTS: ok\n"
    parsed = parse_review_text(text)
    assert parsed["verdict"] == "NEEDS_WORK"
    assert "не обосновал" in parsed["comments"]


def test_approved_no_comments_downgraded():
    text = "VERDICT: APPROVED\n"
    parsed = parse_review_text(text)
    assert parsed["verdict"] == "NEEDS_WORK"


def test_needs_work_false_build_claim_promoted():
    """NEEDS_WORK с false 'не компилируется' при build_passed=True → APPROVED."""
    text = (
        "VERDICT: NEEDS_WORK\n"
        "COMMENTS: код не компилируется, ошибка в импортах\n"
    )
    parsed = parse_review_text(text, build_passed=True)
    assert parsed["verdict"] == "APPROVED"
    assert "автокоррекция" in parsed["comments"]


def test_needs_work_false_build_claim_kept_when_build_failed():
    """When build actually failed, the reviewer was right — keep NEEDS_WORK."""
    text = (
        "VERDICT: NEEDS_WORK\n"
        "COMMENTS: код не компилируется, ошибка в импортах\n"
    )
    parsed = parse_review_text(text, build_passed=False)
    assert parsed["verdict"] == "NEEDS_WORK"


def test_needs_work_english_compile_phrases():
    text = (
        "VERDICT: NEEDS_WORK\n"
        "COMMENTS: this does not compile because the import is wrong\n"
    )
    parsed = parse_review_text(text, build_passed=True)
    assert parsed["verdict"] == "APPROVED"


def test_rejected_terminal_keywords_set_terminal():
    """REJECTED + canonical terminal keyword → is_terminal=True."""
    text = (
        "VERDICT: REJECTED\n"
        "COMMENTS: эту задачу невозможно решить без редизайна архитектуры\n"
    )
    parsed = parse_review_text(text)
    assert parsed["verdict"] == "REJECTED"
    assert parsed["is_terminal"] is True


def test_rejected_terminal_keyword_prefix_match():
    """Prefix-style keyword 'бессмыслен' matches inflected forms."""
    text = (
        "VERDICT: REJECTED\n"
        "COMMENTS: реализация бессмысленна — задача уже решена в другом модуле\n"
    )
    parsed = parse_review_text(text)
    assert parsed["is_terminal"] is True


def test_rejected_without_terminal_keywords():
    text = (
        "VERDICT: REJECTED\n"
        "COMMENTS: реализация делает что-то другое, нужно переписать\n"
    )
    parsed = parse_review_text(text)
    assert parsed["is_terminal"] is False


def test_approved_overrides_is_terminal():
    """APPROVED + IS_TERMINAL: TRUE — weak models confuse fields, override to False."""
    text = (
        "VERDICT: APPROVED\n"
        "IS_TERMINAL: TRUE\n"
        "COMMENTS: код реализует задачу полностью, замечаний нет\n"
    )
    parsed = parse_review_text(text)
    assert parsed["verdict"] == "APPROVED"
    assert parsed["is_terminal"] is False


# ---------------------------------------------------------------------------
# calculate_consensus
# ---------------------------------------------------------------------------

def _rv(verdict, reviewer="llama"):
    return {"verdict": verdict, "reviewer": reviewer}


def test_consensus_single_approved():
    reviews = {"agentA": [_rv("APPROVED")]}
    best, verdict, _ = calculate_consensus(reviews)
    assert best == "agentA"
    assert verdict == "APPROVED"


def test_consensus_rejected_blocks_approve():
    """A single REJECTED forces NEEDS_WORK even with other APPROVEDs."""
    reviews = {
        "agentA": [_rv("APPROVED"), _rv("APPROVED"), _rv("REJECTED")],
    }
    best, verdict, _ = calculate_consensus(reviews)
    assert best == "agentA"
    assert verdict == "NEEDS_WORK"


def test_consensus_failed_excluded_from_score():
    """FAILED reviews don't contribute to the weighted score (they're tech failures)."""
    # Only 1 valid APPROVED out of 2 reviews. avg = 1.0 / 2 = 0.5 > 0 → APPROVED.
    reviews = {"agentA": [_rv("APPROVED"), _rv("FAILED")]}
    best, verdict, _ = calculate_consensus(reviews)
    assert verdict == "APPROVED"


def test_consensus_gemini_weight_breaks_ties():
    """gemini's weight=1.5 should outweigh a single llama vote."""
    reviews = {
        "agentA": [_rv("APPROVED", reviewer="gemini")],   # 1.5
        "agentB": [_rv("APPROVED", reviewer="llama")],    # 1.0
    }
    best, verdict, _ = calculate_consensus(reviews)
    assert best == "agentA"
    assert verdict == "APPROVED"


def test_consensus_picks_highest_score():
    reviews = {
        "agentA": [_rv("APPROVED"), _rv("APPROVED")],     # avg 1.0
        "agentB": [_rv("APPROVED"), _rv("NEEDS_WORK")],   # avg 0.5
    }
    best, _, _ = calculate_consensus(reviews)
    assert best == "agentA"


def test_consensus_all_needs_work():
    reviews = {"agentA": [_rv("NEEDS_WORK")]}
    best, verdict, reason = calculate_consensus(reviews)
    assert best == "agentA"
    assert verdict == "NEEDS_WORK"
    assert "доработки" in reason or "Недостаточно" in reason


def test_consensus_min_reviewers_not_met():
    reviews = {"agentA": [_rv("APPROVED")]}
    best, verdict, _ = calculate_consensus(reviews, min_reviewers=2)
    assert verdict == "NEEDS_WORK"


def test_consensus_empty_input():
    best, verdict, _ = calculate_consensus({})
    assert best == "none"
    assert verdict == "NEEDS_WORK"


# ---------------------------------------------------------------------------
# is_review_successful
# ---------------------------------------------------------------------------

def test_is_successful_approved():
    assert is_review_successful({"verdict": "APPROVED"}) is True


def test_is_successful_needs_work():
    assert is_review_successful({"verdict": "NEEDS_WORK"}) is True


def test_is_successful_rejected():
    assert is_review_successful({"verdict": "REJECTED"}) is True


def test_is_successful_failed():
    assert is_review_successful({"verdict": "FAILED"}) is False


def test_is_successful_error():
    assert is_review_successful({"verdict": "error"}) is False


def test_is_successful_missing_verdict():
    assert is_review_successful({}) is False
