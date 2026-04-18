"""Production web dashboard for ForgeRace — single-file HTTP server with SSE."""

import json, os, subprocess, threading, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path

from .config import cfg
from .tasks import parse_tasks

# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------

def _detect_litellm() -> bool:
    """Check if litellm proxy is reachable on localhost:4000."""
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:4000/health", timeout=2)
        return True
    except Exception:
        return False


def _detect_active_agents() -> list[dict]:
    """Scan log dir for recently-active agent logs (modified in last 120s)."""
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
            # filename pattern: TASK-NNN_agentname.log or agentname_TASK-NNN.log
            stem = logfile.stem
            parts = stem.split("_", 1)
            task_id = agent_name = ""
            for p in parts:
                if p.startswith("TASK-"):
                    task_id = p
                else:
                    agent_name = p
            if task_id and agent_name:
                agents.append({
                    "agent": agent_name,
                    "task": task_id,
                    "since": time.strftime("%H:%M:%S", time.localtime(mtime - 60)),
                })
        except OSError:
            continue
    return agents


def _count_processes() -> int:
    """Count running forgerace-related child processes."""
    try:
        out = subprocess.run(
            ["pgrep", "-c", "-f", "forgerace"],
            capture_output=True, text=True, timeout=3,
        )
        return max(0, int(out.stdout.strip()) - 1)  # exclude self
    except Exception:
        return 0


def _build_snapshot() -> dict:
    """Read TASKS.md and build JSON-serializable snapshot."""
    try:
        tasks = parse_tasks()
    except Exception:
        tasks = []

    teams: dict[str, dict] = {}
    ungrouped_tasks = []

    for t in tasks:
        d = t.discussion or ""
        # Group by discussion topic (team name)
        if d and d != "\u2014" and len(d) < 60 and "**" not in d:
            grp = teams.setdefault(d, {"done": 0, "total": 0, "skip": 0, "tasks": []})
        else:
            if not teams.get("(ungrouped)"):
                teams["(ungrouped)"] = {"done": 0, "total": 0, "skip": 0, "tasks": []}
            grp = teams["(ungrouped)"]

        status_base = t.status.split(":")[0] if t.status else "open"
        grp["total"] += 1
        if status_base == "done":
            grp["done"] += 1
        elif status_base == "skip":
            grp["skip"] += 1

        grp["tasks"].append({
            "id": t.id,
            "name": t.name,
            "status": status_base,
            "agent": t.agent if t.agent and t.agent != "\u2014" else "",
        })

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
    }


