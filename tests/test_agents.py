import pytest
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from forgerace.agents import _run_agent_streaming, AgentProcessResult
from forgerace.config import cfg
from forgerace.cost import TokenUsage

def test_budget_exceeded():
    """Тест превышения бюджета в _run_agent_streaming."""
    # Настройка конфига
    cfg.budget_per_task_usd = 0.5
    cfg.agent_timeout = 10
    cfg.progress_timeout = 10

    cmd = ["dummy"]
    workdir = Path(".")
    tag = "test/budget"
    inactivity_timeout = 5

    # Мокаем log_event_fn, чтобы он увеличивал затраты
    def mock_log_event(tag, event, usage_acc=None):
        if usage_acc and "cost" in event:
            usage_acc.estimated_usd += event["cost"]

    mock_activity_check = MagicMock(return_value=False)
    mock_extract_result = MagicMock(return_value="done")

    # Мокаем subprocess.Popen и select.select
    with (
        patch("subprocess.Popen") as mock_popen,
        patch("select.select") as mock_select,
        patch("forgerace.agents._get_diff_snapshot") as mock_diff
    ):
        mock_diff.return_value = "stat"
        
        proc = mock_popen.return_value
        proc.stdout.readline.side_effect = [
            json.dumps({"type": "info", "cost": 0.1}) + "\n",
            json.dumps({"type": "info", "cost": 0.6}) + "\n",  # Итого 0.7 > 0.5
            ""
        ]
        proc.poll.return_value = None
        proc.stderr.read.return_value = ""
        proc.pid = 12345
        
        # select.select возвращает ([proc.stdout], [], []) когда есть данные
        mock_select.side_effect = [
            ([proc.stdout], [], []),
            ([proc.stdout], [], []),
            ([], [], [])
        ]

        result = _run_agent_streaming(
            cmd, workdir, tag, inactivity_timeout,
            mock_log_event, mock_activity_check, mock_extract_result
        )

        assert result.stderr == "BUDGET_EXCEEDED"
        assert result.usage.estimated_usd == 0.7
        # Должен быть вызван terminate (или kill)
        assert proc.terminate.called or proc.kill.called
