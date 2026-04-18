"""Checkpoint system: snapshots after each task, rollback to previous state."""

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from forgerace.config import cfg
from forgerace.utils import log

@dataclass
class Checkpoint:
    """Snapshot of system state after a task."""
    task_id: str
    timestamp: float
    git_sha: str
    metrics: dict  # code_lines, binary_size, etc.
    agent: str
    branch: str

class CheckpointStore:
    """Thread-safe storage for checkpoints."""

    def __init__(self, store_path: Path):
        self._store_path = store_path
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def snapshot(self, task_id: str, git_sha: str, metrics: dict, agent: str, branch: str) -> None:
        """Create a checkpoint after task completion."""
        import time
        checkpoint = Checkpoint(
            task_id=task_id,
            timestamp=time.time(),
            git_sha=git_sha,
            metrics=metrics,
            agent=agent,
            branch=branch,
        )
        with self._lock:
            data = self._load()
            data[task_id] = checkpoint
            self._save(data)
        log.info(f"📸 Checkpoint created for {task_id} (sha={git_sha[:7]})")

    def rollback(self, task_id: str) -> Optional[Checkpoint]:
        """Rollback to the state after the specified task."""
        with self._lock:
            data = self._load()
            checkpoint = data.get(task_id)
            if checkpoint:
                # Rollback: checkout the git commit
                import subprocess
                result = subprocess.run(
                    ["git", "checkout", checkpoint.git_sha],
                    cwd=cfg.root_dir, capture_output=True, text=True,
                )
                if result.returncode == 0:
                    log.info(f"🔄 Rolled back to {task_id} (sha={checkpoint.git_sha[:7]})")
                    return checkpoint
                else:
                    log.error(f"✗ Rollback failed: {result.stderr}")
                    return None
            log.error(f"✗ Checkpoint not found for {task_id}")
            return None

    def _load(self) -> dict[str, Checkpoint]:
        """Load checkpoints from JSON file."""
        if not self._store_path.exists():
            return {}
        try:
            data = json.loads(self._store_path.read_text(encoding="utf-8"))
            return {k: Checkpoint(**v) for k, v in data.items()}
        except Exception as e:
            log.error(f"CheckpointStore: failed to load {self._store_path}: {e}")
            return {}

    def _save(self, data: dict[str, Checkpoint]) -> None:
        """Save checkpoints to JSON file."""
        serialized = {k: {
            "task_id": v.task_id,
            "timestamp": v.timestamp,
            "git_sha": v.git_sha,
            "metrics": v.metrics,
            "agent": v.agent,
            "branch": v.branch,
        } for k, v in data.items()}
        self._store_path.write_text(json.dumps(serialized, indent=2), encoding="utf-8")

# Global store instance
store = CheckpointStore(cfg.root_dir / ".forgerace" / "checkpoints.json")

forgerace/pipeline.py
````python
<<<<<<< SEARCH
    _log_total_cost(task.id, all_results)
    cleanup_worktrees(all_results)
    return True