# ---------------------------------------------------------------------------
# HTML/CSS/JS — embedded as a single string
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ForgeRace Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;background:#1a1a2e;color:#e0e0e0;padding:1.2em;min-height:100vh}
a{color:#4ade80;text-decoration:none}
code,.mono{font-family:'JetBrains Mono',monospace,monospace}
.header{display:flex;flex-wrap:wrap;align-items:center;gap:1em;margin-bottom:1.5em;padding-bottom:1em;border-bottom:1px solid #2a2a4e}
.header h1{font-size:1.4em;color:#e0e0e0;font-weight:600}
.header h1 span{color:#4ade80}
.summary{display:flex;flex-wrap:wrap;gap:.8em;margin-left:auto}
.pill{background:#16213e;border:1px solid #2a2a4e;border-radius:20px;padding:.35em .9em;font-size:.85em;white-space:nowrap;transition:background .3s ease}
.pill.on{border-color:#4ade80;color:#4ade80} .pill.off{border-color:#ef4444;color:#ef4444} .pill.starting{border-color:#eab308;color:#eab308}
.ts{color:#6b7280;font-size:.8em;margin-left:auto;align-self:center}
.activity{margin-bottom:1.5em}
.activity-title{font-size:.95em;color:#a855f7;margin-bottom:.5em;font-weight:600}
.agent-card{display:inline-flex;align-items:center;gap:.5em;background:#16213e;border:1px solid #2a2a4e;border-radius:8px;padding:.4em .8em;margin:0 .5em .5em 0;font-size:.85em;transition:all .3s ease}
.agent-card .dot{width:8px;height:8px;border-radius:50%;background:#a855f7;animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.team{background:#16213e;border:1px solid #2a2a4e;border-radius:10px;margin-bottom:1em;overflow:hidden;transition:border-color .3s ease}
.team:hover{border-color:#4ade80}
.team-header{display:flex;align-items:center;gap:.8em;padding:.8em 1em;cursor:pointer;user-select:none;transition:background .3s ease}
.team-header:hover{background:#1e2748}
.team-name{font-weight:600;color:#eab308;font-size:1em;flex:1}
.team-stats{font-size:.85em;color:#6b7280}
.team-chevron{color:#6b7280;transition:transform .3s ease;font-size:.8em}
.team.collapsed .team-chevron{transform:rotate(-90deg)}
.team.collapsed .team-body{max-height:0;padding-top:0;padding-bottom:0;opacity:0}
.bar-wrap{width:140px;height:10px;background:#2a2a4e;border-radius:5px;overflow:hidden;flex-shrink:0}
.bar-fill{height:100%;border-radius:5px;background:linear-gradient(90deg,#22c55e,#4ade80);transition:width .6s ease;position:relative}
.bar-fill::after{content:'';position:absolute;top:0;left:0;right:0;bottom:0;background:linear-gradient(90deg,transparent 0%,rgba(255,255,255,.15) 50%,transparent 100%);animation:shimmer 2s infinite}
@keyframes shimmer{0%{transform:translateX(-100%)}100%{transform:translateX(100%)}}
.team-body{padding:0 1em;max-height:2000px;opacity:1;transition:max-height .4s ease,opacity .3s ease,padding .3s ease;overflow:hidden}
.task{display:flex;align-items:center;gap:.6em;padding:.4em .5em;border-radius:6px;font-size:.88em;transition:background .2s ease}
.task:hover{background:#1e2748} .task:last-child{margin-bottom:.6em}
.task-icon{width:1.4em;text-align:center;flex-shrink:0;font-size:1em}
.task-id{color:#6b7280;font-family:monospace;font-size:.85em;width:5.5em;flex-shrink:0}
.task-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.task-agent{font-size:.75em;color:#6b7280;background:#2a2a4e;border-radius:4px;padding:.15em .4em;flex-shrink:0}
.st-done .task-icon{color:#4ade80} .st-skip .task-icon{color:#6b7280}
.st-in_progress .task-icon{color:#a855f7;animation:pulse 1.5s infinite}
.st-review .task-icon{color:#f59e0b} .st-blocked .task-icon{color:#ef4444}
.st-open .task-icon{color:#eab308} .st-failed .task-icon{color:#ef4444}
.st-claimed .task-icon{color:#a855f7}
.empty{color:#6b7280;padding:2em;text-align:center}
@media(max-width:640px){body{padding:.6em}.header{flex-direction:column;align-items:flex-start;gap:.5em}.summary{margin-left:0;width:100%}.bar-wrap{width:100px}.task-name{max-width:45vw}}
</style></head><body>

<div class="header">
  <h1>Forge<span>Race</span></h1>
  <div class="summary" id="summary"></div>
  <div class="ts" id="ts">connecting...</div>
</div>
<div class="activity" id="activity"></div>
<div id="root"></div>

<script>
const ICONS={done:"\u2713",skip:"\u2298",in_progress:"\u26a1",review:"\u23f3",blocked:"\u2717",open:"\u25cb",failed:"\u274c",claimed:"\u26a1"};
const collapsed=new Set();

function renderSummary(d){
  const pct=d.total_all?Math.round(d.total_done/d.total_all*100):0;
  let h=`<span class="pill">${d.total_done}/${d.total_all} tasks (${pct}%)</span>`;
  h+=`<span class="pill">${d.processes} proc</span>`;
  if(_litellmPending&&!d.litellm){
    h+=`<span class="pill starting" style="cursor:pointer">⟳ starting...</span>`;
  }else{
    _litellmPending=false;
    h+=`<span class="pill ${d.litellm?'on':'off'}" style="cursor:pointer" onclick="toggleLitellm(${d.litellm})" title="Click to ${d.litellm?'stop':'start'}">LiteLLM ${d.litellm?'ON':'OFF'}</span>`;
  }
  document.getElementById("summary").innerHTML=h;
}

function renderActivity(agents){
  const el=document.getElementById("activity");
  if(!agents||!agents.length){el.innerHTML="";return}
  let h='<div class="activity-title">Agent Activity</div>';
  for(const a of agents){
    h+=`<span class="agent-card"><span class="dot"></span><b>${a.agent}</b> → ${a.task}</span>`;
  }
  el.innerHTML=h;
}

let _litellmPending=false;
function toggleLitellm(isOn){
  if(_litellmPending)return;
  _litellmPending=true;
  const pill=document.querySelector('.pill.on,.pill.off');
  if(pill){pill.className='pill starting';pill.innerHTML='⟳ LiteLLM starting...';}
  const action=isOn?'stop':'start';
  fetch('/api/litellm/'+action).then(r=>r.json()).then(d=>{
    if(action==='stop'&&pill){pill.className='pill off';pill.innerHTML='LiteLLM OFF';_litellmPending=false;}
  }).catch(e=>{console.error(e);_litellmPending=false;});
}

function renderTeams(teams){
  const root=document.getElementById("root");
  if(!Object.keys(teams).length){root.innerHTML='<div class="empty">No tasks found.</div>';return}

  // Preserve scroll
  const ids=Object.keys(teams);
  let h="";
  for(const name of ids){
    const t=teams[name];
    const pct=t.total?Math.round((t.done+t.skip)/t.total*100):0;
    const isCollapsed=collapsed.has(name);
    h+=`<div class="team${isCollapsed?' collapsed':''}" data-team="${name}">`;
    h+=`<div class="team-header" onclick="toggleTeam('${name.replace(/'/g,"\\'")}')">`;
    h+=`<span class="team-chevron">&#9660;</span>`;
    h+=`<span class="team-name">${esc(name)}</span>`;
    h+=`<span class="team-stats">${t.done+t.skip}/${t.total}</span>`;
    h+=`<div class="bar-wrap"><div class="bar-fill" style="width:${pct}%"></div></div>`;
    h+=`</div>`;  // team-header
    h+=`<div class="team-body">`;
    for(const task of t.tasks){
      const ic=ICONS[task.status]||"\u25cb";
      h+=`<div class="task st-${task.status}">`;
      h+=`<span class="task-icon">${ic}</span>`;
      h+=`<span class="task-id mono">${task.id}</span>`;
      h+=`<span class="task-name">${esc(task.name)}</span>`;
      if(task.agent)h+=`<span class="task-agent">${esc(task.agent)}</span>`;
      h+=`</div>`;
    }
    h+=`</div></div>`;  // team-body, team
  }
  root.innerHTML=h;
}

function toggleTeam(name){
  if(collapsed.has(name))collapsed.delete(name);else collapsed.add(name);
  const el=document.querySelector(`[data-team="${CSS.escape(name)}"]`);
  if(el)el.classList.toggle("collapsed");
}

function esc(s){const d=document.createElement("div");d.textContent=s;return d.innerHTML}

function render(d){
  document.getElementById("ts").textContent="Updated: "+d.timestamp;
  renderSummary(d);
  renderActivity(d.active_agents);
  renderTeams(d.teams);
}

window.addEventListener('load',function(){
  const es=new EventSource("/events");
  es.onmessage=function(e){try{render(JSON.parse(e.data))}catch(err){console.error(err)}};
  es.onerror=function(){document.getElementById("ts").textContent="Connection lost, retrying..."};
});
</script></body></html>"""


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress request logging

    def do_GET(self):
        try:
            self._handle_get()
        except BrokenPipeError:
            pass

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
        else:
            self.send_error(404)

    def _api_litellm_start(self):
        """Start LiteLLM proxy (non-blocking)."""
        from pathlib import Path
        import subprocess
        litellm_bin = Path.home() / ".local/share/pipx/venvs/litellm/bin/litellm"
        config_file = cfg.root_dir / "litellm_config.yaml"
        if not litellm_bin.exists() or not config_file.exists():
            self._json_response({"ok": False, "error": "LiteLLM not installed"})
            return
        clean_env = {k: v for k, v in os.environ.items()
                     if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
        clean_env["no_proxy"] = "127.0.0.1,localhost"
        clean_env["NO_PROXY"] = "127.0.0.1,localhost"
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            [str(litellm_bin), "--config", str(config_file), "--port", "4000", "--host", "127.0.0.1"],
            stdout=open(cfg.log_dir / "litellm.log", "w"),
            stderr=subprocess.STDOUT, env=clean_env,
        )
        self._json_response({"ok": True, "status": "starting (check in 10s)"})

    def _api_litellm_stop(self):
        """Stop LiteLLM proxy."""
        import subprocess
        subprocess.run(["pkill", "-f", "litellm.*--port.*4000"], capture_output=True)
        time.sleep(1)
        self._json_response({"ok": True, "status": "stopped"})

    def _json_response(self, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # silence request logs


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
