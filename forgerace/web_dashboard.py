"""Production web dashboard for ForgeRace — single-file HTTP server with SSE.

Exposes full CLI functionality: dashboard, discussions, agents, history, settings.
"""

import glob as _glob
import json, os, re, subprocess, sys, threading, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path

from .config import cfg
from .tasks import parse_tasks, update_task_status

# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------

def _detect_litellm() -> bool:
    try:
        clean_env = {k: v for k, v in os.environ.items()
                     if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
        r = subprocess.run(["curl", "-s", "--connect-timeout", "1", "-o", "/dev/null",
                            "-w", "%{http_code}", "http://127.0.0.1:4000/health"],
                           capture_output=True, text=True, timeout=3, env=clean_env)
        return r.stdout.strip() in ("200", "401")
    except Exception:
        return False


def _detect_active_agents() -> list[dict]:
    agents = []
    log_dir = cfg.log_dir
    if not log_dir.exists():
        return agents
    now = time.time()
    for logfile in sorted(log_dir.iterdir()):
        if not logfile.name.endswith(".log"):
            continue
        try:
            mtime = logfile.stat().st_mtime
            if now - mtime > 120:
                continue
            stem = logfile.stem
            parts = stem.split("_", 1)
            task_id = agent_name = ""
            for p in parts:
                if p.startswith("TASK-"):
                    task_id = p
                else:
                    agent_name = p
            if task_id and agent_name:
                agents.append({"agent": agent_name, "task": task_id,
                    "since": time.strftime("%H:%M:%S", time.localtime(mtime - 60))})
        except OSError:
            continue
    return agents


def _count_processes() -> int:
    try:
        out = subprocess.run(["pgrep", "-c", "-f", "forgerace"],
                             capture_output=True, text=True, timeout=3)
        return max(0, int(out.stdout.strip()) - 1)
    except Exception:
        return 0


def _list_teams() -> list[str]:
    teams = set()
    try:
        tasks = parse_tasks()
        for t in tasks:
            d = t.discussion or ""
            if d and d != "\u2014" and len(d) < 60 and "**" not in d:
                teams.add(d)
    except Exception:
        pass
    return sorted(teams)


_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

def _read_history(limit: int = 20) -> list[dict]:
    events = []
    log_dir = cfg.log_dir
    if not log_dir.exists():
        return events
    for logfile in sorted(log_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True):
        if not logfile.name.endswith(".log"):
            continue
        try:
            lines = logfile.read_text(errors="replace").splitlines()[-100:]
            for line in reversed(lines):
                line = _ANSI_RE.sub('', line).strip()
                if not line or "TASK" not in line:
                    continue
                kind = "info"
                ll = line.lower()
                if "done" in ll or "approved" in ll or "merged" in ll or "\u2713" in line:
                    kind = "done"
                elif "blocked" in ll or "failed" in ll or "\u2717" in line or "\u274c" in line:
                    kind = "blocked"
                elif "review" in ll or "\u0440\u0435\u0432\u044c\u044e" in ll:
                    kind = "review"
                elif "progress" in ll or "coding" in ll or "\u26a1" in line:
                    kind = "coding"
                events.append({"text": line[:120], "kind": kind})
                if len(events) >= limit:
                    return events
        except OSError:
            continue
    return events


def _build_snapshot() -> dict:
    try:
        tasks = parse_tasks()
    except Exception:
        tasks = []

    teams: dict[str, dict] = {}
    for t in tasks:
        d = t.discussion or ""
        if d and d != "\u2014" and len(d) < 60 and "**" not in d:
            grp = teams.setdefault(d, {"done": 0, "total": 0, "skip": 0, "tasks": []})
        else:
            grp = teams.setdefault("(ungrouped)", {"done": 0, "total": 0, "skip": 0, "tasks": []})
        status_base = t.status.split(":")[0] if t.status else "open"
        grp["total"] += 1
        if status_base == "done":
            grp["done"] += 1
        elif status_base == "skip":
            grp["skip"] += 1
        grp["tasks"].append({"id": t.id, "name": t.name, "status": status_base,
            "agent": t.agent if t.agent and t.agent != "\u2014" else ""})

    total_done = sum(g["done"] + g["skip"] for g in teams.values())
    total_all = sum(g["total"] for g in teams.values())

    return {
        "timestamp": time.strftime("%H:%M:%S"),
        "processes": _count_processes(),
        "litellm": _detect_litellm(),
        "active_agents": _detect_active_agents(),
        "total_done": total_done,
        "total_all": total_all,
        "teams": teams,
        "team_names": _list_teams(),
        "history": _read_history(20),
        "mode": cfg.mode,
        "agent_count": len(cfg.agent_names),
    }


def _list_discussions() -> list[dict]:
    result = []
    ddir = cfg.discuss_dir
    if not ddir.exists():
        return result
    for f in sorted(ddir.glob("*.md")):
        # Exclude task copies (e.g. my-feature-tasks.md)
        if f.name.endswith("-tasks.md"):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            status = "resolved" if ("РЕЗОЛЮЦИЯ" in text or "--- RESOLVED ---" in text) else "open"
            participants = set(re.findall(r"^## @(\S+)", text, re.MULTILINE))
            msg_count = len(re.findall(r"^## @", text, re.MULTILINE))
            # Display name: remove hyphens, capitalize first letter
            display_name = f.stem.replace("-", " ").capitalize()
            result.append({"topic": f.stem, "display_name": display_name,
                           "status": status, "msg_count": msg_count,
                           "participants": len(participants),
                           "participant_names": sorted(participants)})
        except OSError:
            continue
    return result


def _show_discussion(topic: str) -> str:
    fp = cfg.discuss_dir / f"{topic}.md"
    if fp.exists():
        return fp.read_text(encoding="utf-8", errors="replace")
    return ""


def _list_agents_info() -> list[dict]:
    result = []
    for name, acfg in cfg.agents.items():
        # Check last activity from logs
        last_activity = ""
        log_dir = cfg.log_dir
        if log_dir.exists():
            for logfile in sorted(log_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True):
                if name in logfile.name and logfile.name.endswith(".log"):
                    try:
                        last_activity = time.strftime("%H:%M:%S", time.localtime(logfile.stat().st_mtime))
                    except OSError:
                        pass
                    break
        # Count completed tasks
        active = any(a["agent"] == name for a in _detect_active_agents())
        status = "active" if active else ("idle" if acfg.enabled else "disabled")
        result.append({
            "name": name,
            "type": "API" if acfg.protocol == "openai" else "CLI",
            "protocol": acfg.protocol,
            "enabled": acfg.enabled,
            "status": status,
            "last_activity": last_activity,
            "command": acfg.command,
        })
    return result


def _read_config_file() -> str:
    for name in ("forgerace.toml",):
        p = cfg.root_dir / name
        if p.exists():
            try:
                return p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    return "# No forgerace.toml found"


def _get_parsed_config() -> dict:
    """Return structured config parsed from cfg object (not from file)."""
    project = {
        "name": getattr(cfg, "project_context", "")[:80] or "(not set)",
        "root": str(cfg.root_dir),
        "mode": cfg.mode,
        "dev_branch": cfg.dev_branch,
    }
    agents = []
    for name, acfg in cfg.agents.items():
        agents.append({
            "name": name,
            "command": acfg.command,
            "protocol": acfg.protocol,
            "enabled": acfg.enabled,
            "model": acfg.model if acfg.protocol == "openai" else "",
        })
    limits = {
        "max_parallel_tasks": cfg.max_parallel_tasks,
        "agent_timeout": cfg.agent_timeout,
        "max_review_rounds": cfg.max_review_rounds,
        "max_task_complexity": cfg.max_task_complexity,
        "progress_timeout": cfg.progress_timeout,
        "max_concurrent": cfg.max_concurrent,
        "review_consensus": cfg.review_consensus,
        "min_reviewers": cfg.min_reviewers,
        "preflight": cfg.preflight,
    }
    if cfg.budget_per_task_usd is not None:
        limits["budget_per_task_usd"] = cfg.budget_per_task_usd
    frames = []
    for name, fcfg in cfg.frames.items():
        frames.append({"name": name, "description": fcfg.description})
    build = {
        "commands": cfg.build_commands,
        "lint_fix": cfg.lint_commands,
        "check_command": cfg.check_command,
    }
    return {"project": project, "agents": agents, "limits": limits,
            "frames": frames, "build": build}


def _get_system_info() -> dict:
    branch = ""
    try:
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                cwd=str(cfg.root_dir), capture_output=True, text=True,
                                timeout=3).stdout.strip()
    except Exception:
        pass
    return {
        "python": sys.version.split()[0],
        "branch": branch,
        "root": str(cfg.root_dir),
        "mode": cfg.mode,
        "frames": sorted(cfg.frames.keys()) if cfg.frames else [],
    }


