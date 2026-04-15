import pytest
import logging
from pathlib import Path
from unittest.mock import patch
from forgerace.config import (
    Config, AgentConfig, validate_numeric_fields, 
    validate_agent_commands, validate_paths
)
from forgerace.config_errors import ConfigValidationError

@pytest.fixture
def base_cfg():
    """Базовый валидный конфиг для тестов."""
    cfg = Config()
    cfg.root_dir = Path("/mock/root")
    cfg.agents = {
        "claude": AgentConfig(command="claude", enabled=True)
    }
    return cfg

def test_valid_config_passes(base_cfg):
    """1. Валидный конфиг — проходит все проверки."""
    with patch("shutil.which", return_value="/usr/bin/claude"):
        with patch("pathlib.Path.is_dir", return_value=True):
            validate_numeric_fields(base_cfg)
            validate_agent_commands(base_cfg)
            validate_paths(base_cfg)

def test_agent_timeout_negative(base_cfg):
    """2. agent_timeout = -1 — ConfigValidationError."""
    base_cfg.agent_timeout = -1
    with pytest.raises(ConfigValidationError, match="agent_timeout must be a positive number"):
        validate_numeric_fields(base_cfg)

def test_agent_timeout_string(base_cfg):
    """3. agent_timeout = 'string' — ConfigValidationError."""
    base_cfg.agent_timeout = "900"
    with pytest.raises(ConfigValidationError, match="agent_timeout must be a positive number"):
        validate_numeric_fields(base_cfg)

def test_agent_timeout_float_passes(base_cfg):
    """4. agent_timeout = 0.5 (float) — проходит, приводится к float."""
    base_cfg.agent_timeout = 0.5
    validate_numeric_fields(base_cfg)
    assert isinstance(base_cfg.agent_timeout, float)
    assert base_cfg.agent_timeout == 0.5

def test_max_retries_zero_passes(base_cfg):
    """5. max_retries = 0 — проходит (ноль допустим)."""
    base_cfg.max_retries = 0
    validate_numeric_fields(base_cfg)
    assert base_cfg.max_retries == 0

def test_max_retries_negative(base_cfg):
    """6. max_retries = -1 — ConfigValidationError."""
    base_cfg.max_retries = -1
    with pytest.raises(ConfigValidationError, match="max_retries must be a non-negative integer"):
        validate_numeric_fields(base_cfg)

def test_max_parallel_tasks_float(base_cfg):
    """7. max_parallel_tasks = 2.5 — ConfigValidationError (должен быть int)."""
    base_cfg.max_parallel_tasks = 2.5
    with pytest.raises(ConfigValidationError, match="max_parallel_tasks must be a positive integer"):
        validate_numeric_fields(base_cfg)

def test_progress_timeout_warning(base_cfg, caplog):
    """8. progress_timeout >= agent_timeout — warning."""
    base_cfg.agent_timeout = 100
    base_cfg.progress_timeout = 100
    with caplog.at_level(logging.WARNING):
        validate_numeric_fields(base_cfg)
    assert "progress_timeout" in caplog.text
    assert "agent_timeout" in caplog.text

def test_agent_command_empty(base_cfg):
    """9. Команда агента = '' — ConfigValidationError."""
    base_cfg.agents["claude"].command = ""
    with pytest.raises(ConfigValidationError, match="команда не может быть пустой"):
        validate_agent_commands(base_cfg)

def test_agent_command_with_args(base_cfg):
    """10. Команда с аргументами — проверяется только бинарник."""
    base_cfg.agents["claude"].command = "claude-cli --model sonnet"
    # shutil.which должен получить только "claude-cli"
    with patch("shutil.which") as mock_which:
        mock_which.return_value = "/usr/bin/claude-cli"
        validate_agent_commands(base_cfg)
        mock_which.assert_called_once_with("claude-cli")

def test_root_dir_not_exists(base_cfg):
    """11. root_dir не существует — ConfigValidationError."""
    with patch("pathlib.Path.is_dir", return_value=False):
        with pytest.raises(ConfigValidationError, match="root_dir .* не существует"):
            validate_paths(base_cfg)

def test_discuss_dir_not_exists_warning(base_cfg, caplog):
    """12. discuss_dir не существует — warning."""
    def mock_is_dir(self):
        # root_dir существует, остальное — нет
        return str(self) == "/mock/root"
    
    with patch("pathlib.Path.is_dir", side_effect=mock_is_dir, autospec=True):
        with caplog.at_level(logging.WARNING):
            validate_paths(base_cfg)
    assert "Директория дискуссий" in caplog.text

def test_max_concurrent_negative(base_cfg):
    """13. Дополнительный: max_concurrent = -1 — ConfigValidationError."""
    base_cfg.max_concurrent = -1
    with pytest.raises(ConfigValidationError, match="max_concurrent must be a positive integer"):
        validate_numeric_fields(base_cfg)

def test_agent_command_not_found_warning(base_cfg, caplog):
    """14. Дополнительный: бинарник не найден — warning, не error."""
    base_cfg.agents["claude"].command = "nonexistent"
    with patch("shutil.which", return_value=None):
        with caplog.at_level(logging.WARNING):
            validate_agent_commands(base_cfg)
    assert "не найдена в PATH" in caplog.text

def test_agent_command_not_string(base_cfg):
    """15. Дополнительный: команда не строка — ConfigValidationError."""
    base_cfg.agents["claude"].command = 123
    with pytest.raises(ConfigValidationError, match="команда должна быть строкой"):
        validate_agent_commands(base_cfg)
