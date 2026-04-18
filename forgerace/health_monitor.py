"""Health monitor for LiteLLM.

Runs in a background daemon thread and periodically calls
``LiteLLM.health_check()`` to ensure the proxy is alive.

The monitor is started automatically from ``forgerace.agents`` on import.
"""

import threading
import time
from .utils import log

# Interval between health checks (seconds). Must be a fixed value per task requirements.
_HEALTH_CHECK_INTERVAL = 30


def _run_monitor(stop_event: threading.Event):
    """Thread target that performs the health checks."""
    # Import lazily to avoid import errors if LiteLLM is not installed.
    try:
        from litellm import LiteLLM  # type: ignore
    except Exception as exc:  # pragma: no cover
        log.warning(f"LiteLLM not available for health monitoring: {exc}")
        return

    while not stop_event.is_set():
        try:
            LiteLLM.health_check()
            log.debug("[health_monitor] LiteLLM health_check OK")
        except Exception as e:  # pragma: no cover
            log.error(f"[health_monitor] LiteLLM health_check failed: {e}")
        # Wait for the next interval or exit early if stopped.
        stop_event.wait(_HEALTH_CHECK_INTERVAL)


def start_health_monitor():
    """Create and start the health‑monitor thread.

    Returns the ``threading.Thread`` instance (use ``stop()`` on the returned
    ``Event`` to terminate it if needed).
    """
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_run_monitor,
        args=(stop_event,),
        name="LiteLLMHealthMonitor",
        daemon=True,
    )
    thread.start()
    # Attach the stop event to the thread object for external control.
    thread.stop_event = stop_event  # type: ignore[attr-defined]
    return thread
