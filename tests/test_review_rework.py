import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from forgerace.tasks import Task
from forgerace.agents import AgentResult
from forgerace.review import send_to_rework
import forgerace.pipeline
import forgerace.tasks

@pytest.fixture
def mock_task():
    return Task(
        id="TASK-101",
        name="Test Task",
        status="open",
        priority="P1",
        stage="1",
        deps=[],
        files_new="file1.py",
        files_modify="—",
        integration="—",
        description="Test Description",
        forbidden="—",
        verification="—",
        acceptance="—",
        agent="—",
        branch="task/101-test",
        discussion="—",
        raw_section="### TASK-101: Test Task..."
    )

@pytest.fixture
def mock_agent_result(tmp_path):
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    return AgentResult(
        agent_type="test-agent",
        branch="task/101-test",
        workdir=workdir,
        success=True
    )

@patch("forgerace.review.run_agent_process")
@patch("forgerace.review.run_cmd")
@patch("forgerace.pipeline.verify_build")
@patch("forgerace.tasks.update_task_status")
def test_send_to_rework_updates_counters(mock_update, mock_verify, mock_run_cmd, mock_run_agent, mock_task, mock_agent_result):
    # Setup mocks
    mock_verify.return_value = (True, "")
    
    # Mock git diff --cached --stat output
    mock_diff_res = MagicMock()
    mock_diff_res.stdout = " 1 file changed, 10 insertions(+)"
    mock_run_cmd.side_effect = lambda cmd, **kwargs: mock_diff_res if "--stat" in cmd else MagicMock(returncode=0, stdout="")

    comments = "Please fix the indentation."
    
    # Initial state
    assert mock_task.rework_count == 0
    assert len(mock_task.last_attempts) == 0

    # Execute
    result = send_to_rework(mock_agent_result, mock_task, comments)

    # Verify
    assert result is True
    assert mock_task.rework_count == 1
    assert len(mock_task.last_attempts) == 1
    assert mock_task.last_attempts[0]["comments"] == comments
    assert "1 file changed" in mock_task.last_attempts[0]["diff"]
    assert "timestamp" in mock_task.last_attempts[0]
    
    # Second rework
    new_comments = "Still needs work."
    send_to_rework(mock_agent_result, mock_task, new_comments)
    
    assert mock_task.rework_count == 2
    assert len(mock_task.last_attempts) == 2
    assert mock_task.last_attempts[1]["comments"] == new_comments

def test_task_initialization(mock_task):
    assert mock_task.rework_count == 0
    assert mock_task.last_attempts == []
