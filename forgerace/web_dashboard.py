"""Minimal web dashboard for ForgeRace — single-file HTTP server with SSE."""

import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from .config import cfg
from .tasks import parse_tasks

_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>ForgeRace Dashboard</title>
<style>
body{font-family:monospace;background:#1a1a2e;color:#e0e0e0;margin:2em}
h1{color:#00d4ff;margin:0}
.ts{color:#888;font-size:.85em}
.team{margin:1em 0;padding:.8em;background:#16213e;border-radius:6px}
.team-name{font-weight:bold;color:#f5c518;font-size:1.1em}
.bar-bg{background:#333;border-radius:3px;height:14px;width:200px;display:inline-block;vertical-align:middle}
.bar-fg{height:14px;border-radius:3px;background:#4caf50}
.task{margin:.2em 0 .2em 1.5em;font-size:.95em}
.done{color:#4caf50} .blocked{color:#e74c3c} .pending{color:#f5c518}
.coding{color:#ab47bc} .skip{color:#888} .review{color:#ff9800} .open{color:#aaa}
</style></head><body>
<h1>ForgeRace Dashboard</h1>
<div id="ts" class="ts"></div>
<div id="root"></div>
<script>
const icons={done:"\u2713",skip:"\u2298",in_progress:"\u26a1",review:"\u23f3",blocked:"\u2717",open:"\u25cb",failed:"\u274c"};
const cls={done:"done",skip:"skip",in_progress:"coding",review:"review",blocked:"blocked",open:"pending",failed:"blocked"};
function render(d){
  document.getElementById("ts").textContent="Updated: "+d.time;
  let h="";
  for(const[team,tasks]of Object.entries(d.teams)){
    const done=tasks.filter(t=>t.s==="done"||t.s==="skip").length;
    const total=tasks.length;
    const pct=total?Math.round(done/total*100):0;
    h+='<div class="team"><span class="team-name">'+team+'</span> '+done+'/'+total+
      ' <span class="bar-bg"><span class="bar-fg" style="width:'+pct+'%"></span></span>';
    for(const t of tasks){
      const base=t.s.split(":")[0]||"open";
      const ic=icons[base]||"?";
      const c=cls[base]||"open";
      h+='<div class="task '+c+'">'+ic+' '+t.id+' '+t.name+'</div>';
    }
    h+='</div>';
  }
  if(!Object.keys(d.teams).length) h='<p style="color:#888">No tasks with teams found.</p>';
  document.getElementById("root").innerHTML=h;
}
const es=new EventSource("/events");
es.onmessage=function(e){render(JSON.parse(e.data))};
es.onerror=function(){document.getElementById("ts").textContent="Connection lost, retrying..."};
</script></body></html>"""


def _build_snapshot():
    """Read TASKS.md and build JSON-serializable snapshot."""
    try:
        tasks = parse_tasks()
    except Exception:
        tasks = []
    teams = {}
    ungrouped = []
    for t in tasks:
        d = t.discussion or ""
        if d and d != "\u2014" and len(d) < 60 and "**" not in d:
            teams.setdefault(d, []).append({"id": t.id, "name": t.name, "s": t.status})
        else:
            ungrouped.append({"id": t.id, "name": t.name, "s": t.status})
    if ungrouped:
        teams["(ungrouped)"] = ungrouped
    return {"time": time.strftime("%H:%M:%S"), "teams": teams}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_HTML.encode("utf-8"))
        elif self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    data = json.dumps(_build_snapshot(), ensure_ascii=False)
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(5)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        pass  # silence request logs


def start_dashboard(host: str = "localhost", port: int = 8080):
    """Start the dashboard HTTP server (blocking)."""
    server = HTTPServer((host, port), _Handler)
    print(f"ForgeRace Dashboard: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()