# ---------------------------------------------------------------------------
# HTML/CSS/JS — full management UI
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ForgeRace Dashboard</title>
<style>
:root{--bg:#1a1a2e;--card:#16213e;--border:#2a2a4e;--hover:#1e2748;--green:#4ade80;--red:#ef4444;--yellow:#eab308;--purple:#a855f7;--blue:#60a5fa;--gray:#6b7280;--text:#e0e0e0;--text-dim:#9ca3af}
*{box-sizing:border-box;margin:0;padding:0}body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--green);text-decoration:none}.mono{font-family:'JetBrains Mono',monospace;font-size:.9em}
.header{display:flex;flex-wrap:wrap;align-items:center;gap:1em;padding:1em 1.5em;border-bottom:1px solid var(--border);background:var(--card)}
.header h1{font-size:1.3em;font-weight:700}.header h1 span{color:var(--green)}
.summary{display:flex;flex-wrap:wrap;gap:.5em;margin-left:auto;align-items:center}
.pill{background:var(--card);border:1px solid var(--border);border-radius:20px;padding:.3em .8em;font-size:.8em;white-space:nowrap;cursor:pointer;transition:all .2s}
.pill:hover{background:var(--hover);border-color:var(--text-dim)}
.pill.on{border-color:var(--green);color:var(--green)}.pill.off,.pill.danger{border-color:var(--red);color:var(--red)}
.pill.starting{border-color:var(--yellow);color:var(--yellow)}.pill.action{border-color:var(--blue);color:var(--blue)}
.pill.mode{border-color:var(--purple);color:var(--purple)}
.status-bar{display:flex;flex-wrap:wrap;gap:1em;padding:.5em 1.5em;background:rgba(22,33,62,.5);border-bottom:1px solid var(--border);font-size:.8em;color:var(--text-dim);align-items:center}
.ts{color:var(--gray);font-size:.8em}
/* Tabs */
.tabs{display:flex;gap:0;padding:0 1.5em;background:var(--card);border-bottom:2px solid var(--border)}
.tab{padding:.7em 1.2em;cursor:pointer;font-size:.85em;font-weight:500;color:var(--text-dim);border-bottom:2px solid transparent;margin-bottom:-2px;transition:all .2s;user-select:none}
.tab:hover{color:var(--text)}.tab.active{color:var(--green);border-bottom-color:var(--green)}
.tab-content{display:none;padding:1.2em 1.5em}.tab-content.active{display:block}
/* Controls */
.controls{display:flex;flex-wrap:wrap;align-items:center;gap:.6em;margin-bottom:1em}
.controls select{background:var(--card);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:.35em .7em;font-size:.85em}
/* Activity */
.activity{margin-bottom:1em}.activity-title{font-size:.9em;color:var(--purple);margin-bottom:.4em;font-weight:600}
.agent-card{display:inline-flex;align-items:center;gap:.5em;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:.35em .7em;margin:0 .4em .4em 0;font-size:.82em}
.agent-card .dot{width:8px;height:8px;border-radius:50%;background:var(--purple);animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
/* Teams */
.team{background:var(--card);border:1px solid var(--border);border-radius:10px;margin-bottom:.8em;overflow:hidden;transition:border-color .2s}
.team:hover{border-color:var(--green)}.team-header{display:flex;align-items:center;gap:.8em;padding:.7em 1em;cursor:pointer;user-select:none}
.team-header:hover{background:var(--hover)}.team-name{font-weight:600;color:var(--yellow);flex:1}.team-stats{font-size:.82em;color:var(--gray)}
.team-chevron{color:var(--gray);transition:transform .3s;font-size:.75em}.team.collapsed .team-chevron{transform:rotate(-90deg)}
.team.collapsed .team-body{max-height:0;padding:0;opacity:0;overflow:hidden}
.bar-wrap{width:120px;height:8px;background:var(--border);border-radius:5px;overflow:hidden;flex-shrink:0}
.bar-fill{height:100%;border-radius:5px;background:linear-gradient(90deg,#22c55e,var(--green));transition:width .6s;position:relative}
.bar-fill::after{content:'';position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,.15) 50%,transparent);animation:shimmer 2s infinite}
@keyframes shimmer{0%{transform:translateX(-100%)}100%{transform:translateX(100%)}}
.team-body{padding:0 1em;max-height:2000px;opacity:1;transition:max-height .4s,opacity .3s,padding .3s}
.task{display:flex;align-items:center;gap:.5em;padding:.35em .5em;border-radius:6px;font-size:.85em}.task:hover{background:var(--hover)}.task:last-child{margin-bottom:.5em}
.task-icon{width:1.3em;text-align:center;flex-shrink:0}.task-id{color:var(--gray);font-family:monospace;font-size:.82em;width:5.5em;flex-shrink:0}
.task-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.task-agent{font-size:.72em;color:var(--gray);background:var(--border);border-radius:4px;padding:.12em .4em;flex-shrink:0}
.st-done .task-icon{color:var(--green)}.st-skip .task-icon{color:var(--gray)}.st-in_progress .task-icon{color:var(--purple);animation:pulse 1.5s infinite}
.st-review .task-icon{color:#f59e0b}.st-blocked .task-icon,.st-failed .task-icon{color:var(--red)}.st-open .task-icon{color:var(--yellow)}.st-claimed .task-icon{color:var(--purple)}
/* History */
.history-panel{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:.8em 1em;max-height:500px;overflow-y:auto}
.history-title{font-size:.9em;color:var(--blue);margin-bottom:.4em;font-weight:600}
.history-line{font-family:'JetBrains Mono',monospace;font-size:.75em;padding:.12em 0;border-bottom:1px solid rgba(42,42,78,.5);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hk-done{color:var(--green)}.hk-blocked{color:var(--red)}.hk-review{color:#f59e0b}.hk-coding{color:var(--purple)}.hk-info{color:var(--gray)}
.history-filters{display:flex;gap:.5em;margin-bottom:.5em;flex-wrap:wrap;align-items:center}
.history-filters input,.history-filters select{background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:.3em .6em;font-size:.8em}
/* Discussions */
.disc-list{display:grid;gap:.6em}.disc-item{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:.8em 1em;cursor:pointer;transition:all .2s}
.disc-item:hover{border-color:var(--green);background:var(--hover)}
.disc-expand{background:var(--card);border:1px solid var(--purple);border-radius:0 0 10px 10px;margin-top:-0.6em;margin-bottom:.6em;animation:fadeIn .2s}
.disc-topic{font-weight:600;color:var(--yellow);font-size:.95em}.disc-meta{font-size:.78em;color:var(--gray);margin-top:.2em}
.disc-status{display:inline-block;padding:.1em .5em;border-radius:10px;font-size:.72em;font-weight:600}
.disc-status.open{background:rgba(234,179,8,.15);color:var(--yellow)}.disc-status.resolved{background:rgba(74,222,128,.15);color:var(--green)}
.disc-viewer{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:1em;max-height:500px;overflow-y:auto;font-family:'JetBrains Mono',monospace;font-size:.82em;white-space:pre-wrap;line-height:1.5}
/* Agents table */
.agents-table{width:100%;border-collapse:collapse;font-size:.85em}
.agents-table th{text-align:left;color:var(--text-dim);font-weight:500;padding:.5em .8em;border-bottom:1px solid var(--border)}
.agents-table td{padding:.5em .8em;border-bottom:1px solid rgba(42,42,78,.3)}
.agents-table tr:hover td{background:var(--hover)}
/* Toggle switch */
.toggle{position:relative;display:inline-block;width:36px;height:20px;cursor:pointer}
.toggle input{display:none}.toggle .slider{position:absolute;inset:0;background:var(--border);border-radius:20px;transition:.3s}
.toggle input:checked+.slider{background:var(--green)}
.toggle .slider::before{content:'';position:absolute;width:16px;height:16px;left:2px;bottom:2px;background:#fff;border-radius:50%;transition:.3s}
.toggle input:checked+.slider::before{transform:translateX(16px)}
/* Settings */
.config-view{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:1em;max-height:500px;overflow-y:auto;font-family:'JetBrains Mono',monospace;font-size:.8em;white-space:pre-wrap;line-height:1.4;color:var(--text-dim)}
.sys-info{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:.6em;margin-top:1em}
.sys-card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:.6em .8em}
.sys-card label{font-size:.72em;color:var(--gray);text-transform:uppercase;display:block;margin-bottom:.2em}
.sys-card span{font-size:.9em;font-weight:500}
/* Modal */
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;z-index:100}
.modal-bg.show{display:flex}.modal{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:1.5em;width:min(90vw,500px);max-height:85vh;overflow-y:auto}
.modal h3{margin-bottom:1em;color:var(--green)}.modal label{display:block;font-size:.82em;color:var(--text-dim);margin-bottom:.3em;margin-top:.8em}
.modal input,.modal textarea,.modal select{width:100%;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:.5em .7em;font-size:.85em;font-family:inherit}
.modal textarea{min-height:80px;resize:vertical}
.modal .agent-checks{display:flex;flex-wrap:wrap;gap:.5em;margin-top:.3em}
.modal .agent-checks label{display:inline-flex;align-items:center;gap:.3em;font-size:.82em;cursor:pointer;color:var(--text)}
.modal-actions{display:flex;gap:.6em;margin-top:1.2em;justify-content:flex-end}
.btn{padding:.45em 1em;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--text);cursor:pointer;font-size:.82em;transition:all .2s}
.btn:hover{background:var(--hover)}.btn-primary{background:var(--green);color:#000;border-color:var(--green);font-weight:600}
.btn-primary:hover{background:#22c55e}.btn-danger{border-color:var(--red);color:var(--red)}.btn-danger:hover{background:rgba(239,68,68,.15)}
.empty{color:var(--gray);padding:2em;text-align:center}
/* Toast */
.toast{position:fixed;top:1em;right:1em;background:var(--card);border:1px solid var(--green);border-radius:8px;padding:.5em 1em;font-size:.82em;color:var(--green);opacity:0;transition:opacity .3s;z-index:200;pointer-events:none;max-width:350px}
.toast.err{border-color:var(--red);color:var(--red)}.toast.show{opacity:1}
/* Responsive */
@media(max-width:640px){.header{flex-direction:column;gap:.5em;padding:.8em}.summary{margin-left:0;width:100%}.tabs{overflow-x:auto;padding:0 .5em}
.tab{padding:.5em .8em;font-size:.8em}.tab-content{padding:.8em}.bar-wrap{width:80px}.task-name{max-width:40vw}.modal{width:95vw;padding:1em}}
</style></head><body>

<div class="header">
  <h1>Forge<span>Race</span></h1>
  <div class="summary" id="summary"></div>
  <div class="ts" id="ts">connecting...</div>
</div>
<div class="status-bar" id="statusBar"></div>
<div class="tabs" id="tabBar">
  <div class="tab active" data-tab="dashboard">Dashboard</div>
  <div class="tab" data-tab="discussions">Discussions</div>
  <div class="tab" data-tab="agents">Agents</div>
  <div class="tab" data-tab="history">History</div>
  <div class="tab" data-tab="settings">Settings</div>
</div>

<!-- Tab: Dashboard -->
<div class="tab-content active" id="tab-dashboard">
  <div class="controls" id="controls"></div>
  <div class="activity" id="activity"></div>
  <div id="root"></div>
  <div class="history-panel" id="miniHistory" style="margin-top:1em;display:none">
    <div class="history-title">Recent Events</div>
    <div id="miniHistoryBody"></div>
  </div>
</div>

<!-- Tab: Discussions -->
<div class="tab-content" id="tab-discussions">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1em">
    <h3 style="color:var(--yellow)">Discussions</h3>
    <span class="btn btn-primary" onclick="showModal('newDiscModal')">New Discussion</span>
  </div>
  <div id="discList" class="disc-list"></div>
  <div id="discViewer" style="margin-top:1em;display:none">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.5em">
      <span id="discViewTitle" style="font-weight:600;color:var(--yellow)"></span>
      <div style="display:flex;gap:.5em">
        <span class="btn" onclick="showModal('replyModal')">Reply</span>
        <span class="btn btn-primary" onclick="showModal('resolveModal')">Resolve</span>
        <span class="btn" onclick="hideDiscViewer()">Close</span>
      </div>
    </div>
    <div class="disc-viewer" id="discContent"></div>
  </div>
</div>

<!-- Tab: Agents -->
<div class="tab-content" id="tab-agents">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1em">
    <h3 style="color:var(--purple)">Agents</h3>
    <div style="display:flex;gap:.8em;align-items:center;font-size:.85em">
      <span style="color:var(--text-dim)">Mode:</span>
      <label style="cursor:pointer"><input type="radio" name="modeRadio" value="competitive" onchange="setMode('competitive')"> competitive</label>
      <label style="cursor:pointer"><input type="radio" name="modeRadio" value="distributed" onchange="setMode('distributed')"> distributed</label>
    </div>
  </div>
  <table class="agents-table">
    <thead><tr><th>Agent</th><th>Type</th><th>Protocol</th><th>Status</th><th>Last Activity</th><th>Enabled</th></tr></thead>
    <tbody id="agentsBody"></tbody>
  </table>
</div>

<!-- Tab: History -->
<div class="tab-content" id="tab-history">
  <h3 style="color:var(--blue);margin-bottom:.8em">Event Log</h3>
  <div class="history-filters">
    <input type="text" id="histSearch" placeholder="Search..." oninput="filterHistory()">
    <select id="histKind" onchange="filterHistory()"><option value="">All Types</option><option value="done">Done</option><option value="blocked">Blocked</option><option value="review">Review</option><option value="coding">Coding</option><option value="info">Info</option></select>
  </div>
  <div class="history-panel" id="fullHistory" style="max-height:calc(100vh - 280px)">
    <div id="fullHistoryBody"></div>
  </div>
</div>

<!-- Tab: Settings -->
<div class="tab-content" id="tab-settings">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.8em">
    <h3 style="color:var(--text-dim)">Configuration</h3>
    <span class="btn" id="rawToggleBtn" onclick="toggleRawConfig()">Show Raw</span>
  </div>
  <div id="parsedConfigView"></div>
  <div class="config-view" id="configView" style="display:none">Loading...</div>
  <h3 style="color:var(--text-dim);margin-top:1.5em;margin-bottom:.8em">System Info</h3>
  <div class="sys-info" id="sysInfo"></div>
</div>

<!-- Modal: New Discussion -->
<div class="modal-bg" id="newDiscModal">
  <div class="modal">
    <h3>New Discussion</h3>
    <label>Topic (slug)</label><input id="ndTopic" placeholder="my-feature">
    <label>Question</label><textarea id="ndQuestion" placeholder="What approach should we use for..."></textarea>
    <label>Invite agents</label>
    <div class="agent-checks" id="ndAgents"></div>
    <div class="modal-actions">
      <span class="btn" onclick="hideModal('newDiscModal')">Cancel</span>
      <span class="btn btn-primary" onclick="createDiscussion()">Create</span>
    </div>
  </div>
</div>

<!-- Modal: Reply -->
<div class="modal-bg" id="replyModal">
  <div class="modal">
    <h3>Agent Reply</h3>
    <label>Agent</label><select id="replyAgent"></select>
    <div class="modal-actions">
      <span class="btn" onclick="hideModal('replyModal')">Cancel</span>
      <span class="btn btn-primary" onclick="sendReply()">Send Reply</span>
    </div>
  </div>
</div>

<!-- Modal: Resolve -->
<div class="modal-bg" id="resolveModal">
  <div class="modal">
    <h3>Resolve Discussion</h3>
    <label>Resolution</label><textarea id="resolveText" placeholder="Final decision..."></textarea>
    <div class="modal-actions">
      <span class="btn" onclick="hideModal('resolveModal')">Cancel</span>
      <span class="btn btn-primary" onclick="resolveDiscussion()">Resolve</span>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const ICONS={done:"\u2713",skip:"\u2298",in_progress:"\u26a1",review:"\u23f3",blocked:"\u2717",open:"\u25cb",failed:"\u274c",claimed:"\u26a1"};
const collapsed=new Set();let _teamNames=[],_litellmPending=false,_currentDisc="",_allHistory=[],_agentNames=[],_lastSnapshot=null;
const $=id=>document.getElementById(id);
function esc(s){const d=document.createElement("div");d.textContent=s;return d.innerHTML}
function toast(msg,err){const t=$("toast");t.textContent=msg;t.className=err?"toast err show":"toast show";setTimeout(()=>t.className="toast",3500)}
function apiPost(u,b){return fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b||{})}).then(r=>r.json()).then(d=>{toast(d.msg||d.status||"OK",!d.ok);return d}).catch(e=>{toast("Error: "+e,true)})}
function apiGet(u){return fetch(u).then(r=>r.json()).catch(e=>{toast("Error: "+e,true)})}

/* Tabs */
document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(x=>x.classList.remove('active'));
  t.classList.add('active');$('tab-'+t.dataset.tab).classList.add('active');
  if(t.dataset.tab==='discussions')loadDiscussions();
  if(t.dataset.tab==='agents')loadAgents();
  if(t.dataset.tab==='history')loadFullHistory();
  if(t.dataset.tab==='settings')loadSettings();
}));

