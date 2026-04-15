import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from forgerace.pipeline import run_single_agent
from forgerace.tasks import Task
from forgerace.agents import AgentProcessResult, AgentResult
from forgerace.cost import TokenUsage

def test_run_single_agent_budget_exceeded():
    task = Task(
        id="TASK-999", name="Test Task", status="open", priority="P1",
        stage="1", deps=[], files_new="src/new.py", files_modify="", integration="",
        description="", forbidden="", verification="", acceptance="",
        agent="", branch="", discussion="", raw_section=""
    )
    
    # Mocking necessary components
    with patch("forgerace.pipeline.create_worktree") as mock_create_wt, \
         patch("forgerace.pipeline.run_agent_process") as mock_run_agent, \
         patch("forgerace.pipeline.update_task_status") as mock_update_status, \
         patch("forgerace.pipeline.cfg") as mock_cfg, \
         patch("forgerace.pipeline.run_cmd") as mock_run_cmd, \
         patch("forgerace.pipeline._get_usage_cost") as mock_get_cost, \
         patch("forgerace.pipeline._register_agent"), \
         patch("forgerace.pipeline._unregister_agent"), \
         patch("forgerace.pipeline.build_prompt"), \
         patch("forgerace.pipeline.translate_slug"):
        
        mock_cfg.max_retries = 3
        mock_cfg.log_dir = Path("/tmp")
        mock_cfg.root_dir = Path("/tmp")
        mock_cfg.agents_dir = Path("/tmp/agents")
        mock_create_wt.return_value = Path("/tmp/worktree")
        mock_get_cost.return_value = 0.5
        
        # Mocking result with BUDGET_EXCEEDED in stderr
        mock_run_agent.return_value = AgentProcessResult(
            returncode=1,
            stdout="Normal output",
            stderr="BUDGET_EXCEEDED",
            usage=TokenUsage()
        )
        
        # We need to mock Path.write_text as it's used for agent logs
        with patch("pathlib.Path.write_text") as mock_write_text:
            result = run_single_agent(task, agent_num=1, agent_type="claude")
        
        # Assertions
        mock_update_status.assert_called_once_with("TASK-999", "BUDGET_EXCEEDED")
        # Ensure it stopped after the first attempt
        assert mock_run_agent.call_count == 1
        assert result.success is False
