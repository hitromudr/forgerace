"""Tests for probe-agents baseline comparison."""
import json
from pathlib import Path

from forgerace.agent_probe import (
    AgentDiagnosis,
    compare_with_baseline,
    find_previous_diagnostic,
)


def _diagnosis(name: str, short_avg: float | None = None,
                medium_avg: float | None = None, enabled: bool = True) -> AgentDiagnosis:
    return AgentDiagnosis(
        name=name, protocol="openai", enabled=enabled,
        short_avg=short_avg, medium_avg=medium_avg,
    )


def _write_baseline(path: Path, items: list[dict]) -> None:
    path.write_text(json.dumps({"results": items}, ensure_ascii=False), encoding="utf-8")


def test_compare_no_regression(tmp_path):
    baseline = tmp_path / "agents-old.json"
    _write_baseline(baseline, [
        {"name": "gpt-oss", "short_avg": 5.0, "medium_avg": 10.0},
    ])
    current = [_diagnosis("gpt-oss", short_avg=5.5, medium_avg=11.0)]
    regressions = compare_with_baseline(current, baseline)
    assert regressions == []


def test_compare_short_avg_regression(tmp_path):
    baseline = tmp_path / "agents-old.json"
    _write_baseline(baseline, [
        {"name": "gpt-oss", "short_avg": 5.0, "medium_avg": 10.0},
    ])
    # 12s > 5 * 2 = 10s → регресс по short_avg
    current = [_diagnosis("gpt-oss", short_avg=12.0, medium_avg=11.0)]
    regressions = compare_with_baseline(current, baseline)
    assert len(regressions) == 1
    assert regressions[0]["agent"] == "gpt-oss"
    assert regressions[0]["metric"] == "short_avg"
    assert regressions[0]["ratio"] == 12.0 / 5.0


def test_compare_skips_disabled(tmp_path):
    baseline = tmp_path / "agents-old.json"
    _write_baseline(baseline, [
        {"name": "gpt-oss", "short_avg": 5.0, "medium_avg": 10.0},
    ])
    current = [_diagnosis("gpt-oss", short_avg=99.0, medium_avg=99.0, enabled=False)]
    assert compare_with_baseline(current, baseline) == []


def test_compare_skips_unknown_agent(tmp_path):
    baseline = tmp_path / "agents-old.json"
    _write_baseline(baseline, [
        {"name": "old-agent", "short_avg": 5.0, "medium_avg": 10.0},
    ])
    current = [_diagnosis("new-agent", short_avg=99.0, medium_avg=99.0)]
    # new agent not in baseline → no comparison
    assert compare_with_baseline(current, baseline) == []


def test_compare_handles_missing_metric(tmp_path):
    baseline = tmp_path / "agents-old.json"
    _write_baseline(baseline, [
        {"name": "gpt-oss", "short_avg": None, "medium_avg": 10.0},
    ])
    current = [_diagnosis("gpt-oss", short_avg=99.0, medium_avg=11.0)]
    regressions = compare_with_baseline(current, baseline)
    # short_avg can't be compared (None in baseline), medium_avg ok
    assert regressions == []


def test_compare_custom_multiplier(tmp_path):
    baseline = tmp_path / "agents-old.json"
    _write_baseline(baseline, [
        {"name": "gpt-oss", "short_avg": 5.0, "medium_avg": 10.0},
    ])
    current = [_diagnosis("gpt-oss", short_avg=8.0, medium_avg=11.0)]
    # 8/5 = 1.6 — регресс при множителе 1.5, не при 2.0
    assert compare_with_baseline(current, baseline, multiplier=1.5)
    assert compare_with_baseline(current, baseline, multiplier=2.0) == []


def test_find_previous_diagnostic(tmp_path):
    a = tmp_path / "agents-20260101-000000.json"
    b = tmp_path / "agents-20260102-000000.json"
    a.write_text("{}")
    b.write_text("{}")
    # Newer (b) is current → previous should be a
    assert find_previous_diagnostic(tmp_path, exclude=b) == a
    # No exclude → newest one
    assert find_previous_diagnostic(tmp_path) == b


def test_find_previous_no_files(tmp_path):
    assert find_previous_diagnostic(tmp_path) is None


def test_find_previous_only_excluded(tmp_path):
    a = tmp_path / "agents-20260101-000000.json"
    a.write_text("{}")
    assert find_previous_diagnostic(tmp_path, exclude=a) is None


def test_compare_handles_corrupt_baseline(tmp_path):
    baseline = tmp_path / "agents-broken.json"
    baseline.write_text("not json{{{")
    current = [_diagnosis("gpt-oss", short_avg=99.0, medium_avg=99.0)]
    # Should warn and return empty list, not raise
    assert compare_with_baseline(current, baseline) == []