/* Modals */
function showModal(id){$(id).classList.add('show');if(id==='newDiscModal')populateAgentChecks();if(id==='replyModal')populateReplyAgent()}
function hideModal(id){$(id).classList.remove('show')}
document.querySelectorAll('.modal-bg').forEach(m=>m.addEventListener('click',e=>{if(e.target===m)m.classList.remove('show')}));

/* Dashboard */
function doRun(){const t=$("teamSel").value;t?apiPost("/api/run",{team:t}):toast("Select a team first",true)}
function doStop(){apiPost("/api/stop")}
function doRetry(){const t=$("teamSel").value;t?apiPost("/api/retry",{team:t}):toast("Select a team first",true)}
function doRunAll(){apiPost("/api/run/all")}
function toggleLitellm(on){if(_litellmPending)return;_litellmPending=true;fetch('/api/litellm/'+(on?'stop':'start')).then(r=>r.json()).then(()=>{if(on)_litellmPending=false}).catch(()=>_litellmPending=false)}
function toggleTeam(n){collapsed.has(n)?collapsed.delete(n):collapsed.add(n);const e=document.querySelector(`[data-team="${CSS.escape(n)}"]`);if(e)e.classList.toggle("collapsed")}

function renderControls(){let o=_teamNames.map(t=>`<option value="${esc(t)}">${esc(t)}</option>`).join('');
$("controls").innerHTML=`<select id="teamSel"><option value="">-- team --</option>${o}</select>`+
`<span class="pill action" onclick="doRun()">Run Team</span>`+
`<span class="pill action" onclick="doRunAll()">Quick Run All</span>`+
`<span class="pill danger" onclick="doStop()">Stop All</span>`+
`<span class="pill action" onclick="doRetry()">Retry Failed</span>`+
`<span class="pill action" onclick="showModal('newDiscModal')">New Discussion</span>`}

