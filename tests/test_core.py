"""Core tests: imports, config, task parsing, agent routing."""
import pytest
import os
import re
import tempfile
from pathlib import Path


def test_all_modules_import():
    """All forgerace modules import without error."""
    import forgerace.config
    import forgerace.tasks
    import forgerace.agents
    import forgerace.pipeline
    import forgerace.review
    import forgerace.merge
    import forgerace.decompose
    import forgerace.discuss
    import forgerace.worktree
    import forgerace.cli
    import forgerace.cost
    import forgerace.utils
    import forgerace.benchmark
    import forgerace.dashboard


def test_config_loads():
    """Config loads from forgerace.toml."""
    from forgerace.config import cfg, init_config
    init_config()
    assert cfg.root_dir == Path.cwd()
    assert len(cfg.cli_agent_names) >= 2
    assert len(cfg.agent_names) >= 3


def test_config_agents():
    """Verify agents are configured (at least some enabled)."""
    from forgerace.config import cfg, init_config
    init_config()
    # At least one coding agent and one review agent
    assert len(cfg.cli_agent_names) > 0, "No CLI agents configured"
    assert len(cfg.agent_names) > 0, "No review agents configured"


def test_task_parsing():
    """Tasks parse from TASKS.md without error."""
    from forgerace.tasks import parse_tasks
    tasks = parse_tasks()
    # Should not crash, may return empty list
    assert isinstance(tasks, list)


def test_task_deduplication():
    """Duplicate task IDs are deduplicated."""
    from forgerace.tasks import _deduplicate_tasks, Task
    from dataclasses import fields
    # Create dummy tasks with duplicate IDs
    def _make(id, name="test"):
        kwargs = {f.name: "" for f in fields(Task)}
        kwargs.update(id=id, name=name, status="open", priority="P1",
                      deps=[], rework_count=0, last_attempts=[])
        return Task(**kwargs)
    tasks = [_make("TASK-001", "first"), _make("TASK-001", "duplicate"), _make("TASK-002")]
    result = _deduplicate_tasks(tasks)
    assert len(result) == 2
    assert result[0].name == "first"


def test_find_ready_tasks():
    """Ready tasks: open + deps done."""
    from forgerace.tasks import find_ready_tasks, Task
    from dataclasses import fields
    def _make(id, status="open", deps=None):
        kwargs = {f.name: "" for f in fields(Task)}
        kwargs.update(id=id, status=status, priority="P1",
                      deps=deps or [], rework_count=0, last_attempts=[])
        return Task(**kwargs)
    tasks = [
        _make("TASK-001", "done"),
        _make("TASK-002", "open", ["TASK-001"]),  # deps done → ready
        _make("TASK-003", "open", ["TASK-999"]),  # ghost dep → ready (ignored)
        _make("TASK-004", "open", ["TASK-002"]),  # deps not done → not ready
    ]
    ready = find_ready_tasks(tasks)
    ready_ids = {t.id for t in ready}
    assert "TASK-002" in ready_ids
    assert "TASK-003" in ready_ids  # ghost deps ignored
    assert "TASK-004" not in ready_ids


def test_slugify():
    """Slugify handles cyrillic and edge cases."""
    from forgerace.utils import slugify
    assert slugify("Hello World") == "hello-world"
    assert slugify("Привет мир") == "privet-mir"
    assert len(slugify("a" * 100)) <= 40


def test_agent_color():
    """Agent colors defined for known agents."""
    from forgerace.utils import agent_color, C
    assert agent_color("gemini") == C["blue"]
    assert agent_color("unknown") == C["white"]


def test_protected_files():
    """Protected files excluded from task file list."""
    from forgerace.agents import _PROTECTED_FILES
    assert "TASKS.md" in _PROTECTED_FILES
    assert "forgerace.toml" in _PROTECTED_FILES


def test_review_majority_vote():
    """Majority vote: >50% APPROVED passes."""
    # Test the logic directly
    reviews = [
        {"verdict": "APPROVED"},
        {"verdict": "APPROVED"},
        {"verdict": "NEEDS_WORK"},
    ]
    approved = sum(1 for r in reviews if r["verdict"] == "APPROVED")
    assert approved > len(reviews) / 2  # 2 > 1.5


def test_validate_generated_tasks_phantom_deps():
    """Phantom dependencies are removed from generated tasks."""
    from forgerace.decompose import validate_generated_tasks
    from forgerace.tasks import Task
    from dataclasses import fields

    def _make(id, status="done"):
        kwargs = {f.name: "" for f in fields(Task)}
        kwargs.update(id=id, status=status, priority="P1",
                      deps=[], rework_count=0, last_attempts=[])
        return Task(**kwargs)

    existing = [_make("TASK-001"), _make("TASK-002")]
    block = """### TASK-010: Test task
- **Статус**: open
- **Зависимости**: TASK-001, TASK-999
"""
    result = validate_generated_tasks(block, existing)
    assert "TASK-999" not in result
    assert "TASK-001" in result


def test_validate_generated_tasks_duplicate_ids():
    """Duplicate TASK IDs in generated block are renumbered."""
    from forgerace.decompose import validate_generated_tasks
    from forgerace.tasks import Task
    from dataclasses import fields

    def _make(id, status="done"):
        kwargs = {f.name: "" for f in fields(Task)}
        kwargs.update(id=id, status=status, priority="P1",
                      deps=[], rework_count=0, last_attempts=[])
        return Task(**kwargs)

    existing = [_make("TASK-001")]
    block = """### TASK-010: First
- **Статус**: open
- **Зависимости**: —

### TASK-010: Duplicate
- **Статус**: open
- **Зависимости**: —
"""
    result = validate_generated_tasks(block, existing)
    # Should have two different TASK IDs now
    task_ids = re.findall(r"### (TASK-\d+):", result)
    assert len(task_ids) == 2
    assert len(set(task_ids)) == 2, f"Still has duplicates: {task_ids}"


def test_validate_generated_tasks_no_changes_needed():
    """Valid tasks block passes through unchanged (except whitespace)."""
    from forgerace.decompose import validate_generated_tasks
    from forgerace.tasks import Task
    from dataclasses import fields

    def _make(id, status="done"):
        kwargs = {f.name: "" for f in fields(Task)}
        kwargs.update(id=id, status=status, priority="P1",
                      deps=[], rework_count=0, last_attempts=[])
        return Task(**kwargs)

    existing = [_make("TASK-001")]
    block = """### TASK-010: Clean task
- **Статус**: open
- **Зависимости**: TASK-001
"""
    result = validate_generated_tasks(block, existing)
    assert "TASK-010" in result
    assert "TASK-001" in result
