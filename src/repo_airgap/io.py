import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def sha256(data: bytes):
    return hashlib.sha256(data).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def atomic_write(path: Path, data: bytes):
    """Replace a file atomically on its own volume; preserve its permission bits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode if path.exists() else None
    fd, tmp = tempfile.mkstemp(prefix=".airgap-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def dump_json(path, value):
    atomic_write(Path(path), (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def redact(text):
    # Outputs can be read by an Agent; do not propagate obvious credentials.
    text = re.sub(r"(?i)([?&](?:token|key|api_key|secret|password)=)[^&\s'\"<>]+", r"\1[REDACTED]", text)
    text = re.sub(r"(https?://)[^/\s:@]+:[^/@\s]+@", r"\1[REDACTED]@", text)
    text = re.sub(
        r"(?i)((?:[\w.-]*(?:token|secret|password|api[_-]?key|access[_-]?key)[\w.-]*)"
        r"[\"']?\s*[:=]\s*[\"']?)([^\s\"',;}]+)",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16})\b", "[REDACTED]", text)
    return text
