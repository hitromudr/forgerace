# ForgeRace

Multi-agent development orchestrator. Run multiple AI agents (Claude, Gemini, etc.) competitively on coding tasks, with cross-review, auto-decomposition, and race-to-merge.

## How It Works

1. You define tasks in `TASKS.md`
2. ForgeRace launches multiple agents on each task in parallel
3. Agents solve the task in isolated git worktrees
4. Cross-review: each agent reviews the other's code (never self-review)
5. First agent to pass review gets merged — race model

Complex tasks are automatically decomposed into subtasks. Stalled reviews are detected and resolved. All agent activity streams to your terminal with MUD-style colored output — each agent gets its own color, and you see tool calls (Read, Write, Bash, etc.) in real time.

## Requirements

- Python 3.10+
- Git
- At least one agent CLI installed and authenticated:
  - [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude` CLI)
  - [Gemini CLI](https://github.com/google-gemini/gemini-cli) (`gemini` CLI)

**Important:** ForgeRace uses official agent CLIs (not API keys). Agents authenticate through your browser — run `claude` or `gemini` once to log in, and ForgeRace reuses that session. This means you use your existing Claude/Gemini subscription, no separate API billing.

## Quick Start

```bash
# 1. Clone ForgeRace
git clone https://github.com/hitromudr/forgerace.git ~/forgerace

# 2. Go to your project and initialize
cd ~/work/my-project
python3 ~/forgerace/forgerace.py init
# Creates forgerace.toml and TASKS.md

# 3. Edit config and add tasks
vim forgerace.toml    # set build commands, dev_branch, etc.
vim TASKS.md          # define tasks (see examples/)

# 4. Run
python3 ~/forgerace/forgerace.py run

# After first run, config is remembered — no --config needed:
python3 ~/forgerace/forgerace.py status
python3 ~/forgerace/forgerace.py run
```

Optionally install as a package for shorter commands:
```bash
pip install -e ~/forgerace
forgerace run
```

### Discussion System

Before jumping into implementation, use structured discussions to align agents on architecture:

```bash
forgerace discuss new auth-design 'How should we structure the auth module?'
forgerace discuss chat auth-design    # agents discuss back and forth
forgerace discuss show auth-design    # view the conversation
forgerace run                         # tasks linked to discussions get context
```

## Features

- **Competitive mode** — multiple agents solve the same task, best solution wins
- **Race-to-merge** — first agent to pass review gets merged immediately
- **Cross-review** — agents review each other's code, not their own
- **Auto-decomposition** — complex tasks are split into subtasks via LLM assessment
- **Review stall detection** — detects and breaks out of review loops
- **Discussion system** — structured architecture discussions before implementation
- **Streaming output** — real-time colored terminal output showing agent tool calls
- **Git worktree isolation** — each agent works in its own worktree, no conflicts

## Task Format (TASKS.md)

```markdown
### TASK-001: Feature name
- **Статус**: open
- **Приоритет**: P1
- **Зависимости**: TASK-000 or —
- **Файлы (новые)**: src/path/file.py
- **Файлы (modify)**: — or path
- **Описание**: what to implement
- **Критерий готовности**: what should work
- **Дискуссия**: topic-name or —
- **Агент**: —
- **Ветка**: —
```

Field names are in Russian (as parsed by the task engine). See `examples/TASKS.md` for complete examples.

## Configuration (forgerace.toml)

```toml
[project]
name = "my-project"
context = "brief project description for agent context"
root = "."
dev_branch = "develop"

[agents.claude]
command = "claude"
args = ["-p", "--allowedTools", "Read,Write,Edit,Bash,Grep,Glob,WebFetch,WebSearch",
        "--max-turns", "50", "--output-format", "stream-json", "--verbose"]
review_args = ["-p", "-", "--output-format", "text", "--permission-mode", "auto"]
inactivity_timeout = 300

[agents.gemini]
command = "gemini"
args = ["-p", "--approval-mode", "yolo", "--output-format", "stream-json"]
review_args = ["-p", "-"]
inactivity_timeout = 180

[build]
commands = [["make", "build"], ["make", "test"]]
check_command = "make check"

[limits]
max_parallel_tasks = 4
agent_timeout = 900
max_review_rounds = 3
```

See `examples/example.toml` for a complete configuration reference.

## Architecture

```
forgerace/
  __init__.py     — package entry
  config.py       — configuration loading (TOML)
  tasks.py        — TASKS.md parser, task model
  agents.py       — agent runners (Claude, Gemini, streaming)
  review.py       — cross-review, verdict parsing
  pipeline.py     — main pipeline: competitive execution, race model
  decompose.py    — complexity assessment, auto-decomposition
  discuss.py      — discussion system (create, reply, chat, resolve)
  worktree.py     — git worktree management
  merge.py        — merge to develop (detached worktree + update-ref)
  utils.py        — run_cmd, logging, heartbeat
  cli.py          — CLI entry point (argparse)
```

## License

MIT — see [LICENSE](LICENSE).