function renderSummary(d){const p=d.total_all?Math.round(d.total_done/d.total_all*100):0;
const agentTip=d.active_agents&&d.active_agents.length?d.active_agents.map(a=>a.agent+' \u2192 '+a.task).join('\n'):'No active processes';
let h=`<span class="pill">${d.total_done}/${d.total_all} (${p}%)</span><span class="pill proc-pill" onclick="toggleProcTooltip(this)" title="${esc(agentTip)}" style="position:relative">${d.processes} proc</span>`;
if(_litellmPending&&!d.litellm)h+=`<span class="pill starting">starting...</span>`;
else{_litellmPending=false;h+=`<span class="pill ${d.litellm?'on':'off'}" onclick="toggleLitellm(${d.litellm})">LiteLLM ${d.litellm?'ON':'OFF'}</span>`}
$("summary").innerHTML=h}
function toggleProcTooltip(el){const existing=el.querySelector('.proc-tip');if(existing){existing.remove();return}
const d=_lastSnapshot;if(!d||!d.active_agents||!d.active_agents.length)return;
const tip=document.createElement('div');tip.className='proc-tip';
tip.style.cssText='position:absolute;top:110%;left:0;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:.5em .7em;font-size:.8em;white-space:nowrap;z-index:50;min-width:180px';
tip.innerHTML=d.active_agents.map(a=>`<div>${esc(a.agent)} \u2192 ${esc(a.task)} <span style="color:var(--gray)">${a.since}</span></div>`).join('');
el.appendChild(tip);setTimeout(()=>{if(tip.parentNode)tip.remove()},5000)}

