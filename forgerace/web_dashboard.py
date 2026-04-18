"""Production web dashboard for ForgeRace — single-file HTTP server with SSE."""

import glob as _glob
import json, os, re, subprocess, threading, time
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
    """List team names from discussions directory."""
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
    """Read recent TASK events from log files."""
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
    }


# ---------------------------------------------------------------------------
# HTML/CSS/JS
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ForgeRace Dashboard</title>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:system-ui,sans-serif;background:#1a1a2e;color:#e0e0e0;padding:1.2em;min-height:100vh}a{color:#4ade80}.mono{font-family:'JetBrains Mono',monospace}
.header{display:flex;flex-wrap:wrap;align-items:center;gap:1em;margin-bottom:1.5em;padding-bottom:1em;border-bottom:1px solid #2a2a4e}.header h1{font-size:1.4em;font-weight:600}.header h1 span{color:#4ade80}
.summary{display:flex;flex-wrap:wrap;gap:.8em;margin-left:auto}.pill{background:#16213e;border:1px solid #2a2a4e;border-radius:20px;padding:.35em .9em;font-size:.85em;white-space:nowrap;cursor:pointer}.pill:hover{background:#1e2748}
.pill.on{border-color:#4ade80;color:#4ade80}.pill.off,.pill.danger{border-color:#ef4444;color:#ef4444}.pill.starting{border-color:#eab308;color:#eab308}.pill.action{border-color:#60a5fa;color:#60a5fa}
.ts{color:#6b7280;font-size:.8em;margin-left:auto}.controls{display:flex;flex-wrap:wrap;align-items:center;gap:.6em;margin-bottom:1.2em}.controls select{background:#16213e;color:#e0e0e0;border:1px solid #2a2a4e;border-radius:8px;padding:.35em .7em;font-size:.85em}
.activity{margin-bottom:1.5em}.activity-title{font-size:.95em;color:#a855f7;margin-bottom:.5em;font-weight:600}
.agent-card{display:inline-flex;align-items:center;gap:.5em;background:#16213e;border:1px solid #2a2a4e;border-radius:8px;padding:.4em .8em;margin:0 .5em .5em 0;font-size:.85em}.agent-card .dot{width:8px;height:8px;border-radius:50%;background:#a855f7;animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}.team{background:#16213e;border:1px solid #2a2a4e;border-radius:10px;margin-bottom:1em;overflow:hidden}.team:hover{border-color:#4ade80}
.team-header{display:flex;align-items:center;gap:.8em;padding:.8em 1em;cursor:pointer;user-select:none}.team-header:hover{background:#1e2748}.team-name{font-weight:600;color:#eab308;flex:1}.team-stats{font-size:.85em;color:#6b7280}
.team-chevron{color:#6b7280;transition:transform .3s;font-size:.8em}.team.collapsed .team-chevron{transform:rotate(-90deg)}.team.collapsed .team-body{max-height:0;padding:0;opacity:0}
.bar-wrap{width:140px;height:10px;background:#2a2a4e;border-radius:5px;overflow:hidden;flex-shrink:0}.bar-fill{height:100%;border-radius:5px;background:linear-gradient(90deg,#22c55e,#4ade80);transition:width .6s;position:relative}
.bar-fill::after{content:'';position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,.15) 50%,transparent);animation:shimmer 2s infinite}@keyframes shimmer{0%{transform:translateX(-100%)}100%{transform:translateX(100%)}}
.team-body{padding:0 1em;max-height:2000px;opacity:1;transition:max-height .4s,opacity .3s,padding .3s;overflow:hidden}
.task{display:flex;align-items:center;gap:.6em;padding:.4em .5em;border-radius:6px;font-size:.88em}.task:hover{background:#1e2748}.task:last-child{margin-bottom:.6em}
.task-icon{width:1.4em;text-align:center;flex-shrink:0}.task-id{color:#6b7280;font-family:monospace;font-size:.85em;width:5.5em;flex-shrink:0}.task-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.task-agent{font-size:.75em;color:#6b7280;background:#2a2a4e;border-radius:4px;padding:.15em .4em;flex-shrink:0}
.st-done .task-icon{color:#4ade80}.st-skip .task-icon{color:#6b7280}.st-in_progress .task-icon{color:#a855f7;animation:pulse 1.5s infinite}.st-review .task-icon{color:#f59e0b}.st-blocked .task-icon,.st-failed .task-icon{color:#ef4444}.st-open .task-icon{color:#eab308}.st-claimed .task-icon{color:#a855f7}
.empty{color:#6b7280;padding:2em;text-align:center}.history{background:#16213e;border:1px solid #2a2a4e;border-radius:10px;margin-top:1.2em;padding:.8em 1em;max-height:300px;overflow-y:auto}
.history-title{font-size:.95em;color:#60a5fa;margin-bottom:.5em;font-weight:600}.history-line{font-family:'JetBrains Mono',monospace;font-size:.78em;padding:.15em 0;border-bottom:1px solid #2a2a3e;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hk-done{color:#4ade80}.hk-blocked{color:#ef4444}.hk-review{color:#f59e0b}.hk-coding{color:#a855f7}.hk-info{color:#6b7280}
.toast{position:fixed;top:1em;right:1em;background:#16213e;border:1px solid #4ade80;border-radius:8px;padding:.6em 1.2em;font-size:.85em;color:#4ade80;opacity:0;transition:opacity .3s;z-index:999;pointer-events:none}.toast.err{border-color:#ef4444;color:#ef4444}.toast.show{opacity:1}
@media(max-width:640px){body{padding:.6em}.header{flex-direction:column;gap:.5em}.summary{margin-left:0;width:100%}.bar-wrap{width:100px}.task-name{max-width:45vw}}</style></head><body>

<div class="header">
  <h1>Forge<span>Race</span></h1>
  <div class="summary" id="summary"></div>
  <div class="ts" id="ts">connecting...</div>
</div>
<div class="controls" id="controls"></div>
<div class="activity" id="activity"></div>
<div id="root"></div>
<div class="history" id="historyPanel" style="display:none"><div class="history-title">Event Log</div><div id="historyBody"></div></div>
<div class="toast" id="toast"></div>

<script>const ICONS={done:"\u2713",skip:"\u2298",in_progress:"\u26a1",review:"\u23f3",blocked:"\u2717",open:"\u25cb",failed:"\u274c",claimed:"\u26a1"};
const collapsed=new Set();let _teamNames=[],_litellmPending=false;
const $=id=>document.getElementById(id);
function esc(s){const d=document.createElement("div");d.textContent=s;return d.innerHTML}
function toast(msg,err){const t=$("toast");t.textContent=msg;t.className=err?"toast err show":"toast show";setTimeout(()=>t.className="toast",3000)}
function apiPost(u,b){fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b||{})}).then(r=>r.json()).then(d=>toast(d.msg||d.status||"OK",!d.ok)).catch(e=>toast("Error: "+e,true))}
function doRun(){const t=$("teamSel").value;t?apiPost("/api/run",{team:t}):toast("Select a team first",true)}
function doStop(){apiPost("/api/stop")}
function doRetry(){const t=$("teamSel").value;t?apiPost("/api/retry",{team:t}):toast("Select a team first",true)}
function toggleLitellm(on){if(_litellmPending)return;_litellmPending=true;fetch('/api/litellm/'+(on?'stop':'start')).then(r=>r.json()).then(()=>{if(on)_litellmPending=false}).catch(()=>_litellmPending=false)}
function toggleTeam(n){collapsed.has(n)?collapsed.delete(n):collapsed.add(n);const e=document.querySelector(`[data-team="${CSS.escape(n)}"]`);if(e)e.classList.toggle("collapsed")}
function renderControls(){let o=_teamNames.map(t=>`<option value="${esc(t)}">${esc(t)}</option>`).join('');
$("controls").innerHTML=`<select id="teamSel"><option value="">-- team --</option>${o}</select><span class="pill action" onclick="doRun()">Run Team</span><span class="pill danger" onclick="doStop()">Stop All</span><span class="pill action" onclick="doRetry()">Retry Failed</span>`}
function renderSummary(d){const p=d.total_all?Math.round(d.total_done/d.total_all*100):0;let h=`<span class="pill">${d.total_done}/${d.total_all} (${p}%)</span><span class="pill">${d.processes} proc</span>`;
if(_litellmPending&&!d.litellm)h+=`<span class="pill starting">starting...</span>`;
else{_litellmPending=false;h+=`<span class="pill ${d.litellm?'on':'off'}" onclick="toggleLitellm(${d.litellm})">LiteLLM ${d.litellm?'ON':'OFF'}</span>`}
$("summary").innerHTML=h}
function renderActivity(a){if(!a||!a.length){$("activity").innerHTML="";return}
$("activity").innerHTML='<div class="activity-title">Agent Activity</div>'+a.map(x=>`<span class="agent-card"><span class="dot"></span><b>${x.agent}</b> &rarr; ${x.task}</span>`).join('')}
function renderTeams(teams){const r=$("root");if(!Object.keys(teams).length){r.innerHTML='<div class="empty">No tasks found.</div>';return}let h="";
for(const n of Object.keys(teams)){const t=teams[n],p=t.total?Math.round((t.done+t.skip)/t.total*100):0,c=collapsed.has(n);
h+=`<div class="team${c?' collapsed':''}" data-team="${n}"><div class="team-header" onclick="toggleTeam('${n.replace(/'/g,"\\'")}')"><span class="team-chevron">&#9660;</span><span class="team-name">${esc(n)}</span><span class="team-stats">${t.done+t.skip}/${t.total}</span><div class="bar-wrap"><div class="bar-fill" style="width:${p}%"></div></div></div><div class="team-body">`;
for(const tk of t.tasks){h+=`<div class="task st-${tk.status}"><span class="task-icon">${ICONS[tk.status]||"\u25cb"}</span><span class="task-id mono">${tk.id}</span><span class="task-name">${esc(tk.name)}</span>${tk.agent?`<span class="task-agent">${esc(tk.agent)}</span>`:''}</div>`}
h+=`</div></div>`}r.innerHTML=h}
function renderHistory(ev){const p=$("historyPanel"),b=$("historyBody");if(!ev||!ev.length){p.style.display="none";return}
p.style.display="block";b.innerHTML=ev.map(e=>`<div class="history-line hk-${e.kind}">${esc(e.text)}</div>`).join('')}
function render(d){$("ts").textContent="Updated: "+d.timestamp;
if(JSON.stringify(_teamNames)!==JSON.stringify(d.team_names||[])){_teamNames=d.team_names||[];renderControls()}
renderSummary(d);renderActivity(d.active_agents);renderTeams(d.teams);renderHistory(d.history)}
window.addEventListener('load',()=>{const es=new EventSource("/events");es.onmessage=e=>{try{render(JSON.parse(e.data))}catch(err){console.error(err)}};es.onerror=()=>$("ts").textContent="Connection lost, retrying..."});
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
            self._json_response({"ok": True, "events": _read_history(50)})
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
        else:
            self.send_error(404)

    # -- API handlers --

    def _api_run(self, body: dict):
        team = body.get("team", "")
        if not team:
            self._json_response({"ok": False, "msg": "No team specified"})
            return
        # Find forgerace.py relative to this module
        fr_py = Path(__file__).resolve().parent.parent / "forgerace.py"
        cmd = ["python3", str(fr_py), "run", "--auto", "--team", team]
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(cmd, cwd=str(cfg.root_dir),
            stdout=open(cfg.log_dir / "web_run.log", "a"),
            stderr=subprocess.STDOUT, start_new_session=True)
        self._json_response({"ok": True, "msg": f"Started run for {team}"})

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
        body = json.dumps(data).encode("utf-8")
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
