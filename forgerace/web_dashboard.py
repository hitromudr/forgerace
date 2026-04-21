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


def _list_disc_topics() -> list[str]:
    """All discussion topic names (open + archived)."""
    topics = []
    ddir = cfg.discuss_dir
    if ddir.exists():
        for f in ddir.glob("*.md"):
            if not f.name.endswith("-tasks.md"):
                topics.append(f.stem)
        adir = ddir / "archive"
        if adir.exists():
            for f in adir.glob("*.md"):
                if not f.name.endswith("-tasks.md"):
                    topics.append(f.stem)
    return topics


_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


def _tail_run_log(n: int = 30) -> list[str]:
    """Read last N lines of web_run.log, strip ANSI codes."""
    logfile = cfg.log_dir / "web_run.log"
    if not logfile.exists():
        return []
    try:
        text = logfile.read_text(encoding="utf-8", errors="replace")
        lines = text.strip().split("\n")
        result = []
        for line in lines[-n:]:
            clean = _ANSI_RE.sub("", line).strip()
            if clean:
                result.append(clean)
        return result
    except OSError:
        return []

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


_snapshot_cache: dict = {}
_snapshot_ts: float = 0


def _build_snapshot() -> dict:
    global _snapshot_cache, _snapshot_ts
    now = time.time()
    if now - _snapshot_ts < 3 and _snapshot_cache:
        return _snapshot_cache
    _snapshot_ts = now
    _snapshot_cache = _build_snapshot_impl()
    return _snapshot_cache


def _build_snapshot_impl() -> dict:
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

    # Add open discussions that don't have tasks yet (planning phase)
    for topic in _list_disc_topics():
        if topic not in teams:
            fp = cfg.discuss_dir / f"{topic}.md"
            if fp.exists():
                try:
                    text = fp.read_text(encoding="utf-8", errors="replace")
                    participants = sorted(set(re.findall(r"^## @(\S+)", text, re.MULTILINE)))
                    msg_count = len(re.findall(r"^## @", text, re.MULTILINE))
                except Exception:
                    participants, msg_count = [], 0
                teams[topic] = {"done": 0, "total": 0, "skip": 0, "tasks": [],
                                "status": "planning", "participants": participants,
                                "msg_count": msg_count}

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
        "history": _read_history(50),
        "run_log": _tail_run_log(30),
        "mode": cfg.mode,
        "agent_count": sum(1 for a in cfg.agents.values() if a.enabled),
        "disc_topics": _list_disc_topics(),
    }


def _parse_disc_file(f: Path, archived: bool = False) -> dict | None:
    """Parse a single discussion file into a dict."""
    if f.name.endswith("-tasks.md"):
        return None
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
        resolved = "РЕЗОЛЮЦИЯ" in text or "--- RESOLVED ---" in text
        # Check for reopen after last resolve
        if resolved:
            reopened = "ДИСКУССИЯ ПЕРЕОТКРЫТА" in text
            if reopened:
                last_resolve = max(text.rfind("РЕЗОЛЮЦИЯ"), text.rfind("--- RESOLVED ---"))
                last_reopen = text.rfind("ДИСКУССИЯ ПЕРЕОТКРЫТА")
                resolved = last_resolve > last_reopen
        status = "archived" if archived else ("resolved" if resolved else "open")
        participants = set(re.findall(r"^## @(\S+)", text, re.MULTILINE))
        msg_count = len(re.findall(r"^## @", text, re.MULTILINE))
        display_name = f.stem.replace("-", " ").capitalize()
        return {"topic": f.stem, "display_name": display_name,
                "status": status, "msg_count": msg_count,
                "participants": len(participants),
                "participant_names": sorted(participants)}
    except OSError:
        return None


def _list_discussions() -> list[dict]:
    result = []
    ddir = cfg.discuss_dir
    if not ddir.exists():
        return result
    # Build task stats by discussion topic
    task_stats = {}
    try:
        for t in parse_tasks():
            d = t.discussion or ""
            if d and d != "\u2014":
                s = task_stats.setdefault(d, {"done": 0, "total": 0})
                s["total"] += 1
                st = (t.status or "").split(":")[0]
                if st in ("done", "skip"):
                    s["done"] += 1
    except Exception:
        pass
    # Active discussions
    for f in sorted(ddir.glob("*.md")):
        entry = _parse_disc_file(f)
        if entry:
            ts = task_stats.get(entry["topic"], {})
            entry["task_done"] = ts.get("done", 0)
            entry["task_total"] = ts.get("total", 0)
            result.append(entry)
    # Archived discussions
    archive_dir = ddir / "archive"
    if archive_dir.exists():
        for f in sorted(archive_dir.glob("*.md")):
            entry = _parse_disc_file(f, archived=True)
            if entry:
                ts = task_stats.get(entry["topic"], {})
                entry["task_done"] = ts.get("done", 0)
                entry["task_total"] = ts.get("total", 0)
                result.append(entry)
    return result


def _archive_discussion(topic: str):
    """Move resolved discussion + tasks file to archive/."""
    ddir = cfg.discuss_dir
    archive = ddir / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    for suffix in (f"{topic}.md", f"{topic}-tasks.md"):
        src = ddir / suffix
        if src.exists():
            src.rename(archive / suffix)


def _unarchive_discussion(topic: str):
    """Move discussion back from archive/ to main dir."""
    ddir = cfg.discuss_dir
    archive = ddir / "archive"
    for suffix in (f"{topic}.md", f"{topic}-tasks.md"):
        src = archive / suffix
        if src.exists():
            src.rename(ddir / suffix)


def _show_discussion(topic: str) -> str:
    fp = cfg.discuss_dir / f"{topic}.md"
    if not fp.exists():
        fp = cfg.discuss_dir / "archive" / f"{topic}.md"
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
        "name": cfg.project_name or cfg.root_dir.name,
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
.team-done{opacity:.5}.team-done:hover{opacity:.8}
.team-planning{border-style:dashed;border-color:var(--yellow)}.team-planning .team-name{color:var(--yellow)}
.team:hover{border-color:var(--green)}.team-header{display:flex;align-items:center;gap:.8em;padding:.7em 1em;cursor:pointer;user-select:none}
.team-header:hover{background:var(--hover)}.team-name{font-weight:600;color:var(--yellow);flex:1}.disc-link{font-size:.85em;opacity:.5;cursor:pointer;margin-left:.5em;transition:opacity .2s}.disc-link:hover{opacity:1}.team-stats{font-size:.82em;color:var(--gray)}
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
.disc-resolved{opacity:.6}.disc-resolved:hover{opacity:1}.disc-resolved .disc-topic{color:var(--text-dim)}
.disc-highlight{border-color:var(--green)!important;box-shadow:0 0 8px rgba(74,222,128,.3)}
.disc-expand{background:var(--card);border:1px solid var(--purple);border-radius:0 0 10px 10px;margin-top:-0.6em;margin-bottom:.6em;animation:fadeIn .2s}
.ac-item{padding:.4em .8em;cursor:pointer;border-bottom:1px solid rgba(42,42,78,.3)}.ac-item:hover,.ac-sel{background:var(--hover)}
.disc-topic{font-weight:600;color:var(--yellow);font-size:.95em}.disc-meta{font-size:.78em;color:var(--gray);margin-top:.2em}
.disc-status{display:inline-block;padding:.1em .5em;border-radius:10px;font-size:.72em;font-weight:600}
.disc-status.open{background:rgba(234,179,8,.15);color:var(--yellow)}.disc-status.resolved{background:rgba(74,222,128,.15);color:var(--green)}.disc-status.archived{background:rgba(107,114,128,.15);color:var(--gray)}
.disc-viewer{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:1em;max-height:500px;overflow-y:auto;font-family:'JetBrains Mono',monospace;font-size:.82em;white-space:pre-wrap;line-height:1.5}
/* Agents table */
.agents-table{width:100%;border-collapse:collapse;font-size:.82em}
.agents-table th{text-align:left;color:var(--text-dim);font-weight:500;padding:.4em .7em;border-bottom:1px solid var(--border)}
.agents-table td{padding:.35em .7em;border-bottom:1px solid rgba(42,42,78,.2)}
.agents-table tr:nth-child(even) td{background:rgba(42,42,78,.15)}
.agents-table tr:hover td{background:var(--hover)}
.agents-table tr.agent-off td{opacity:.45}
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
.toast{position:fixed;top:5em;right:1em;background:var(--card);border:1px solid var(--green);border-radius:8px;padding:.5em 1em;font-size:.82em;color:var(--green);opacity:0;transition:opacity .3s;z-index:200;pointer-events:none;max-width:350px}
.toast.show{opacity:1}
/* Responsive */
@media(max-width:640px){.header{flex-direction:column;gap:.5em;padding:.8em}.summary{margin-left:0;width:100%}.tabs{overflow-x:auto;padding:0 .5em}
.tab{padding:.5em .8em;font-size:.8em}.tab-content{padding:.8em}.bar-wrap{width:80px}.task-name{max-width:40vw}.modal{width:95vw;padding:1em}}
</style></head><body>