function renderStatusBar(d){
$("statusBar").innerHTML=`<span>Mode: <b style="color:var(--purple)">${d.mode||'competitive'}</b></span>`+
`<span>Active agents: <b>${d.agent_count||0}</b></span>`+
`<span>Updated: ${d.timestamp}</span>`}

function renderActivity(a){if(!a||!a.length){$("activity").innerHTML="";return}
$("activity").innerHTML='<div class="activity-title">Agent Activity</div>'+a.map(x=>`<span class="agent-card"><span class="dot"></span><b>${x.agent}</b> &rarr; ${x.task}</span>`).join('')}

function renderTeams(teams){const r=$("root");if(!Object.keys(teams).length){r.innerHTML='<div class="empty">No tasks found.</div>';return}let h="";
for(const n of Object.keys(teams)){const t=teams[n],p=t.total?Math.round((t.done+t.skip)/t.total*100):0,c=collapsed.has(n);
h+=`<div class="team${c?' collapsed':''}" data-team="${n}"><div class="team-header" onclick="toggleTeam('${n.replace(/'/g,"\\'")}')"><span class="team-chevron">&#9660;</span><span class="team-name">${esc(n)}</span><span class="team-stats">${t.done+t.skip}/${t.total}</span><div class="bar-wrap"><div class="bar-fill" style="width:${p}%"></div></div></div><div class="team-body">`;
for(const tk of t.tasks){h+=`<div class="task st-${tk.status}"><span class="task-icon">${ICONS[tk.status]||"\u25cb"}</span><span class="task-id mono">${tk.id}</span><span class="task-name">${esc(tk.name)}</span>${tk.agent?`<span class="task-agent">${esc(tk.agent)}</span>`:''}</div>`}
h+=`</div></div>`}r.innerHTML=h}

function renderMiniHistory(ev){const p=$("miniHistory"),b=$("miniHistoryBody");if(!ev||!ev.length){p.style.display="none";return}
p.style.display="block";b.innerHTML=ev.slice(0,10).map(e=>`<div class="history-line hk-${e.kind}">${esc(e.text)}</div>`).join('')}

function render(d){$("ts").textContent="";
if(JSON.stringify(_teamNames)!==JSON.stringify(d.team_names||[])){_teamNames=d.team_names||[];renderControls()}
renderSummary(d);renderStatusBar(d);renderActivity(d.active_agents);renderTeams(d.teams);renderMiniHistory(d.history);
_allHistory=d.history||[];_lastSnapshot=d}

/* Discussions */
function loadDiscussions(){apiGet("/api/discuss/list").then(d=>{if(!d)return;
const list=d.discussions||[];
if(!list.length){$("discList").innerHTML='<div class="empty">No discussions yet.</div>';return}
$("discList").innerHTML=list.map(x=>`<div class="disc-item" data-topic="${esc(x.topic)}" onclick="viewDiscussion('${esc(x.topic)}')">`+
`<div style="display:flex;justify-content:space-between;align-items:center"><span class="disc-topic">${esc(x.display_name||x.topic)}</span><div><span style="font-size:.75em;color:var(--gray);margin-right:.5em">${x.msg_count||0} msg</span><span class="disc-status ${x.status}">${x.status}</span></div></div>`+
`<div class="disc-meta">${x.participants} participants: ${(x.participant_names||[]).join(', ')}</div></div>`).join('')})}

