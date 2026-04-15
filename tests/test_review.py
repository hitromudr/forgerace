import pytest
from forgerace.review import validate_review, REVIEW_SCHEMA

def test_validate_review_success_approved():
    data = {
        "verdict": "APPROVED",
        "confidence": 100,
        "issues": []
    }
    success, msg = validate_review(data)
    assert success is True
    assert msg == ""
    assert data["verdict"] == "APPROVED"
    assert data["confidence"] == 100
    assert data["issues"] == []

def test_validate_review_success_needs_rework_alias():
    # Test NEEDS_WORK alias and confidence as float string
    data = {
        "verdict": "NEEDS_WORK",
        "confidence": "85.5",
        "issues": ["[major] Some problem", "Another problem"]
    }
    success, msg = validate_review(data)
    assert success is True
    assert data["verdict"] == "NEEDS_REWORK"
    assert data["confidence"] == 85
    assert len(data["issues"]) == 2
    assert data["issues"][0] == {"severity": "major", "text": "Some problem"}
    assert data["issues"][1] == {"severity": "major", "text": "Another problem"}

def test_validate_review_success_rejected_with_issues():
    data = {
        "verdict": "REJECTED",
        "confidence": 50,
        "issues": [
            {"severity": "CRITICAL", "text": "Bad code"},
            {"text": "Missing docs"}
        ]
    }
    success, msg = validate_review(data)
    assert success is True
    assert data["verdict"] == "REJECTED"
    assert data["issues"][0] == {"severity": "critical", "text": "Bad code"}
    assert data["issues"][1] == {"severity": "major", "text": "Missing docs"}

def test_validate_review_invalid_verdict():
    data = {"verdict": "MAYBE"}
    success, msg = validate_review(data)
    assert success is False
    assert "Недопустимый вердикт" in msg

def test_validate_review_invalid_confidence_type():
    data = {"verdict": "APPROVED", "confidence": "very high"}
    success, msg = validate_review(data)
    assert success is False
    assert "Некорректное значение confidence" in msg

def test_validate_review_confidence_out_of_range():
    data = {"verdict": "APPROVED", "confidence": 101}
    success, msg = validate_review(data)
    assert success is False
    assert "вне диапазона" in msg

def test_validate_review_approved_with_critical():
    data = {
        "verdict": "APPROVED",
        "issues": ["[critical] Security flaw"]
    }
    success, msg = validate_review(data)
    assert success is False
    assert "критическими ошибками" in msg

def test_validate_review_rejected_without_issues():
    data = {
        "verdict": "REJECTED",
        "issues": []
    }
    success, msg = validate_review(data)
    assert success is False
    assert "требует указания хотя бы одной проблемы" in msg

def test_validate_review_issues_multiline_string():
    data = {
        "verdict": "NEEDS_REWORK",
        "issues": "[minor] Fix typo\nAdd tests"
    }
    success, msg = validate_review(data)
    assert success is True
    assert len(data["issues"]) == 2
    assert data["issues"][0] == {"severity": "minor", "text": "Fix typo"}
    assert data["issues"][1] == {"severity": "major", "text": "Add tests"}

def test_validate_review_invalid_issue_dict():
    data = {
        "verdict": "NEEDS_REWORK",
        "issues": [{"no_text": "here"}]
    }
    success, msg = validate_review(data)
    assert success is False
    assert "должно содержать ключ 'text'" in msg

def test_validate_review_invalid_issue_type():
    data = {
        "verdict": "NEEDS_REWORK",
        "issues": [123]
    }
    success, msg = validate_review(data)
    assert success is False
    assert "строкой или словарем" in msg

def test_validate_review_not_a_dict():
    success, msg = validate_review("not a dict")
    assert success is False
    assert "должны быть словарем" in msg
