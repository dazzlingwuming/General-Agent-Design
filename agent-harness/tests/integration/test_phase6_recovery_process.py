from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_harness.checkpoints.store import CheckpointStore


pytestmark = pytest.mark.recovery_process


def test_checkpoint_survives_hard_process_exit(tmp_path: Path) -> None:
    """Commit a checkpoint in a child process, hard-exit, and recover it in a new process."""
    script = """
from pathlib import Path
import os
from agent_harness.checkpoints.models import CheckpointEnvelope, DurableTurnStatus, ResumePoint
from agent_harness.checkpoints.store import CheckpointStore
from agent_harness.utils.time import iso_now
path = Path(os.environ['RUNTIME_DB'])
store = CheckpointStore(path)
item = CheckpointEnvelope(1, 'cp-crash', 'thread-crash', 'thread-crash', 'turn-crash', 'root', 1, 0, ResumePoint.AFTER_MODEL, DurableTurnStatus.RUNNING, '0.1.0', 'cfg', 'fake', 'fake', {'messages': []}, created_at=iso_now())
store.save(item)
os._exit(91)
"""
    env = dict(os.environ, RUNTIME_DB=str(tmp_path / "runtime.sqlite3"))
    completed = subprocess.run([sys.executable, "-c", script], env=env, check=False)
    assert completed.returncode == 91
    recovered = CheckpointStore(tmp_path / "runtime.sqlite3").latest("thread-crash")
    assert recovered is not None
    assert recovered.resume_point.value == "after_model"
