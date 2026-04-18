"""LiteLLM health monitor.

Periodically checks the health endpoint of the LiteLLM proxy (default
http://127.0.0.1:4000/health). If the check fails, it logs a warning.
The monitor runs in a daemon thread and sleeps 30 seconds between checks.
"""

import threading
import time
import urllib.request

from .utils import log

def _run_monitor():
    """Continuously poll the LiteLLM health endpoint."""
    url = "http://127.0.0.1:4000/health"
    headers = {"Authorization": "Bearer fr-local-dev"}
    while True:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5):
                # Successful response – nothing to do.
                pass
        except Exception as e:
            log.warning(f"LiteLLM health check failed: {e}")
            # Here you could add restart logic for the proxy if needed.
        time.sleep(30)

def start_health_monitor():
    """Start the health monitor in a daemon thread."""
    thread = threading.Thread(target=_run_monitor, daemon=True, name="LiteLLMHealthMonitor")
    thread.start()
    return thread