<div class="header">
  <h1>Forge<span>Race</span></h1>
  <div class="summary" id="summary"></div>
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
  <div class="history-panel" id="runLogPanel" style="margin-top:1em;display:none">
    <div style="display:flex;justify-content:space-between;align-items:center"><span class="history-title" style="color:var(--green)">Live Agent Log</span><span id="runLogStatus" style="font-size:.75em;color:var(--gray)"></span></div>
    <div id="runLogBody" style="max-height:300px;overflow-y:auto;font-family:monospace;font-size:.78em;line-height:1.4"></div>
  </div>
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
  <div style="text-align:center;margin-top:.8em"><span class="btn" id="loadMoreBtn" onclick="loadMoreHistory()">Load More</span></div>
</div>

<!-- Tab: Settings -->
<div class="tab-content" id="tab-settings">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.8em">
    <h3 style="color:var(--text-dim)">Configuration</h3>
    <span class="btn" id="rawToggleBtn" onclick="toggleRawConfig()">Show Raw</span>
  </div>
  <div id="parsedConfigView"></div>
  <div id="rawConfigWrap" style="display:none">
    <div class="config-view" id="configView"></div>
    <div id="configEditWrap" style="display:none">
      <textarea id="configEditor" spellcheck="false" style="width:100%;min-height:500px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:10px;padding:1em;font-family:'JetBrains Mono',monospace;font-size:.8em;line-height:1.4;resize:vertical;outline:none"></textarea>
    </div>
    <div style="margin-top:.6em;display:flex;gap:.5em;justify-content:flex-end">
      <button class="pill action" id="editConfigBtn" onclick="toggleEditConfig()">Edit</button>
      <button class="pill" id="saveConfigBtn" style="display:none;border-color:var(--green);color:var(--green)" onclick="saveConfig()">Save</button>
      <button class="pill" id="cancelConfigBtn" style="display:none;border-color:var(--red);color:var(--red)" onclick="cancelEditConfig()">Cancel</button>
    </div>
  </div>
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
function toast(msg,color){const t=$("toast");t.textContent=msg;t.style.borderColor=color||'var(--green)';t.style.color=color||'var(--green)';t.className="toast show";setTimeout(()=>t.className="toast",3500)}
function apiPost(u,b){return fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b||{})}).then(r=>r.json()).then(d=>{toast(d.msg||d.status||"OK",d.ok?null:'var(--red)');return d}).catch(e=>{toast("Error: "+e,'var(--red)')})}
function apiGet(u){return fetch(u).then(r=>r.json()).catch(e=>{toast("Error: "+e,'var(--red)')})}

/* Tabs */
function activateTab(name){
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(x=>x.classList.remove('active'));
  const tab=document.querySelector(`.tab[data-tab="${name}"]`);
  if(tab)tab.classList.add('active');
  const panel=$('tab-'+name);if(panel)panel.classList.add('active');
  if(name==='discussions')loadDiscussions();
  if(name==='agents')loadAgents();
  if(name==='history')loadFullHistory();
  if(name==='settings')loadSettings();
  location.hash=name;
}
document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>activateTab(t.dataset.tab)));
// Restore tab from URL hash on load
{const h=location.hash.replace('#','');if(h&&$('tab-'+h))activateTab(h)}

/* Modals */
function showModal(id){$(id).classList.add('show');if(id==='newDiscModal')populateAgentChecks();if(id==='replyModal')populateReplyAgent()}
function hideModal(id){$(id).classList.remove('show')}
document.querySelectorAll('.modal-bg').forEach(m=>m.addEventListener('click',e=>{if(e.target===m)m.classList.remove('show')}));

/* Dashboard */
function doRun(){const t=$("teamSel").value;t?apiPost("/api/run",{team:t}):toast("Select a team first",'var(--red)')}
function doStop(){apiPost("/api/stop")}
function doRetry(){const t=$("teamSel").value;t?apiPost("/api/retry",{team:t}):toast("Select a team first",'var(--red)')}
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
let h=`<span class="pill" title="Tasks completed / total">${d.total_done}/${d.total_all} (${p}%)</span><span class="pill proc-pill" onclick="toggleProcTooltip(this)" title="${esc(agentTip)}" style="position:relative">${d.processes} proc</span>`;
if(_litellmPending&&!d.litellm)h+=`<span class="pill starting">starting...</span>`;
else{_litellmPending=false;h+=`<span class="pill ${d.litellm?'on':'off'}" onclick="toggleLitellm(${d.litellm})">LiteLLM ${d.litellm?'ON':'OFF'}</span>`}
$("summary").innerHTML=h}
function toggleProcTooltip(el){const existing=el.querySelector('.proc-tip');if(existing){existing.remove();return}
const d=_lastSnapshot;if(!d||!d.active_agents||!d.active_agents.length)return;
const tip=document.createElement('div');tip.className='proc-tip';
tip.style.cssText='position:absolute;top:110%;left:0;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:.5em .7em;font-size:.8em;white-space:nowrap;z-index:50;min-width:180px';
tip.innerHTML=d.active_agents.map(a=>`<div>${esc(a.agent)} \u2192 ${esc(a.task)} <span style="color:var(--gray)">${a.since}</span></div>`).join('');
el.appendChild(tip);setTimeout(()=>{if(tip.parentNode)tip.remove()},5000)}

function renderStatusBar(d){const modeColor=(d.mode||'competitive')==='competitive'?'cyan':'magenta';
const ac=d.agent_count||0;const acStyle=ac>0?'color:#4ade80;font-weight:700':'';
$("statusBar").innerHTML=`<span>Mode: <b style="color:${modeColor}">${d.mode||'competitive'}</b></span>`+
`<span style="border-left:1px solid var(--border);padding-left:1em">Enabled: <b style="${acStyle}">${ac}</b></span>`+
`<span style="border-left:1px solid var(--border);padding-left:1em;color:#6b7280">Updated: ${d.timestamp}</span>`}

