import json
import os
import socket
import subprocess
from contextlib import contextmanager

from ..io import sha256, utcnow
from ..safety import state_dir


def git_head(root):
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def file_digest(path):
    return sha256(path.read_bytes())


def _owner(lock):
    try:
        info = json.loads(lock.read_text(encoding="utf-8"))
        return f"pid {info.get('pid')} on {info.get('host')}, started {info.get('started_at')}"
    except (OSError, ValueError, AttributeError):
        return "owner unknown (lock written by an older version)"


@contextmanager
def mutation_lock(root):
    """Exclusive per-repository lock for apply/revert.

    The lock records its owner. A lock left by a killed process is never removed automatically:
    the owner's PID may have been reused, and the half-finished transaction must be inspected
    (`repo-airgap revert` resumes it) before the lock file is deleted by hand.
    """
    state = state_dir(root, create=True)
    lock = state / "mutation.lock"
    try:
        stream = lock.open("x", encoding="utf-8")
    except FileExistsError:
        raise ValueError(
            f"Another apply/revert holds .airgap/mutation.lock ({_owner(lock)}). If that process is "
            "no longer running, check .airgap/manifest.json, delete the lock file, then run "
            "`repo-airgap revert` to resume an interrupted transaction."
        ) from None
    try:
        with stream:
            json.dump({"pid": os.getpid(), "host": socket.gethostname(), "started_at": utcnow()}, stream)
            stream.flush()
        yield state
    finally:
        lock.unlink(missing_ok=True)
