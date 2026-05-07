"""Tests for merger agent: response parsing + decision dispatcher.

The LLM call itself is monkey-patched out — we never hit a real API
in unit tests. Integration with the full pipeline is covered by
smoke runs against TASK-041.
"""

from dataclasses import dataclass
from forgerace import merger as merger_mod
from forgerace.merger import (
    MergeDecision,
    build_merger_prompt,
    parse_merger_response,
    merger_decide,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@dataclass
class _FakeTask:
    id: str = "TASK-041"
    name: str = "format_duration utility"
    description: str = "Implement format_duration(seconds) -> str."


@dataclass
class _FakeCandidate:
    agent_type: str
    branch: str = "task/x"
    code_lines: int = 60


def _candidates(*names):
    return [_FakeCandidate(agent_type=n, branch=f"task/x-{n}") for n in names]


def _reviews(approved=(), needs_work=(), rejected=()):
    out = []
    for r in approved:
        out.append({"reviewer": r, "verdict": "APPROVED",
                    "comments": "реализация соответствует задаче, тесты проходят"})
    for r in needs_work:
        out.append({"reviewer": r, "verdict": "NEEDS_WORK",
                    "comments": "обработка нулевых единиц не учтена"})
    for r in rejected:
        out.append({"reviewer": r, "verdict": "REJECTED",
                    "comments": "реализация противоречит спеке"})
    return out


# ---------------------------------------------------------------------------
# build_merger_prompt
# ---------------------------------------------------------------------------

def test_prompt_contains_task_and_candidates():
    task = _FakeTask()
    cands = _candidates("aider-devstral", "aider-qwen")
    reviews = {
        "aider-devstral": _reviews(approved=("gemini-api",), needs_work=("gpt-oss",)),
        "aider-qwen": _reviews(approved=("gemini-api", "gpt-oss")),
    }
    diffs = {"aider-devstral": "diff-d", "aider-qwen": "diff-q"}

    prompt = build_merger_prompt(task, cands, reviews, diffs)

    assert task.id in prompt
    assert task.name in prompt
    assert "aider-devstral" in prompt
    assert "aider-qwen" in prompt
    assert "diff-d" in prompt
    assert "diff-q" in prompt
    assert "gemini-api" in prompt
    assert "DECISION:" in prompt


def test_prompt_truncates_huge_diff():
    task = _FakeTask()
    cands = _candidates("aider-devstral")
    big_diff = "x" * 50000
    prompt = build_merger_prompt(task, cands, {"aider-devstral": []},
                                  {"aider-devstral": big_diff})
    # Should not embed the full 50k chars verbatim
    assert "truncated" in prompt
    assert len(prompt) < 20000


# ---------------------------------------------------------------------------
# parse_merger_response — happy paths
# ---------------------------------------------------------------------------

def test_parse_plain_merge():
    text = (
        "DECISION: merge\n"
        "WINNER: aider-devstral\n"
        "REASON: minimal diff and matches the spec\n"
    )
    d = parse_merger_response(text)
    assert d.decision == "merge"
    assert d.winner == "aider-devstral"
    assert "minimal" in d.reason


def test_parse_merge_bold():
    text = (
        "**DECISION**: **merge**\n"
        "**WINNER**: **aider-qwen**\n"
        "**REASON**: covers edge cases\n"
    )
    d = parse_merger_response(text)
    assert d.decision == "merge"
    assert d.winner == "aider-qwen"


def test_parse_rework():
    text = "DECISION: rework\nWINNER: none\nREASON: all candidates miss zero handling\n"
    d = parse_merger_response(text)
    assert d.decision == "rework"
    assert d.winner is None
    assert "zero" in d.reason


def test_parse_fail():
    text = "DECISION: fail\nWINNER: none\nREASON: задача противоречит существующему API\n"
    d = parse_merger_response(text)
    assert d.decision == "fail"
    assert d.winner is None


def test_parse_winner_with_at_sign():
    """Some models prefix names with @ in chat-like style."""
    text = "DECISION: merge\nWINNER: @aider-devstral\nREASON: ok\n"
    d = parse_merger_response(text)
    assert d.winner == "aider-devstral"


# ---------------------------------------------------------------------------
# parse_merger_response — failure modes
# ---------------------------------------------------------------------------

def test_parse_no_decision_returns_none():
    assert parse_merger_response("I think we should merge devstral") is None


def test_parse_unknown_decision_returns_none():
    assert parse_merger_response("DECISION: postpone\nWINNER: none\n") is None


def test_parse_merge_without_winner_degrades_to_rework():
    """An LLM that says 'merge' but forgets WINNER is inconsistent —
    safer to rework than to merge nothing."""
    text = "DECISION: merge\nWINNER: none\nREASON: looks fine\n"
    d = parse_merger_response(text)
    assert d.decision == "rework"
    assert d.winner is None


# ---------------------------------------------------------------------------
# merger_decide — dispatcher
# ---------------------------------------------------------------------------

def test_decide_no_candidates_returns_fail():
    d = merger_decide(_FakeTask(), [], {}, {})
    assert d.decision == "fail"
    assert d.winner is None


def test_decide_single_candidate_still_consults_llm(monkeypatch):
    """One candidate (race-to-merge winner) — merger still runs as gate-keeper."""
    cands = _candidates("aider-devstral")

    def fake(prompt, agent_name="", tag=""):
        return "DECISION: merge\nWINNER: aider-devstral\nREASON: matches spec\n"

    monkeypatch.setattr("forgerace.agents.run_text_agent", fake)
    d = merger_decide(_FakeTask(), cands, {"aider-devstral": []},
                       {"aider-devstral": "diff"})
    assert d.decision == "merge"
    assert d.winner == "aider-devstral"


def test_decide_single_candidate_can_be_blocked_by_merger(monkeypatch):
    """Merger can refuse a single candidate that fails its rules."""
    cands = _candidates("aider-devstral")

    def fake(prompt, agent_name="", tag=""):
        return ("DECISION: rework\nWINNER: none\n"
                "REASON: не учтена обработка нулевых единиц\n")

    monkeypatch.setattr("forgerace.agents.run_text_agent", fake)
    d = merger_decide(_FakeTask(), cands, {"aider-devstral": []},
                       {"aider-devstral": "diff"})
    assert d.decision == "rework"
    assert "нулевых" in d.reason


def test_decide_invokes_llm_and_returns_parsed(monkeypatch):
    cands = _candidates("aider-devstral", "aider-qwen")
    reviews = {"aider-devstral": [], "aider-qwen": []}
    diffs = {"aider-devstral": "diff-d", "aider-qwen": "diff-q"}

    captured = {}

    def fake_run_text_agent(prompt, agent_name="", tag=""):
        captured["prompt"] = prompt
        captured["agent_name"] = agent_name
        return "DECISION: merge\nWINNER: aider-qwen\nREASON: cleaner edge-case handling\n"

    monkeypatch.setattr("forgerace.agents.run_text_agent", fake_run_text_agent)

    d = merger_decide(_FakeTask(), cands, reviews, diffs)
    assert d.decision == "merge"
    assert d.winner == "aider-qwen"
    assert "cleaner" in d.reason
    assert "aider-devstral" in captured["prompt"]


def test_decide_unknown_winner_falls_back_to_rework(monkeypatch):
    """LLM names a winner not in the candidate list — refuse to merge."""
    cands = _candidates("aider-devstral", "aider-qwen")

    def fake_run_text_agent(prompt, agent_name="", tag=""):
        return "DECISION: merge\nWINNER: aider-nonexistent\nREASON: something\n"

    monkeypatch.setattr("forgerace.agents.run_text_agent", fake_run_text_agent)
    d = merger_decide(_FakeTask(), cands, {}, {})
    assert d.decision == "rework"
    assert d.winner is None
    assert "aider-nonexistent" in d.reason


def test_decide_empty_response_falls_back(monkeypatch):
    cands = _candidates("aider-devstral", "aider-qwen")
    monkeypatch.setattr("forgerace.agents.run_text_agent", lambda *a, **kw: "")
    d = merger_decide(_FakeTask(), cands, {}, {})
    assert d.decision == "rework"


def test_decide_garbage_response_falls_back(monkeypatch):
    cands = _candidates("aider-devstral", "aider-qwen")
    monkeypatch.setattr(
        "forgerace.agents.run_text_agent",
        lambda *a, **kw: "I don't have enough information to decide.",
    )
    d = merger_decide(_FakeTask(), cands, {}, {})
    assert d.decision == "rework"


def test_decide_llm_exception_falls_back(monkeypatch):
    cands = _candidates("aider-devstral", "aider-qwen")

    def boom(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr("forgerace.agents.run_text_agent", boom)
    d = merger_decide(_FakeTask(), cands, {}, {})
    assert d.decision == "rework"
    assert "network down" in d.reason
