# ForgeRace — Multi-Agent Development Orchestrator

## What it does

ForgeRace runs multiple AI coding agents (Claude, Gemini, Aider-based LLM agents) in parallel on the same task, in a competitive race-to-merge mode. The first agent whose code passes cross-review by other agents gets merged. Failed agents are killed.

## Architecture

Python 3.10+, single package `forgerace/` (26 modules), entry point `forgerace.py` → `forgerace.cli.main_with_signal_handling()`.

### Core modules

| Module | Purpose |
|--------|---------|
| `config.py` | Config dataclass + TOML loader. Global `cfg` singleton, mutated in-place by `init_config()`. |
| `tasks.py` | Parser for `TASKS.md`, `Task` model, status updates, dependency resolution. |
| `agents.py` | Agent process launcher, stdin prompt injection, stream-json/text event parsing. |
| `review.py` | Cross-review: agents review each other's code. Tier-based prompts (strong/medium/weak). Majority vote. |
| `pipeline.py` | Main loop: `run_pipeline` → `execute_task_competitive`. Heartbeat thread monitors progress. |
| `discuss.py` | Architectural discussions: agents debate approach before coding. Auto-resolve at consensus. Interactive chat with `/solo`, `/fresh`, `/compact`, `/undo`, `/reopen`, `/summary`, `/tasks`, `/cd`. |
| `decompose.py` | LLM-based task complexity estimation and splitting. |
| `merge.py` | Merge via detached worktree + `git update-ref` (no checkout in main repo). |
| `worktree.py` | Git worktree isolation per agent. |

### CLI tools

| Command | Purpose |
|---------|---------|
| `./fr run` | Run pipeline (competitive mode) |
| `./fr discuss` | Architectural discussions (new/chat/list/show/round/msg/resolve/regen) |
| `./fr task` | Task management (list/add/edit/archive) |
| `./fr retry` | Smart retry with failure analysis and agent rotation |
| `./fr logs` | Log viewer (list/show/follow) |
| `./fr doctor` | Diagnose and fix environment issues |
| `./fr status` | Show task/agent status |
| `./fr monitor` | Live dashboard |
| `./fr merge-pending` | Merge approved worktrees |

### Agent protocols

- **CLI agents** (claude, gemini): stdin prompt, stream-json output
- **Aider agents**: stdin prompt via `--message-file /dev/stdin`, text output, `--no-auto-commits`
- **OpenAI-compatible API** (nvidia free tier): direct HTTP, used for discussions and reviews
- **Goose agents**: experimental, currently disabled

### Key patterns

- **Competitive mode**: all enabled agents race on same task, first approved wins
- **Tier-based prompts**: strong/medium/weak agents get different prompt complexity
- **Worktree isolation**: each agent works in its own git worktree
- **Progress timeout**: agent killed if diff unchanged for N seconds
- **Signal handling**: process group management, SIGINT/SIGTERM propagation
- **Task archival**: done/skip tasks moved to `done/TASKS_YYYY-MM-DD.md`

## Configuration

Everything in `forgerace.toml`: `[project]`, `[agents.*]`, `[build]`, `[limits]`, `[frames.*]`.

## Dependencies

- Python 3.10+ (tomli for <3.11)
- External CLIs: claude, gemini, aider, goose (optional)
- LiteLLM proxy (localhost:4000) for routing to nvidia API with tool_use support
- Git (worktree support required)