function formatDiscContent(raw){
if(!raw)return '(empty)';
let h=esc(raw);
// Section headers: ## @agent (date)
h=h.replace(/^(## @\S+.*)/gm,'<div style="color:var(--purple);font-weight:700;margin-top:1em;border-bottom:1px solid var(--border);padding-bottom:.3em">$1</div>');
// РЕЗОЛЮЦИЯ line
h=h.replace(/(РЕЗОЛЮЦИЯ:.*)/g,'<span style="color:var(--green);font-weight:700">$1</span>');
// CONFIDENCE line
h=h.replace(/(CONFIDENCE:\s*\d+%)/g,'<span style="color:var(--yellow);font-weight:600">$1</span>');
// --- RESOLVED ---
h=h.replace(/(--- RESOLVED ---)/g,'<div style="color:var(--green);text-align:center;margin:1em 0;font-weight:700">$1</div>');
return h}

function viewDiscussion(topic){
  _currentDisc=topic;
  // Close any open viewer
  document.querySelectorAll('.disc-expand').forEach(e=>e.remove());
  // Find the clicked card
  const items=document.querySelectorAll('.disc-item');
  let target=null;
  items.forEach(el=>{if(el.getAttribute('data-topic')===topic)target=el});
  if(!target)return;
  // Create expand panel after the card
  const panel=document.createElement('div');
  panel.className='disc-expand';
  panel.innerHTML='<div style="padding:1em;color:var(--gray)">Loading...</div>';
  target.after(panel);
  apiGet("/api/discuss/show/"+encodeURIComponent(topic)).then(d=>{if(!d)return;
    panel.innerHTML=`<div style="display:flex;justify-content:space-between;align-items:center;padding:.8em 1em;border-bottom:1px solid var(--border)"><b style="color:var(--purple)">${topic.replace(/-/g,' ')}</b><div><button class="pill action" onclick="showModal('replyModal')">Reply</button> <button class="pill" style="border-color:var(--green);color:var(--green)" onclick="showModal('resolveModal')">Resolve</button> <button class="pill" onclick="this.closest('.disc-expand').remove()">Close</button></div></div><div style="padding:1em;max-height:60vh;overflow-y:auto;white-space:pre-wrap;font-size:.85em;line-height:1.5">${formatDiscContent(d.content)}</div>`;
  })
}
function hideDiscViewer(){document.querySelectorAll('.disc-expand').forEach(e=>e.remove());_currentDisc=""}

function populateAgentChecks(){apiGet("/api/agents").then(d=>{if(!d)return;
_agentNames=(d.agents||[]).filter(a=>a.enabled).map(a=>a.name);
$("ndAgents").innerHTML=_agentNames.map(n=>`<label><input type="checkbox" value="${n}" checked> ${n}</label>`).join('')})}

function populateReplyAgent(){apiGet("/api/agents").then(d=>{if(!d)return;
const agents=(d.agents||[]).filter(a=>a.enabled);
$("replyAgent").innerHTML=agents.map(a=>`<option value="${a.name}">${a.name}</option>`).join('')})}

function createDiscussion(){const topic=$("ndTopic").value.trim(),q=$("ndQuestion").value.trim();
if(!topic||!q){toast("Fill topic and question",true);return}
const agents=Array.from(document.querySelectorAll('#ndAgents input:checked')).map(i=>i.value);
apiPost("/api/discuss/new",{topic,question:q,agents}).then(d=>{if(d&&d.ok){hideModal('newDiscModal');$("ndTopic").value='';$("ndQuestion").value='';loadDiscussions()}})}

function sendReply(){if(!_currentDisc){toast("No discussion selected",true);return}
const agent=$("replyAgent").value;
apiPost("/api/discuss/reply",{topic:_currentDisc,agent}).then(d=>{if(d&&d.ok){hideModal('replyModal');setTimeout(()=>viewDiscussion(_currentDisc),2000)}})}

function resolveDiscussion(){if(!_currentDisc){toast("No discussion selected",true);return}
const text=$("resolveText").value.trim();if(!text){toast("Enter resolution text",true);return}
apiPost("/api/discuss/resolve",{topic:_currentDisc,resolution:text}).then(d=>{if(d&&d.ok){hideModal('resolveModal');$("resolveText").value='';viewDiscussion(_currentDisc);loadDiscussions()}})}

/* Agents */
function loadAgents(){apiGet("/api/agents").then(d=>{if(!d)return;
const agents=d.agents||[];
$("agentsBody").innerHTML=agents.map(a=>{
const stColor=a.status==='active'?'var(--green)':a.status==='idle'?'var(--text-dim)':'var(--red)';
return `<tr><td><b>${esc(a.name)}</b></td><td>${a.type}</td><td><span class="mono">${a.protocol}</span></td>`+
`<td><span style="color:${stColor}">${a.status}</span></td><td class="mono">${a.last_activity||'-'}</td>`+
`<td><label class="toggle"><input type="checkbox" ${a.enabled?'checked':''} onchange="toggleAgent('${esc(a.name)}',this.checked)"><span class="slider"></span></label></td></tr>`}).join('');
// Set mode radio
const modeRadios=document.querySelectorAll('input[name="modeRadio"]');
modeRadios.forEach(r=>{r.checked=r.value===(d.mode||'competitive')})})}

function toggleAgent(name,enabled){apiPost("/api/agents/toggle",{name,enabled})}
function setMode(mode){apiPost("/api/mode",{mode})}

/* History */
function loadFullHistory(){apiGet("/api/history").then(d=>{if(!d)return;_allHistory=d.events||[];filterHistory()})}
function filterHistory(){const search=($("histSearch").value||'').toLowerCase(),kind=$("histKind").value;
const filtered=_allHistory.filter(e=>{if(kind&&e.kind!==kind)return false;if(search&&!e.text.toLowerCase().includes(search))return false;return true});
$("fullHistoryBody").innerHTML=filtered.length?filtered.map(e=>`<div class="history-line hk-${e.kind}">${esc(e.text)}</div>`).join(''):'<div class="empty">No matching events.</div>'}

/* Settings */
let _rawConfigVisible=false;
function toggleRawConfig(){_rawConfigVisible=!_rawConfigVisible;
$("configView").style.display=_rawConfigVisible?'block':'none';
$("parsedConfigView").style.display=_rawConfigVisible?'none':'block';
$("rawToggleBtn").textContent=_rawConfigVisible?'Show Parsed':'Show Raw'}

function renderParsedConfig(p){if(!p){$("parsedConfigView").innerHTML='';return}
let h='';
// Project section
const pr=p.project||{};
h+='<div style="margin-bottom:1em"><div style="color:var(--purple);font-weight:600;margin-bottom:.4em;font-size:.95em">Project</div>';
h+='<div class="sys-info" style="margin:0">';
for(const [k,v] of [['Name',pr.name],['Root',pr.root],['Mode',pr.mode],['Dev Branch',pr.dev_branch]]){
h+=`<div class="sys-card"><label>${k}</label><span>${esc(String(v||'-'))}</span></div>`}
h+='</div></div>';
// Agents section
const ag=p.agents||[];
if(ag.length){h+='<div style="margin-bottom:1em"><div style="color:var(--purple);font-weight:600;margin-bottom:.4em;font-size:.95em">Agents</div>';
h+='<table class="agents-table"><thead><tr><th>Name</th><th>Command</th><th>Protocol</th><th>Enabled</th><th>Model</th></tr></thead><tbody>';
for(const a of ag){h+=`<tr><td><b>${esc(a.name)}</b></td><td class="mono">${esc(a.command)}</td><td>${esc(a.protocol)}</td>`+
`<td style="color:${a.enabled?'var(--green)':'var(--red)'}">` +(a.enabled?'yes':'no')+`</td><td class="mono">${esc(a.model||'-')}</td></tr>`}
h+='</tbody></table></div>'}
// Limits section
const lm=p.limits||{};
if(Object.keys(lm).length){h+='<div style="margin-bottom:1em"><div style="color:var(--purple);font-weight:600;margin-bottom:.4em;font-size:.95em">Limits</div>';
h+='<div class="sys-info" style="margin:0">';
for(const [k,v] of Object.entries(lm)){h+=`<div class="sys-card"><label>${esc(k)}</label><span>${esc(String(v))}</span></div>`}
h+='</div></div>'}
// Frames section
const fr=p.frames||[];
if(fr.length){h+='<div style="margin-bottom:1em"><div style="color:var(--purple);font-weight:600;margin-bottom:.4em;font-size:.95em">Frames</div>';
h+='<div style="display:flex;flex-wrap:wrap;gap:.5em">';
for(const f of fr){h+=`<span class="pill" title="${esc(f.description)}">${esc(f.name)}${f.description?' — '+esc(f.description):''}</span>`}
h+='</div></div>'}
// Build section
const bl=p.build||{};
if(bl.commands&&bl.commands.length||bl.check_command||bl.lint_fix&&bl.lint_fix.length){
h+='<div style="margin-bottom:1em"><div style="color:var(--purple);font-weight:600;margin-bottom:.4em;font-size:.95em">Build</div>';
h+='<div class="sys-info" style="margin:0">';
if(bl.commands&&bl.commands.length)h+=`<div class="sys-card"><label>Commands</label><span class="mono">${esc(JSON.stringify(bl.commands))}</span></div>`;
if(bl.lint_fix&&bl.lint_fix.length)h+=`<div class="sys-card"><label>Lint Fix</label><span class="mono">${esc(JSON.stringify(bl.lint_fix))}</span></div>`;
if(bl.check_command)h+=`<div class="sys-card"><label>Check Command</label><span class="mono">${esc(bl.check_command)}</span></div>`;
h+='</div></div>'}
$("parsedConfigView").innerHTML=h}

function loadSettings(){apiGet("/api/config").then(d=>{if(!d)return;
$("configView").textContent=d.config||"";
renderParsedConfig(d.parsed||null);
const s=d.system||{};
$("sysInfo").innerHTML=`<div class="sys-card"><label>Python</label><span>${esc(s.python||'?')}</span></div>`+
`<div class="sys-card"><label>Git Branch</label><span>${esc(s.branch||'?')}</span></div>`+
`<div class="sys-card"><label>Repo Root</label><span>${esc(s.root||'?')}</span></div>`+
`<div class="sys-card"><label>Mode</label><span>${esc(s.mode||'?')}</span></div>`+
`<div class="sys-card"><label>Frames</label><span>${(s.frames||[]).join(', ')||'none'}</span></div>`})}

/* SSE */
window.addEventListener('load',()=>{const es=new EventSource("/events");
es.onmessage=e=>{try{render(JSON.parse(e.data))}catch(err){console.error(err)}};
es.onerror=()=>$("ts").textContent="Connection lost, retrying..."});
</script></body></html>"""


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        try:
            self._handle_get()
        except BrokenPipeError:
            pass

    def do_POST(self):
        try:
            self._handle_post()
        except BrokenPipeError:
            pass

    def _read_body(self) -> dict:
        length = int(self.headers.get('Content-Length', 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    def _handle_get(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_HTML.encode("utf-8"))
        elif self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        elif self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                while True:
                    data = json.dumps(_build_snapshot(), ensure_ascii=False)
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(5)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        elif self.path == "/api/litellm/start":
            self._api_litellm_start()
        elif self.path == "/api/litellm/stop":
            self._api_litellm_stop()
        elif self.path == "/api/teams":
            self._json_response({"ok": True, "teams": _list_teams()})
        elif self.path == "/api/history":
            self._json_response({"ok": True, "events": _read_history(100)})
        elif self.path == "/api/discuss/list":
            self._json_response({"ok": True, "discussions": _list_discussions()})
        elif self.path.startswith("/api/discuss/show/"):
            topic = self.path.split("/api/discuss/show/", 1)[1]
            # URL-decode topic
            from urllib.parse import unquote
            topic = unquote(topic)
            content = _show_discussion(topic)
            self._json_response({"ok": bool(content), "content": content})
        elif self.path == "/api/agents":
            self._json_response({"ok": True, "agents": _list_agents_info(), "mode": cfg.mode})
        elif self.path == "/api/config":
            self._json_response({"ok": True, "config": _read_config_file(),
                                 "parsed": _get_parsed_config(), "system": _get_system_info()})
        else:
            self.send_error(404)

    def _handle_post(self):
        body = self._read_body()
        if self.path == "/api/run":
            self._api_run(body)
        elif self.path == "/api/stop":
            self._api_stop()
        elif self.path == "/api/retry":
            self._api_retry(body)
        elif self.path == "/api/run/all":
            self._api_run_all()
        elif self.path == "/api/discuss/new":
            self._api_discuss_new(body)
        elif self.path == "/api/discuss/reply":
            self._api_discuss_reply(body)
        elif self.path == "/api/discuss/resolve":
            self._api_discuss_resolve(body)
        elif self.path == "/api/agents/toggle":
            self._api_agents_toggle(body)
        elif self.path == "/api/mode":
            self._api_set_mode(body)
        else:
            self.send_error(404)

    # -- API handlers --

    def _api_run(self, body: dict):
        team = body.get("team", "")
        if not team:
            self._json_response({"ok": False, "msg": "No team specified"})
            return
        fr_py = Path(__file__).resolve().parent.parent / "forgerace.py"
        cmd = ["python3", str(fr_py), "run", "--auto", "--team", team]
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(cmd, cwd=str(cfg.root_dir),
            stdout=open(cfg.log_dir / "web_run.log", "a"),
            stderr=subprocess.STDOUT, start_new_session=True)
        self._json_response({"ok": True, "msg": f"Started run for {team}"})

    def _api_run_all(self):
        fr_py = Path(__file__).resolve().parent.parent / "forgerace.py"
        cmd = ["python3", str(fr_py), "run", "--auto"]
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(cmd, cwd=str(cfg.root_dir),
            stdout=open(cfg.log_dir / "web_run.log", "a"),
            stderr=subprocess.STDOUT, start_new_session=True)
        self._json_response({"ok": True, "msg": "Started run for all ready tasks"})

    def _api_stop(self):
        subprocess.run(["pkill", "-f", "forgerace.py run"], capture_output=True)
        self._json_response({"ok": True, "msg": "Stopped all forgerace processes"})

    def _api_retry(self, body: dict):
        team = body.get("team", "")
        if not team:
            self._json_response({"ok": False, "msg": "No team specified"})
            return
        try:
            tasks = parse_tasks()
            count = 0
            for t in tasks:
                if (t.discussion or "") != team:
                    continue
                status_base = t.status.split(":")[0]
                if status_base in ("blocked", "skip", "failed"):
                    update_task_status(t.id, "open")
                    count += 1
            self._json_response({"ok": True, "msg": f"Reset {count} tasks to open"})
        except Exception as e:
            self._json_response({"ok": False, "msg": str(e)})

    def _api_discuss_new(self, body: dict):
        topic = body.get("topic", "").strip()
        question = body.get("question", "").strip()
        agents = body.get("agents", [])
        if not topic or not question:
            self._json_response({"ok": False, "msg": "Topic and question required"})
            return
        try:
            from .discuss import discuss_create, discuss_reply
            discuss_create(topic, question, author="techlead")
            # Fire agent replies in background
            for agent in agents:
                if agent in cfg.agents and cfg.agents[agent].enabled:
                    threading.Thread(target=discuss_reply, args=(topic, agent),
                                     daemon=True).start()
            self._json_response({"ok": True, "msg": f"Discussion '{topic}' created"})
        except Exception as e:
            self._json_response({"ok": False, "msg": str(e)})

    def _api_discuss_reply(self, body: dict):
        topic = body.get("topic", "").strip()
        agent = body.get("agent", "").strip()
        if not topic or not agent:
            self._json_response({"ok": False, "msg": "Topic and agent required"})
            return
        try:
            from .discuss import discuss_reply
            threading.Thread(target=discuss_reply, args=(topic, agent),
                             daemon=True).start()
            self._json_response({"ok": True, "msg": f"Reply from {agent} started"})
        except Exception as e:
            self._json_response({"ok": False, "msg": str(e)})

    def _api_discuss_resolve(self, body: dict):
        topic = body.get("topic", "").strip()
        resolution = body.get("resolution", "").strip()
        if not topic or not resolution:
            self._json_response({"ok": False, "msg": "Topic and resolution required"})
            return
        try:
            filepath = cfg.discuss_dir / f"{topic}.md"
            if not filepath.exists():
                self._json_response({"ok": False, "msg": f"Discussion '{topic}' not found"})
                return
            from datetime import datetime
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(f"\n## @techlead ({now})\n\n\u0420\u0415\u0417\u041e\u041b\u042e\u0426\u0418\u042f: {resolution}\n\n--- RESOLVED ---\n")
            self._json_response({"ok": True, "msg": f"Discussion '{topic}' resolved"})
        except Exception as e:
            self._json_response({"ok": False, "msg": str(e)})

    def _api_agents_toggle(self, body: dict):
        name = body.get("name", "")
        enabled = body.get("enabled", True)
        if not name or name not in cfg.agents:
            self._json_response({"ok": False, "msg": f"Unknown agent: {name}"})
            return
        cfg.agents[name].enabled = bool(enabled)
        # Try to persist to TOML
        toml_path = cfg.root_dir / "forgerace.toml"
        if toml_path.exists():
            try:
                content = toml_path.read_text(encoding="utf-8")
                # Find [agents.NAME] section and toggle enabled
                pattern = rf'(\[agents\.{re.escape(name)}\][^\[]*?)enabled\s*=\s*(true|false)'
                val = "true" if enabled else "false"
                new_content = re.sub(pattern, rf'\1enabled = {val}', content, flags=re.DOTALL)
                if new_content != content:
                    toml_path.write_text(new_content, encoding="utf-8")
            except Exception:
                pass  # In-memory toggle is enough
        self._json_response({"ok": True, "msg": f"{name} {'enabled' if enabled else 'disabled'}"})

    def _api_set_mode(self, body: dict):
        mode = body.get("mode", "")
        if mode not in ("competitive", "distributed"):
            self._json_response({"ok": False, "msg": "Invalid mode"})
            return
        cfg.mode = mode
        # Try to persist to TOML
        toml_path = cfg.root_dir / "forgerace.toml"
        if toml_path.exists():
            try:
                content = toml_path.read_text(encoding="utf-8")
                new_content = re.sub(r'mode\s*=\s*"[^"]*"', f'mode = "{mode}"', content)
                if new_content != content:
                    toml_path.write_text(new_content, encoding="utf-8")
            except Exception:
                pass
        self._json_response({"ok": True, "msg": f"Mode set to {mode}"})

    def _api_litellm_start(self):
        litellm_bin = Path.home() / ".local/share/pipx/venvs/litellm/bin/litellm"
        config_file = cfg.root_dir / "litellm_config.yaml"
        if not litellm_bin.exists() or not config_file.exists():
            self._json_response({"ok": False, "msg": "LiteLLM not installed"})
            return
        clean_env = {k: v for k, v in os.environ.items()
                     if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
        clean_env["no_proxy"] = "127.0.0.1,localhost"
        clean_env["NO_PROXY"] = "127.0.0.1,localhost"
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            [str(litellm_bin), "--config", str(config_file), "--port", "4000", "--host", "127.0.0.1"],
            stdout=open(cfg.log_dir / "litellm.log", "w"),
            stderr=subprocess.STDOUT, env=clean_env)
        self._json_response({"ok": True, "status": "starting (check in 10s)"})

    def _api_litellm_stop(self):
        subprocess.run(["pkill", "-f", "litellm.*--port.*4000"], capture_output=True)
        time.sleep(1)
        self._json_response({"ok": True, "status": "stopped"})

    def _json_response(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_dashboard(host: str = "localhost", port: int = 8080):
    """Start the dashboard HTTP server (blocking)."""
    class _ThreadedServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True
    server = _ThreadedServer((host, port), _Handler)
    print(f"ForgeRace Dashboard: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()
