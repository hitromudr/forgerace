import pytest
from forgerace.review import validate_review

def test_validate_review_lowercase_severity():
    data = {
        "verdict": "APPROVED",
        "confidence": 100,
        "issues": [
            {"severity": "CRITICAL", "text": "Something is broken"}
        ]
    }
    success, error = validate_review(data)
    assert not success
    assert "Нельзя аппрувить" in error

def test_validate_review_invalid_issue_type():
    data = {
        "verdict": "NEEDS_REWORK",
        "confidence": 100,
        "issues": [
            {"text": "Fine issue"},
            123,
            ["list", "issue"]
        ]
    }
    success, error = validate_review(data)
    assert not success
    assert "Замечание должно быть строкой или словарем" in error

def test_validate_review_valid_issues():
    data = {
        "verdict": "NEEDS_REWORK",
        "confidence": 100,
        "issues": [
            {"severity": "MAJOR", "text": "Uppercase major"},
            {"text": "No severity"},
            "[MINOR] String with severity"
        ]
    }
    success, error = validate_review(data)
    assert success
    assert error == ""
    assert len(data["issues"]) == 3
    assert data["issues"][0]["severity"] == "major"
    assert data["issues"][1]["severity"] == "major"
    assert data["issues"][2]["severity"] == "minor"
