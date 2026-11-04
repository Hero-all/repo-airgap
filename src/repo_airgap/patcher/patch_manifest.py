import subprocess
from contextlib import contextmanager

from ..io import sha256
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


@contextmanager
def mutation_lock(root):
    state = state_dir(root, create=True)
    lock = state / "mutation.lock"
    try:
        stream = lock.open("x", encoding="utf-8")
    except FileExistsError:
        raise ValueError(
            "Another mutation is running, or a stale .airgap/mutation.lock needs inspection"
        ) from None
    try:
        with stream:
            stream.write("repo-airgap mutation in progress\n")
            yield state
    finally:
        lock.unlink(missing_ok=True)
