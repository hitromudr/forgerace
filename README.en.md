# ForgeRace

Multi-agent development orchestrator. Run multiple AI agents (Claude, Gemini, etc.) competitively on coding tasks, with cross-review, auto-decomposition, and race-to-merge.

> [Русская версия](README.md)

## How It Works

```
  discuss new → discuss chat → /ok → TASKS.md → run → done
       ↑                                          ↓
       └── run auto-creates discussion if task is not approved
```

1. Start a discussion — agents discuss architecture together
2. `/ok` in chat — resolution is generated, tasks are auto-inserted into `TASKS.md`
3. `run` — agents competitively solve tasks, cross-review each other, first to pass gets merged

You can also write tasks in `TASKS.md` manually. But then `run` will auto-open a discussion for each unapproved task.

## Requirements

- Python 3.10+
- Git
- At least one agent CLI installed and authenticated:
  - [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`claude` CLI) — authenticates via browser (Max/Team subscription)
  - [Gemini CLI](https://github.com/google-gemini/gemini-cli) (`gemini` CLI) — authenticates via `GEMINI_API_KEY`

**Important:** ForgeRace uses official agent CLIs, not APIs directly. Authentication method depends on each CLI — see their docs.

## Quick Start

```bash
# 1. Clone ForgeRace
git clone https://github.com/hitromudr/forgerace.git ~/forgerace

# 2. Go to your project and initialize
cd ~/work/my-project
python3 ~/forgerace/forgerace.py init
# Creates forgerace.toml and TASKS.md

# 3. Configure
vim forgerace.toml    # build commands, dev_branch, agents

# 4. Start a discussion
python3 ~/forgerace/forgerace.py discuss new my-feature 'How should we implement feature X?'
python3 ~/forgerace/forgerace.py discuss chat my-feature
# Agents discuss. When ready: /ok
# → resolution generated + tasks auto-inserted into TASKS.md

# 5. Run agents
python3 ~/forgerace/forgerace.py run

# Status and dependency graph
python3 ~/forgerace/forgerace.py status
```

After first run, config is remembered — no `--config` needed.

## Discussion Flow

A discussion is a structured conversation between agents before implementation.

```bash
python3 ~/forgerace/forgerace.py discuss new auth-design 'How to implement auth?'
python3 ~/forgerace/forgerace.py discuss chat auth-design
```

Interactive chat commands:
- **text** — your comment (agents not invoked)
- **/claude**, **/gemini**, **/both** — invoke agent(s)
- **/both text** — add comment then invoke both
- **/ok** — approve and close → auto-generate tasks
- **/resolve text** — close with manual resolution
- **/show** — display full discussion
- **/exit** — quit without closing

### What `/ok` Does

1. Final round — both agents voice last objections
2. Claude generates a brief resolution (3-5 lines)
3. Resolution is written to discussion file (word `РЕЗОЛЮЦИЯ` marks approval)
4. Claude parses the discussion and generates tasks → inserted into `TASKS.md`

After `/ok`, tasks are considered approved and `run` will launch agents.

### What If a Task Has No Discussion?

If you write a task in `TASKS.md` manually without a `Discussion` field, `run` will:
1. Auto-create a discussion
2. Request replies from all agents
3. Open interactive chat — waits for `/ok` from techlead

Exception: tasks with acceptance criterion `make check` are auto-approved.

## Competitive Mode

On `run`, each task launches in **competitive mode**: all agents work in parallel in isolated git worktrees.

- First to finish → cross-reviewed by the other agent
- **APPROVED** → merged to develop, others cancelled
- **NEEDS_WORK** → rework, up to `max_review_rounds` rounds
- Review stall (same comment 2+ rounds) → escalation with summary

With multiple tasks in a batch: complex ones go competitive, simple ones are distributed across agents.

## Auto-Decomposition

Before launching agents, each task is rated for complexity (1-5). If above threshold (`max_task_complexity`, default 3) — Claude auto-splits into subtasks and inserts them into `TASKS.md`.

## Features

- **Race-to-merge** — first to pass review gets merged immediately
- **Cross-review** — agents review each other's code, never their own
- **Discussion system** — discuss → `/ok` → auto-generated tasks
- **Auto-decomposition** — complex tasks split via LLM assessment
- **Stall detection** — repeated review comment → escalation
- **MUD-style output** — colored terminal, each agent has its own color
- **Dependency graph** — `status` shows tree and bottlenecks
- **Worktree isolation** — each agent in its own git worktree

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
max_task_complexity = 3
```

Full example: `examples/example.toml`.

## Commands

| Command | Description |
|---------|-------------|
| `init` | Create `forgerace.toml` and `TASKS.md` in current directory |
| `run` | Run tasks (with auto-discussions for unapproved ones) |
| `run --task TASK-001` | Run a specific task |
| `run --retry` | Retry failed/blocked tasks |
| `run --auto` | Auto-launch next tasks as they complete |
| `status` | Task status + dependency graph |
| `discuss new TOPIC 'question'` | Create a discussion |
| `discuss chat TOPIC` | Interactive chat with agents |
| `discuss list` | List discussions |
| `discuss show TOPIC` | Show a discussion |
| `discuss regen TOPIC` | Regenerate tasks from closed discussion |
| `merge-pending` | Merge review-status tasks into develop |

## License

MIT — see [LICENSE](LICENSE).
