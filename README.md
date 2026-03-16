# ForgeRace

Multi-agent development orchestrator. Run multiple AI agents (Claude, Gemini, etc.) competitively on coding tasks, with cross-review, auto-decomposition, and race-to-merge.

## Features

- **Competitive mode**: Multiple agents solve the same task, cross-review each other, best wins
- **Distributed mode**: Different tasks assigned to different agents for parallelism
- **Race model**: First agent to pass review gets merged immediately
- **Auto-decomposition**: Complex tasks split into subtasks via LLM complexity assessment
- **Cross-review**: Each agent reviews the other's code (not self-review)
- **Streaming**: Real-time tool call events from agents (Read, Write, Bash, etc.)
- **Auto-checkpoint**: `make check` runs automatically when all tasks are done
- **Discussion system**: Structured architecture discussions before implementation

## Supported Agents

- **Claude Code** (`claude` CLI) — with stream-json event parsing
- **Gemini CLI** (`gemini` CLI) — with stream-json event parsing
- Extensible: add new agents in config

## Quick Start

```bash
# 1. Create TASKS.md with your tasks
# 2. Configure forgerace.toml
# 3. Run
python3 forgerace.py run

# Or with discussion first
python3 forgerace.py discuss new my-feature 'How should we implement X?'
python3 forgerace.py discuss chat my-feature
python3 forgerace.py run
```

## Task Format (TASKS.md)

```markdown
### TASK-001: Feature name
- **Status**: open
- **Priority**: P1
- **Dependencies**: TASK-000 or —
- **Files (new)**: src/path/file.rs
- **Files (modify)**: — or path
- **Description**: what to implement
- **Acceptance**: what should work
- **Discussion**: topic-name
- **Agent**: —
- **Branch**: —
```

## Configuration (forgerace.toml)

```toml
[project]
name = "my-project"
root = "."
dev_branch = "develop"

[agents]
claude = { command = "claude", args = ["-p", "--max-turns", "50"] }
gemini = { command = "gemini", args = ["-p", "--approval-mode", "yolo"] }

[build]
commands = [["cargo", "build"], ["cargo", "test", "--no-run"]]
check_commands = ["make check"]

[limits]
max_parallel_tasks = 4
agent_timeout = 900
inactivity_timeout_claude = 300
inactivity_timeout_gemini = 180
max_review_rounds = 3
max_task_complexity = 3
```

## Architecture

```
forgerace/
  __init__.py
  config.py       — Configuration loading (TOML)
  tasks.py        — TASKS.md parser, task model
  agents.py       — Agent runners (Claude, Gemini, streaming)
  review.py       — Cross-review, single review, verdict parsing
  pipeline.py     — Main pipeline: competitive, distributed, race model
  decompose.py    — Complexity assessment, auto-decomposition
  discuss.py      — Discussion system (create, reply, chat, resolve)
  worktree.py     — Git worktree management
  merge.py        — Merge to develop (detached worktree + update-ref)
  utils.py        — run_cmd, logging, heartbeat
  cli.py          — CLI entry point (argparse)
```

## License

MIT