function renderActivity(a){if(!a||!a.length){$("activity").innerHTML="";return}
$("activity").innerHTML='<div class="activity-title">Agent Activity</div>'+a.map(x=>`<span class="agent-card"><span class="dot"></span><b>${x.agent}</b> &rarr; ${x.task}</span>`).join('')}

let _discTopics=new Set();
function _renderTeamCard(n,t,isCompleted){
const isPlanning=t.status==='planning';
const p=t.total?Math.round((t.done+t.skip)/t.total*100):0,c=collapsed.has(n);
const hasDisc=_discTopics.has(n);
const discBtn=hasDisc?`<span class="disc-link" title="Open discussion" onclick="event.stopPropagation();goToDiscussion('${n.replace(/'/g,"\\'")}')">💬</span>`:'';
const statusBadge=isPlanning?`<span class="disc-status open" style="margin-left:.5em;font-size:.7em">planning</span>`:'';
const stats=isPlanning?`<span class="team-stats" style="color:var(--gray)">0 tasks</span>`:`<span class="team-stats">${t.done+t.skip}/${t.total}</span><div class="bar-wrap"><div class="bar-fill" style="width:${p}%"></div></div>`;
let h=`<div class="team${c?' collapsed':''}${isCompleted?' team-done':''}${isPlanning?' team-planning':''}" data-team="${n}"><div class="team-header" onclick="toggleTeam('${n.replace(/'/g,"\\'")}')"><span class="team-chevron">&#9660;</span><span class="team-name">${esc(n)}</span>${statusBadge}${discBtn}${stats}</div><div class="team-body">`;
if(isPlanning){const parts=t.participants||[];const mc=t.msg_count||0;
h+=`<div style="padding:.4em 0;font-size:.82em;color:var(--gray)">${parts.length} participants: ${parts.join(', ')}${mc?' \u00b7 '+mc+' msg':''}</div>`}
for(const tk of t.tasks){h+=`<div class="task st-${tk.status}"><span class="task-icon">${ICONS[tk.status]||"\u25cb"}</span><span class="task-id mono">${tk.id}</span><span class="task-name">${esc(tk.name)}</span>${tk.agent?`<span class="task-agent">${esc(tk.agent)}</span>`:''}</div>`}
h+=`</div></div>`;return h}
function renderTeams(teams){const r=$("root");if(!Object.keys(teams).length){r.innerHTML='<div class="empty">No tasks found.</div>';return}
const active=[],completed=[];
for(const n of Object.keys(teams)){const t=teams[n];const done=t.done+t.skip;
if(t.total>0&&done>=t.total)completed.push(n);else active.push(n)}
let h=active.map(n=>_renderTeamCard(n,teams[n],false)).join('');
if(completed.length){h+=`<div style="margin-top:1.2em;cursor:pointer;color:var(--text-dim);font-size:.85em;user-select:none" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'block':'none';this.querySelector('span').textContent=this.nextElementSibling.style.display==='none'?'\u25b8':'\u25be'"><span>\u25b8</span> Completed (${completed.length})</div><div style="display:none">${completed.map(n=>_renderTeamCard(n,teams[n],true)).join('')}</div>`}
r.innerHTML=h}

function renderRunLog(lines){const p=$("runLogPanel"),b=$("runLogBody"),st=$("runLogStatus");
if(!lines||!lines.length){p.style.display="none";return}
p.style.display="block";
const wasAtBottom=b.scrollHeight-b.scrollTop-b.clientHeight<30;
b.innerHTML=lines.map(l=>{
let cls='';const ll=l.toLowerCase();
if(ll.includes('error')||ll.includes('failed')||ll.includes('blocked'))cls='color:var(--red)';
else if(ll.includes('✓')||ll.includes('approved')||ll.includes('done')||ll.includes('merged'))cls='color:var(--green)';
else if(ll.includes('ревью')||ll.includes('review'))cls='color:var(--blue)';
else if(ll.includes('⏳')||ll.includes('читает')||ll.includes('думает'))cls='color:var(--gray)';
else if(ll.includes('═══'))cls='color:var(--yellow);font-weight:600';
return `<div style="${cls}">${esc(l)}</div>`}).join('');
st.textContent=lines.length+' lines';
if(wasAtBottom)b.scrollTop=b.scrollHeight}

function renderMiniHistory(ev){const p=$("miniHistory"),b=$("miniHistoryBody");if(!ev||!ev.length){p.style.display="none";return}
p.style.display="block";b.innerHTML=ev.slice(0,10).map(e=>`<div class="history-line hk-${e.kind}">${esc(e.text)}</div>`).join('')}

function render(d){
if(JSON.stringify(_teamNames)!==JSON.stringify(d.team_names||[])){_teamNames=d.team_names||[];renderControls()}
_discTopics=new Set(d.disc_topics||[]);
renderSummary(d);renderStatusBar(d);renderActivity(d.active_agents);renderTeams(d.teams);renderRunLog(d.run_log);renderMiniHistory(d.history);
_allHistory=d.history||[];_lastSnapshot=d}

/* Discussions */
function _discCard(x){const tt=x.task_total||0,td=x.task_done||0;
const taskBadge=tt?`<span style="font-size:.72em;color:${td>=tt?'var(--green)':'var(--text-dim)'};margin-right:.5em">${td}/${tt} tasks</span>`:'';
return `<div class="disc-item${x.status!=='open'?' disc-resolved':''}" data-topic="${esc(x.topic)}" onclick="viewDiscussion('${esc(x.topic)}')">`+
`<div style="display:flex;justify-content:space-between;align-items:center"><span class="disc-topic">${esc(x.display_name||x.topic)}</span><div>${taskBadge}<span style="font-size:.75em;color:var(--gray);margin-right:.5em">${x.msg_count||0} msg</span><span class="disc-status ${x.status}">${x.status}</span></div></div>`+
`<div class="disc-meta">${x.participants} participants: ${(x.participant_names||[]).join(', ')}</div></div>`}
function loadDiscussions(){apiGet("/api/discuss/list").then(d=>{if(!d)return;
const list=d.discussions||[];
if(!list.length){$("discList").innerHTML='<div class="empty">No discussions yet.</div>';return}
const open=list.filter(x=>x.status==='open'),closed=list.filter(x=>x.status!=='open');
let h=open.map(_discCard).join('');
if(!open.length&&!closed.length){h='<div class="empty">No discussions yet.</div>'}
else if(!open.length){h='<div style="color:var(--text-dim);font-size:.85em;margin-bottom:.8em">No open discussions</div>'}
if(closed.length){h+=`<div style="margin-top:1.2em;cursor:pointer;color:var(--text-dim);font-size:.85em;user-select:none" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'block':'none';this.querySelector('span').textContent=this.nextElementSibling.style.display==='none'?'▸':'▾'"><span>▸</span> Archived (${closed.length})</div><div style="display:none">${closed.map(_discCard).join('')}</div>`}
$("discList").innerHTML=h})}

const AGENT_COLORS={claude:'#22d3ee',gemini:'#60a5fa',qwen:'#a855f7','qwen-api':'#e879f9',llama:'#eab308',devstral:'#4ade80','gpt-oss':'#22d3ee',techlead:'#4ade80','aider-llama':'#eab308','aider-devstral':'#4ade80','aider-qwen':'#a855f7','aider-gptoss':'#22d3ee','goose-llama':'#ef4444','goose-devstral':'#e2e8f0'};
function _agentCss(name){const base=name.split('+')[0];return AGENT_COLORS[base]||'#a855f7'}
function formatDiscContent(raw){
if(!raw)return '(empty)';
let h=esc(raw);
// First line (topic header)
h=h.replace(/^# (.+)$/m,'<div style="color:var(--yellow);font-weight:700;text-align:center;margin:.5em 0;padding:.3em;border-top:2px solid var(--yellow);border-bottom:2px solid var(--yellow)">$1</div>');
// Section headers: ## @agent (date) — styled with agent-specific colors
h=h.replace(/^## @(\S+)\s*(\(.*?\))?$/gm,function(m,agent,date){
const c=_agentCss(agent);const isTl=agent==='techlead';
return `</div><div style="background:rgba(42,42,78,.4);border-left:3px solid ${c};padding:.5em .8em;margin:1.2em 0 .6em;border-radius:0 6px 6px 0"><span style="color:${c};font-weight:700">${isTl?'TechLead':'@'+agent}</span> <span style="color:var(--gray);font-size:.85em">${date||''}</span></div><div style="padding-left:1em">`});
// РЕЗОЛЮЦИЯ line
h=h.replace(/(РЕЗОЛЮЦИЯ:.*)/g,'<div style="background:rgba(74,222,128,.1);border:1px solid rgba(74,222,128,.3);border-radius:6px;padding:.5em .8em;margin:.5em 0;color:var(--green);font-weight:600">$1</div>');
// CONFIDENCE line
h=h.replace(/(CONFIDENCE:\s*\d+%)/g,'<span style="background:rgba(234,179,8,.15);padding:.1em .5em;border-radius:4px;color:var(--yellow);font-weight:600">$1</span>');
// --- RESOLVED ---
h=h.replace(/(--- RESOLVED ---)/g,'<div style="color:var(--green);text-align:center;margin:1em 0;font-weight:700;border-top:1px solid rgba(74,222,128,.3);border-bottom:1px solid rgba(74,222,128,.3);padding:.4em">$1</div>');
// ДИСКУССИЯ ПЕРЕОТКРЫТА
h=h.replace(/(ДИСКУССИЯ ПЕРЕОТКРЫТА)/g,'<div style="color:var(--yellow);text-align:center;margin:1em 0;font-weight:700;border-top:1px solid rgba(234,179,8,.3);border-bottom:1px solid rgba(234,179,8,.3);padding:.4em">$1</div>');
// Markdown rendering
// Bold: **text**
h=h.replace(/\*\*([^*]+)\*\*/g,'<b style="color:var(--text)">$1</b>');
// Italic: *text*
h=h.replace(/\*([^*]+)\*/g,'<i>$1</i>');
// Inline code: `text`
h=h.replace(/`([^`]+)`/g,'<code style="background:rgba(42,42,78,.6);padding:.1em .3em;border-radius:3px;font-family:monospace;color:#e2e8f0">$1</code>');
// Headers: ### text
h=h.replace(/^### (.+)$/gm,'<div style="color:var(--text);font-weight:600;margin:.8em 0 .3em">$1</div>');
// List items: - text or * text (with nesting via indentation)
h=h.replace(/^(\s*)[-*] (.+)$/gm,function(m,indent,text){
const level=Math.floor(indent.length/2);const ml=level*1.2;
return `<div style="margin-left:${ml}em;padding:.1em 0"><span style="color:var(--gray);margin-right:.4em">\u2022</span>${text}</div>`});
// Numbered lists: 1. text
h=h.replace(/^(\s*)\d+\.\s+(.+)$/gm,function(m,indent,text){
const level=Math.floor(indent.length/2);const ml=level*1.2;
return `<div style="margin-left:${ml}em;padding:.1em 0">${text}</div>`});
// Horizontal rules
h=h.replace(/^---$/gm,'<hr style="border:none;border-top:1px solid var(--border);margin:.8em 0">');
// Tables: | col | col |
h=h.replace(/^(\|.+\|)\n(\|[-| :]+\|)\n((?:\|.+\|\n?)+)/gm,function(m,header,sep,body){
const ths=header.split('|').filter(s=>s.trim()).map(s=>'<th style="padding:.3em .6em;border-bottom:1px solid var(--border);color:var(--text-dim);font-size:.85em">'+s.trim()+'</th>').join('');
const rows=body.trim().split('\n').map(r=>'<tr>'+r.split('|').filter(s=>s.trim()).map(s=>'<td style="padding:.2em .6em;font-size:.85em">'+s.trim()+'</td>').join('')+'</tr>').join('');
return '<table style="border-collapse:collapse;margin:.5em 0;width:100%"><thead><tr>'+ths+'</tr></thead><tbody>'+rows+'</tbody></table>'});
// Paragraphs: double newline → margin
h=h.replace(/\n\n/g,'</p><p style="margin:.6em 0">');
// Clean up empty leading div
h=h.replace(/^<\/div>/,'');
return h}

function viewDiscussion(topic){
  // Toggle: clicking the same discussion again closes it
  if(_currentDisc===topic){hideDiscViewer();return}
  _currentDisc=topic;
  // Clear previous highlights and close any open viewer
  document.querySelectorAll('.disc-item').forEach(e=>e.classList.remove('disc-highlight'));
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
    const isResolved=d.content&&(d.content.includes('РЕЗОЛЮЦИЯ')||d.content.includes('--- RESOLVED ---'));
    let taskHtml='';
    if(d.tasks&&d.tasks.length){taskHtml='<div style="border-bottom:1px solid var(--border);padding:.5em 1em;font-size:.82em"><b style="color:var(--text-dim)">Tasks</b> ';
    for(const tk of d.tasks){const ic=ICONS[tk.status]||'\u25cb';const col=tk.status==='done'?'var(--green)':tk.status==='skip'?'var(--gray)':'var(--text)';
    taskHtml+=`<span style="color:${col};margin-right:.8em">${ic} <span class="mono">${tk.id}</span></span>`}
    taskHtml+='</div>'}
    const navBtns=`<button class="pill" style="border-color:var(--purple);color:var(--purple)" onclick="hideDiscViewer();activateTab('dashboard')">\u2190 Dashboard</button> <button class="pill" style="border-color:var(--text-dim);color:var(--text-dim)" onclick="hideDiscViewer()">Collapse</button>`;
    const resolveBtn=isResolved?`<button class="pill" style="border-color:var(--yellow);color:var(--yellow)" onclick="reopenDiscussion()">Reopen</button>`
      :`<button class="pill" style="border-color:var(--green);color:var(--green)" onclick="showModal('resolveModal')">Resolve</button>`;
    // Header
    const hdr=`<div style="display:flex;justify-content:space-between;align-items:center;padding:.6em 1em;border-bottom:1px solid var(--border)"><b style="color:var(--yellow)">${topic.replace(/-/g,' ')}</b><div>${navBtns}</div></div>`;
    // Content (scrollable, fixed font size)
    const content=`<div id="discContent" style="padding:1em 1.2em;max-height:50vh;overflow-y:auto;font-size:.88em;line-height:1.6;color:var(--text)">${formatDiscContent(d.content)}</div>`;
    // Bottom bar: textarea row, then buttons row
    const bottomBar=`<div style="border-top:1px solid var(--border);padding:.6em 1em">
<textarea id="discInput" placeholder="Message or /command..." style="width:100%;box-sizing:border-box;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:.5em .7em;font-size:.85em;font-family:inherit;resize:none;min-height:2.2em;max-height:6em;outline:none;margin-bottom:.5em" onkeydown="discInputKey(event)" oninput="discInputChanged(this)"></textarea>
<div style="display:flex;justify-content:space-between;align-items:center"><div style="display:flex;gap:.4em">${navBtns}</div><div style="display:flex;gap:.4em"><button class="pill" style="border-color:var(--green);color:var(--green)" onclick="sendDiscInput()">Send</button><button class="pill action" onclick="showModal('replyModal')">Agent</button>${resolveBtn}<button class="pill" style="border-color:var(--gray);color:var(--gray)" onclick="toggleDiscHelp()">?</button></div></div></div>`;
    panel.innerHTML=hdr+taskHtml+content+bottomBar;
    // Auto-scroll to bottom after render
    setTimeout(()=>{const ce=_getDiscContent();if(ce)ce.scrollTop=ce.scrollHeight},100);
  })
}
let _discFromDashboard=false;
function hideDiscViewer(){document.querySelectorAll('.disc-expand').forEach(e=>e.remove());document.querySelectorAll('.disc-item').forEach(e=>e.classList.remove('disc-highlight'));_currentDisc="";_discFromDashboard=false;_hideAc();const hp=document.getElementById('discHelpPopup');if(hp)hp.remove()}
function goToDiscussion(topic){
  hideDiscViewer();
  activateTab('discussions');
  let tries=0;
  const tryOpen=()=>{
    const el=document.querySelector(`.disc-item[data-topic="${topic}"]`);
    if(el){
      // Expand archived section if element is inside it
      const hidden=el.closest('div[style*="display: none"],div[style*="display:none"]');
      if(hidden){hidden.style.display='block';const tog=hidden.previousElementSibling;if(tog){const sp=tog.querySelector('span');if(sp)sp.textContent='\u25be'}}
      _discFromDashboard=true;el.classList.add('disc-highlight');el.scrollIntoView({behavior:'smooth',block:'center'});viewDiscussion(topic)
    } else if(++tries<15){setTimeout(tryOpen,200)}
    else{toast('Discussion not found','var(--red)')}};
  setTimeout(tryOpen,100)}

function populateAgentChecks(){apiGet("/api/agents").then(d=>{if(!d)return;
_agentNames=(d.agents||[]).filter(a=>a.enabled).map(a=>a.name);
$("ndAgents").innerHTML=_agentNames.map(n=>`<label><input type="checkbox" value="${n}" checked> ${n}</label>`).join('')})}

function populateReplyAgent(){apiGet("/api/agents").then(d=>{if(!d)return;
const agents=(d.agents||[]).filter(a=>a.enabled);
$("replyAgent").innerHTML=agents.map(a=>`<option value="${a.name}">${a.name}</option>`).join('')})}

function createDiscussion(){const topic=$("ndTopic").value.trim(),q=$("ndQuestion").value.trim();
if(!topic||!q){toast("Fill topic and question",'var(--red)');return}
const agents=Array.from(document.querySelectorAll('#ndAgents input:checked')).map(i=>i.value);
apiPost("/api/discuss/new",{topic,question:q,agents}).then(d=>{if(d&&d.ok){hideModal('newDiscModal');$("ndTopic").value='';$("ndQuestion").value='';loadDiscussions()}})}

function sendReply(){if(!_currentDisc){toast("No discussion selected",'var(--red)');return}
const agent=$("replyAgent").value;
apiPost("/api/discuss/reply",{topic:_currentDisc,agent}).then(d=>{if(d&&d.ok){hideModal('replyModal');setTimeout(()=>viewDiscussion(_currentDisc),2000)}})}

const DISC_CMDS=[
{cmd:'/ok',args:'[comment]',desc:'Final round: all agents → auto-resolve → generate tasks'},
{cmd:'/resolve',args:'[text]',desc:'Manual resolve with your text'},
{cmd:'/reopen',args:'[reason]',desc:'Reopen resolved discussion'},
{cmd:'/agent',args:'<name>',desc:'Send agent reply (opens selector)'},
{cmd:'/show',args:'',desc:'Scroll to top of discussion'},
{cmd:'/tasks',args:'',desc:'Show linked tasks'},
];
let _acSel=0;
function _showAc(items){const el=$("discInput");if(!el||!items.length){_hideAc();return}
let ac=document.getElementById('discAcPopup');
if(!ac){ac=document.createElement('div');ac.id='discAcPopup';
ac.style.cssText='position:fixed;background:var(--card);border:1px solid var(--border);border-radius:8px;max-height:200px;overflow-y:auto;font-size:.82em;z-index:200;box-shadow:0 -4px 12px rgba(0,0,0,.4)';
document.body.appendChild(ac)}
const r=el.getBoundingClientRect();ac.style.left=r.left+'px';ac.style.width=r.width+'px';ac.style.bottom=(window.innerHeight-r.top+4)+'px';
ac.style.display='block';_acSel=0;
ac.innerHTML=items.map((c,i)=>`<div class="ac-item${i===0?' ac-sel':''}" data-i="${i}" onmousedown="insertCmd('${c.cmd}','${c.args}')" onmouseover="_acHover(${i})"><span style="color:var(--green);font-weight:600">${c.cmd}</span> <span style="color:var(--gray)">${c.args}</span> <span style="color:var(--text-dim)">\u2014 ${c.desc}</span></div>`).join('')}
function _hideAc(){const ac=document.getElementById('discAcPopup');if(ac)ac.style.display='none'}
function _acHover(i){_acSel=i;document.querySelectorAll('.ac-item').forEach((el,j)=>el.classList.toggle('ac-sel',j===i))}
function insertCmd(cmd,args){const el=$("discInput");if(el){el.value=cmd+(args?' '+args:'');el.focus();
if(args){const start=cmd.length+1;el.setSelectionRange(start,start+args.length)}}_hideAc()}
function toggleDiscHelp(){
let m=document.getElementById('discHelpPopup');
if(m){m.remove();return}
m=document.createElement('div');m.id='discHelpPopup';
m.style.cssText='position:fixed;top:20%;right:2em;background:var(--card);border:1px solid var(--border);border-radius:12px;z-index:300;box-shadow:0 8px 32px rgba(0,0,0,.5);width:340px;max-height:70vh;display:flex;flex-direction:column';
const hdr=document.createElement('div');
hdr.style.cssText='display:flex;justify-content:space-between;align-items:center;padding:.8em 1em;border-bottom:1px solid var(--border);cursor:move;user-select:none';
hdr.innerHTML=`<b style="color:var(--yellow)">Commands</b><span style="cursor:pointer;color:var(--text-dim);font-size:1.1em" onclick="this.closest('#discHelpPopup').remove()">\u2715</span>`;
// Drag
let dx=0,dy=0,sx=0,sy=0;
hdr.onmousedown=function(e){sx=e.clientX;sy=e.clientY;
document.onmousemove=function(e2){dx=sx-e2.clientX;dy=sy-e2.clientY;sx=e2.clientX;sy=e2.clientY;
m.style.top=(m.offsetTop-dy)+'px';m.style.left=(m.offsetLeft-dx)+'px';m.style.right='auto'};
document.onmouseup=function(){document.onmousemove=null;document.onmouseup=null}};
const body=document.createElement('div');
body.style.cssText='padding:.6em 1em;overflow-y:auto;flex:1';
body.innerHTML=DISC_CMDS.map(c=>`<div style="padding:.5em .4em;border-bottom:1px solid rgba(42,42,78,.3);cursor:pointer;border-radius:4px" onmouseover="this.style.background='var(--hover)'" onmouseout="this.style.background=''" onclick="insertCmd('${c.cmd}','${c.args}')"><span style="color:var(--green);font-weight:600">${c.cmd}</span> <span style="color:var(--gray)">${c.args}</span><div style="color:var(--text-dim);font-size:.85em;margin-top:.15em">${c.desc}</div></div>`).join('')+
`<div style="padding:.6em .4em;font-size:.82em;color:var(--text-dim)">Plain text \u2192 TechLead message<br>Enter \u2192 send, Shift+Enter \u2192 newline</div>`;
m.appendChild(hdr);m.appendChild(body);document.body.appendChild(m)}
function discInputChanged(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,96)+'px';
const v=el.value;
if(v.startsWith('/')&&!v.includes(' ')&&v.length>1){
const matches=DISC_CMDS.filter(c=>c.cmd.startsWith(v));
if(matches.length){_showAc(matches)}else{_hideAc()}}else{_hideAc()}}
function discInputKey(e){const ac=document.getElementById('discAcPopup');const visible=ac&&ac.style.display==='block';
if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();if(visible){const items=ac.querySelectorAll('.ac-item');if(items[_acSel])items[_acSel].dispatchEvent(new Event('mousedown'))}else{sendDiscInput()};return}
if(e.key==='Tab'&&visible){e.preventDefault();const items=ac.querySelectorAll('.ac-item');if(items[_acSel])items[_acSel].dispatchEvent(new Event('mousedown'));return}
if(e.key==='ArrowDown'&&visible){e.preventDefault();const items=ac.querySelectorAll('.ac-item');_acSel=Math.min(_acSel+1,items.length-1);items.forEach((el,j)=>el.classList.toggle('ac-sel',j===_acSel));return}
if(e.key==='ArrowUp'&&visible){e.preventDefault();const items=ac.querySelectorAll('.ac-item');_acSel=Math.max(_acSel-1,0);items.forEach((el,j)=>el.classList.toggle('ac-sel',j===_acSel));return}
if(e.key==='Escape'){_hideAc();return}}
let _okPollTimer=null;
function _getDiscContent(){return document.querySelector('.disc-expand #discContent')||$('discContent')}
function _pollDiscOk(){
if(_okPollTimer)clearInterval(_okPollTimer);
_okPollTimer=setInterval(()=>{
if(!_currentDisc){clearInterval(_okPollTimer);_okPollTimer=null;return}
apiGet("/api/discuss/show/"+encodeURIComponent(_currentDisc)).then(d=>{if(!d||!d.content)return;
const ce=_getDiscContent();if(ce){ce.innerHTML=formatDiscContent(d.content);setTimeout(()=>ce.scrollTop=ce.scrollHeight,50)}
if(d.content.includes('РЕЗОЛЮЦИЯ')||d.content.includes('--- RESOLVED ---')){
clearInterval(_okPollTimer);_okPollTimer=null;toast('Discussion resolved and tasks generated','var(--green)');loadDiscussions()}
})},5000)}
function _reloadDiscContent(){apiGet("/api/discuss/show/"+encodeURIComponent(_currentDisc)).then(d2=>{if(!d2)return;
const ce=_getDiscContent();if(ce){ce.innerHTML=formatDiscContent(d2.content);setTimeout(()=>ce.scrollTop=ce.scrollHeight,50)}})}
function sendDiscInput(){if(!_currentDisc)return;
const el=$("discInput");if(!el)return;const text=el.value.trim();if(!text)return;
el.value='';el.style.height='auto';
// Handle slash commands
if(text.startsWith('/')){const parts=text.split(/\s+/);const cmd=parts[0];const arg=parts.slice(1).join(' ');
if(cmd==='/ok'){toast('Running /ok — final round + auto-resolve...');apiPost("/api/discuss/ok",{topic:_currentDisc,comment:arg}).then(d=>{if(d&&d.ok){toast('Final round started. Agents replying...');_pollDiscOk()}});return}
if(cmd==='/resolve'){if(arg){apiPost("/api/discuss/resolve",{topic:_currentDisc,resolution:arg}).then(d=>{if(d&&d.ok){loadDiscussions();hideDiscViewer()}})}else{showModal('resolveModal')};return}
if(cmd==='/reopen'){apiPost("/api/discuss/reopen",{topic:_currentDisc}).then(d=>{if(d&&d.ok){hideDiscViewer();viewDiscussion(_currentDisc);loadDiscussions()}});return}
if(cmd==='/agent'){showModal('replyModal');return}
if(cmd==='/show'){const ce=$('discContent');if(ce)ce.scrollTop=0;return}
if(cmd==='/tasks'){const ce=$('discContent');if(ce){const tl=panel.querySelector('[style*="Tasks"]');if(tl)tl.scrollIntoView()};return}
toast('Unknown command: '+cmd,'var(--red)');return}
apiPost("/api/discuss/write",{topic:_currentDisc,text}).then(d=>{if(d&&d.ok)_reloadDiscContent()})}
function reopenDiscussion(){if(!_currentDisc){toast("No discussion selected",'var(--red)');return}
apiPost("/api/discuss/reopen",{topic:_currentDisc}).then(d=>{if(d&&d.ok){hideDiscViewer();viewDiscussion(_currentDisc);loadDiscussions()}})}
function resolveDiscussion(){if(!_currentDisc){toast("No discussion selected",'var(--red)');return}
const text=$("resolveText").value.trim();if(!text){toast("Enter resolution text",'var(--red)');return}
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

function toggleAgent(name,enabled){fetch("/api/agents/toggle",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name,enabled})}).then(r=>r.json()).then(d=>{
toast(d.msg||"OK",enabled?'var(--green)':'var(--red)');if(d.ok){loadAgents();apiGet("/api/snapshot").then(s=>{if(s)renderStatusBar(s)})}}).catch(e=>toast("Error: "+e,'var(--red)'))}
function setMode(mode){apiPost("/api/mode",{mode}).then(()=>apiGet("/api/snapshot").then(s=>{if(s)renderStatusBar(s)}))}

/* History */
function loadFullHistory(){apiGet("/api/history").then(d=>{if(!d)return;_allHistory=d.events||[];filterHistory()})}
function loadMoreHistory(){apiGet("/api/history?limit=200").then(d=>{if(!d)return;_allHistory=d.events||[];filterHistory();toast("Loaded "+_allHistory.length+" events")})}
function filterHistory(){const search=($("histSearch").value||'').toLowerCase(),kind=$("histKind").value;
const filtered=_allHistory.filter(e=>{if(kind&&e.kind!==kind)return false;if(search&&!e.text.toLowerCase().includes(search))return false;return true});
$("fullHistoryBody").innerHTML=filtered.length?filtered.map(e=>`<div class="history-line hk-${e.kind}">${esc(e.text)}</div>`).join(''):'<div class="empty">No matching events.</div>'}

/* Settings */
let _rawConfigVisible=false,_rawConfigText='',_editMode=false;
function toggleRawConfig(){_rawConfigVisible=!_rawConfigVisible;
$("rawConfigWrap").style.display=_rawConfigVisible?'block':'none';
$("parsedConfigView").style.display=_rawConfigVisible?'none':'block';
$("rawToggleBtn").textContent=_rawConfigVisible?'Show Parsed':'Show Raw';
if(!_rawConfigVisible)cancelEditConfig()}
function toggleEditConfig(){_editMode=true;
$("configEditor").value=_rawConfigText;
$("configView").style.display='none';$("configEditWrap").style.display='block';
$("editConfigBtn").style.display='none';$("saveConfigBtn").style.display='';$("cancelConfigBtn").style.display=''}
function cancelEditConfig(){_editMode=false;
$("configView").style.display='block';$("configEditWrap").style.display='none';
$("editConfigBtn").style.display='';$("saveConfigBtn").style.display='none';$("cancelConfigBtn").style.display='none'}
function saveConfig(){const body=JSON.stringify({content:$("configEditor").value});
fetch("/api/config/save",{method:"POST",headers:{"Content-Type":"application/json"},body}).then(r=>r.json()).then(d=>{
if(d.ok){toast("Config saved");cancelEditConfig();loadSettings()}else{toast("Save failed: "+(d.error||"unknown"),'var(--red)')}
}).catch(e=>toast("Save error: "+e,'var(--red)'))}

function _humanLabel(k){return k.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase())}
function _fmtCmd(arr){if(!Array.isArray(arr))return esc(String(arr));
if(arr.length&&Array.isArray(arr[0]))return arr.map(a=>a.join(' ')).join('<br>');
return arr.join(' ')}
function renderParsedConfig(p){if(!p){$("parsedConfigView").innerHTML='';return}
let h='';
// Project section
const pr=p.project||{};
h+='<div style="margin-bottom:1.2em"><div style="color:var(--purple);font-weight:600;margin-bottom:.5em;font-size:.95em">Project</div>';
h+='<div class="sys-info" style="margin:0">';
for(const [k,v] of [['Name',pr.name],['Root',pr.root],['Mode',pr.mode],['Dev Branch',pr.dev_branch]]){
const val=v||'-';const color=k==='Mode'?'color:var(--purple);font-weight:600':'';
h+=`<div class="sys-card"><label>${k}</label><span style="${color}">${esc(String(val))}</span></div>`}
h+='</div></div>';
// Agents section
const ag=p.agents||[];
if(ag.length){
const enabled=ag.filter(a=>a.enabled),disabled=ag.filter(a=>!a.enabled);
h+='<div style="margin-bottom:1.2em"><div style="color:var(--purple);font-weight:600;margin-bottom:.5em;font-size:.95em">Agents <span style="color:var(--text-dim);font-weight:400;font-size:.85em">('+enabled.length+' active / '+ag.length+' total)</span></div>';
h+='<table class="agents-table"><thead><tr><th>Name</th><th>Command</th><th>Protocol</th><th>Model</th></tr></thead><tbody>';
for(const a of enabled){h+=`<tr><td><b style="color:var(--green)">${esc(a.name)}</b></td><td class="mono">${esc(a.command)}</td><td>${esc(a.protocol)}</td><td class="mono" style="font-size:.85em">${esc(a.model||'-')}</td></tr>`}
if(disabled.length){h+=`<tr><td colspan="4" style="padding:.6em .7em .3em;color:var(--text-dim);font-size:.8em;border:none">Disabled</td></tr>`;
for(const a of disabled){h+=`<tr class="agent-off"><td>${esc(a.name)}</td><td class="mono">${esc(a.command)}</td><td>${esc(a.protocol)}</td><td class="mono" style="font-size:.85em">${esc(a.model||'-')}</td></tr>`}}
h+='</tbody></table></div>'}
// Limits section
const lm=p.limits||{};
if(Object.keys(lm).length){h+='<div style="margin-bottom:1.2em"><div style="color:var(--purple);font-weight:600;margin-bottom:.5em;font-size:.95em">Limits</div>';
h+='<div class="sys-info" style="margin:0">';
for(const [k,v] of Object.entries(lm)){const sv=String(v);const color=sv==='true'?'color:var(--green)':sv==='false'?'color:var(--red)':!isNaN(v)?'color:var(--yellow)':'';
h+=`<div class="sys-card"><label>${esc(_humanLabel(k))}</label><span style="${color};font-weight:600">${esc(sv)}</span></div>`}
h+='</div></div>'}
// Frames section
const fr=p.frames||[];
if(fr.length){h+='<div style="margin-bottom:1.2em"><div style="color:var(--purple);font-weight:600;margin-bottom:.5em;font-size:.95em">Frames <span style="color:var(--text-dim);font-weight:400;font-size:.85em">('+fr.length+')</span></div>';
h+='<div style="display:flex;flex-wrap:wrap;gap:.4em">';
for(const f of fr){h+=`<span class="pill" title="${esc(f.description||'')}" style="font-size:.78em">${esc(f.name)}</span>`}
h+='</div></div>'}
// Build section
const bl=p.build||{};
if(bl.commands&&bl.commands.length||bl.check_command||bl.lint_fix&&bl.lint_fix.length){
h+='<div style="margin-bottom:1.2em"><div style="color:var(--purple);font-weight:600;margin-bottom:.5em;font-size:.95em">Build</div>';
h+='<div class="sys-info" style="margin:0">';
if(bl.commands&&bl.commands.length)h+=`<div class="sys-card"><label>Commands</label><span class="mono" style="font-size:.82em">${_fmtCmd(bl.commands)}</span></div>`;
if(bl.lint_fix&&bl.lint_fix.length)h+=`<div class="sys-card"><label>Lint Fix</label><span class="mono" style="font-size:.82em">${_fmtCmd(bl.lint_fix)}</span></div>`;
if(bl.check_command)h+=`<div class="sys-card"><label>Check Command</label><span class="mono" style="font-size:.82em">${esc(bl.check_command)}</span></div>`;
h+='</div></div>'}
$("parsedConfigView").innerHTML=h}

function highlightToml(raw){let h=esc(raw);
h=h.replace(/^(#.*)$/gm,'<span style="color:#6b7280">$1</span>');
h=h.replace(/^\[([^\]]+)\]$/gm,'<span style="color:#a855f7;font-weight:600">[$1]</span>');
h=h.replace(/^(\s*)([\w\-]+)(\s*=\s*)(&quot;(?:[^&]|&(?!quot;))*&quot;)/gm,'$1<span style="color:#fff;font-weight:600">$2</span>$3<span style="color:#4ade80">$4</span>');
h=h.replace(/(\[)(&quot;(?:[^&]|&(?!quot;))*&quot;)((?:\s*,\s*&quot;(?:[^&]|&(?!quot;))*&quot;)*\])/gm,function(m,br,first,rest){
return br+'<span style="color:#4ade80">'+first+'</span>'+rest.replace(/(&quot;(?:[^&]|&(?!quot;))*&quot;)/g,'<span style="color:#4ade80">$1</span>')});
h=h.replace(/^(\s*)([\w\-]+)(\s*=\s*)(true|false)$/gm,'$1<span style="color:#fff;font-weight:600">$2</span>$3<span style="color:#ef4444">$4</span>');
h=h.replace(/^(\s*)([\w\-]+)(\s*=\s*)(\d+(?:\.\d+)?)$/gm,'$1<span style="color:#fff;font-weight:600">$2</span>$3<span style="color:#eab308">$4</span>');
h=h.replace(/^(\s*)([\w\-]+)(\s*=)(?!.*<span)/gm,'$1<span style="color:#fff;font-weight:600">$2</span>$3');
return h}
function loadSettings(){apiGet("/api/config").then(d=>{if(!d)return;
_rawConfigText=d.config||"";
$("configView").innerHTML=highlightToml(_rawConfigText);
renderParsedConfig(d.parsed||null);
const s=d.system||{};
$("sysInfo").innerHTML=`<div class="sys-card"><label>Python</label><span>${esc(s.python||'?')}</span></div>`+
`<div class="sys-card"><label>Git Branch</label><span>${esc(s.branch||'?')}</span></div>`+
`<div class="sys-card"><label>Repo Root</label><span>${esc(s.root||'?')}</span></div>`+
`<div class="sys-card"><label>Mode</label><span>${esc(s.mode||'?')}</span></div>`+
`<div class="sys-card"><label>Frames</label><span>${(s.frames||[]).join(', ')||'none'}</span></div>`})}

/* SSE */
window.addEventListener('load',()=>{
function poll(){fetch("/api/snapshot").then(r=>r.json()).then(d=>{render(d)}).catch(()=>{});setTimeout(poll,5000)}
poll()});
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
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
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
        elif self.path.startswith("/api/history"):
            limit = 100
            if "?" in self.path:
                from urllib.parse import parse_qs, urlparse
                qs = parse_qs(urlparse(self.path).query)
                try:
                    limit = int(qs.get("limit", [100])[0])
                except (ValueError, IndexError):
                    pass
            self._json_response({"ok": True, "events": _read_history(limit)})
        elif self.path == "/api/discuss/list":
            self._json_response({"ok": True, "discussions": _list_discussions()})
        elif self.path.startswith("/api/discuss/show/"):
            topic = self.path.split("/api/discuss/show/", 1)[1]
            # URL-decode topic
            from urllib.parse import unquote
            topic = unquote(topic)
            content = _show_discussion(topic)
            # Find linked tasks
            linked_tasks = []
            try:
                for t in parse_tasks():
                    if (t.discussion or "") == topic:
                        st = (t.status or "").split(":")[0]
                        linked_tasks.append({"id": t.id, "name": t.name, "status": st,
                                             "agent": t.agent if t.agent and t.agent != "\u2014" else ""})
            except Exception:
                pass
            self._json_response({"ok": bool(content), "content": content, "tasks": linked_tasks})
        elif self.path == "/api/agents":
            self._json_response({"ok": True, "agents": _list_agents_info(), "mode": cfg.mode})
        elif self.path == "/api/snapshot":
            self._json_response(_build_snapshot())
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
        elif self.path == "/api/discuss/ok":
            self._api_discuss_ok(body)
        elif self.path == "/api/discuss/write":
            self._api_discuss_write(body)
        elif self.path == "/api/discuss/reopen":
            self._api_discuss_reopen(body)
        elif self.path == "/api/agents/toggle":
            self._api_agents_toggle(body)
        elif self.path == "/api/mode":
            self._api_set_mode(body)
        elif self.path == "/api/config/save":
            self._api_config_save(body)
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

    def _api_discuss_ok(self, body: dict):
        """Run /ok flow: techlead comment → final round (all agents) → auto-resolve → generate tasks."""
        topic = body.get("topic", "").strip()
        comment = body.get("comment", "").strip()
        if not topic:
            self._json_response({"ok": False, "msg": "Topic required"})
            return
        filepath = cfg.discuss_dir / f"{topic}.md"
        if not filepath.exists():
            filepath = cfg.discuss_dir / "archive" / f"{topic}.md"
        if not filepath.exists():
            self._json_response({"ok": False, "msg": f"Discussion '{topic}' not found"})
            return

        def _run_ok():
            from .utils import log as _log
            try:
                from .discuss import _chat_append, _chat_agent_reply, _chat_auto_resolve
                from .discuss import _post_resolve, _auto_link_discussion

                if comment:
                    _chat_append(filepath, "techlead", comment)

                _chat_append(filepath, "techlead",
                             "Я готов утвердить. Ваши финальные замечания или возражения? "
                             "Если согласны — напишите 'согласен'. Если нет — аргументируйте.")

                # Final round: all agents reply
                for name in cfg.agent_names:
                    try:
                        _chat_agent_reply(filepath, name)
                    except Exception as e:
                        _log.warning(f"/ok: agent {name} failed: {e}")

                # Auto-resolve: LLM generates summary
                _chat_auto_resolve(filepath)

                # Generate tasks
                _post_resolve(filepath)
                _auto_link_discussion(topic)

                # Archive after a delay so UI polling can see the resolved state
                time.sleep(15)
                _archive_discussion(topic)

                _log.info(f"/ok completed for {topic}")
            except Exception as e:
                _log.error(f"/ok failed for {topic}: {e}")
                from datetime import datetime
                now = datetime.now().strftime("%Y-%m-%d %H:%M")
                try:
                    with open(filepath, "a", encoding="utf-8") as f:
                        f.write(f"\n## @system ({now})\n\n/ok failed: {e}\n")
                except Exception:
                    pass

        threading.Thread(target=_run_ok, daemon=True).start()
        self._json_response({"ok": True, "msg": f"/ok started for '{topic}' — agents replying..."})

    def _api_discuss_write(self, body: dict):
        """Add a techlead message to a discussion."""
        topic = body.get("topic", "").strip()
        text = body.get("text", "").strip()
        if not topic or not text:
            self._json_response({"ok": False, "msg": "Topic and text required"})
            return
        try:
            # Check both main and archive dirs
            filepath = cfg.discuss_dir / f"{topic}.md"
            if not filepath.exists():
                filepath = cfg.discuss_dir / "archive" / f"{topic}.md"
            if not filepath.exists():
                self._json_response({"ok": False, "msg": f"Discussion '{topic}' not found"})
                return
            from datetime import datetime
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(f"\n## @techlead ({now})\n\n{text}\n")
            self._json_response({"ok": True, "msg": "Message added"})
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
            # Archive resolved discussion
            _archive_discussion(topic)
            self._json_response({"ok": True, "msg": f"Discussion '{topic}' resolved and archived"})
        except Exception as e:
            self._json_response({"ok": False, "msg": str(e)})

    def _api_discuss_reopen(self, body: dict):
        topic = body.get("topic", "").strip()
        if not topic:
            self._json_response({"ok": False, "msg": "Topic required"})
            return
        try:
            # Try archive first, then main dir
            filepath = cfg.discuss_dir / "archive" / f"{topic}.md"
            if not filepath.exists():
                filepath = cfg.discuss_dir / f"{topic}.md"
            if not filepath.exists():
                self._json_response({"ok": False, "msg": f"Discussion '{topic}' not found"})
                return
            from datetime import datetime
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(f"\n## @techlead ({now})\n\n\u0414\u0418\u0421\u041a\u0423\u0421\u0421\u0418\u042f \u041f\u0415\u0420\u0415\u041e\u0422\u041a\u0420\u042b\u0422\u0410\n\n")
            # Unarchive: move back to main dir
            _unarchive_discussion(topic)
            self._json_response({"ok": True, "msg": f"Discussion '{topic}' reopened"})
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

    def _api_config_save(self, body: dict):
        content = body.get("content", "")
        if not content.strip():
            self._json_response({"ok": False, "error": "Empty config"})
            return
        toml_path = cfg.root_dir / "forgerace.toml"
        # Validate TOML syntax before saving
        try:
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib  # type: ignore
            tomllib.loads(content)
        except Exception as e:
            self._json_response({"ok": False, "error": f"Invalid TOML: {e}"})
            return
        try:
            # Backup current config
            if toml_path.exists():
                backup = toml_path.with_suffix(".toml.bak")
                backup.write_text(toml_path.read_text(encoding="utf-8"), encoding="utf-8")
            toml_path.write_text(content, encoding="utf-8")
            # Reload config
            from .config import init_config
            init_config(config_path=toml_path)
            self._json_response({"ok": True})
        except Exception as e:
            self._json_response({"ok": False, "error": str(e)})

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
        self.send_header("Cache-Control", "no-cache")
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
