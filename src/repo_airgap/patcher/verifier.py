import ast
import json
import subprocess

from ..io import dump_json, load_json, redact, sha256, utcnow
from ..safety import safe_path, state_dir


def verify(root, commands=(), observations=None, timeout=120):
    """Only run commands explicitly supplied by the caller; no inferred build/install scripts."""
    state = state_dir(root, create=True)
    manifest = load_json(state / "manifest.json") if (state / "manifest.json").exists() else None
    checks, hashes = [], {}
    for f in (manifest or {}).get("files", []):
        path = safe_path(root, f["file_path"])
        if manifest["status"] == "reverted" and f.get("operation") == "create":
            hashes[f["file_path"]] = "ABSENT"
            checks.append(
                {
                    "kind": "snapshot",
                    "file": f["file_path"],
                    "status": "passed" if not path.exists() else "failed",
                }
            )
            continue
        raw = path.read_bytes()
        digest = sha256(raw)
        hashes[f["file_path"]] = digest
        expected = f["before_sha256"] if manifest["status"] == "reverted" else f["after_sha256"]
        checks.append(
            {
                "kind": "snapshot",
                "file": f["file_path"],
                "status": "passed" if digest == expected else "failed",
            }
        )
        if path.suffix == ".py":
            try:
                ast.parse(raw.decode("utf-8-sig"), filename=f["file_path"])
                checks.append({"kind": "python_syntax", "file": f["file_path"], "status": "passed"})
            except (SyntaxError, UnicodeError) as e:
                checks.append(
                    {"kind": "python_syntax", "file": f["file_path"], "status": "failed", "error": str(e)}
                )
    for command in commands:
        argv = json.loads(command) if isinstance(command, str) else command
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError("--command must be a JSON array of argv strings")
        if argv[0].lower().endswith((".bat", ".cmd")):
            raise ValueError("Use an explicit interpreter executable for batch scripts")
        try:
            result = subprocess.run(argv, cwd=root, capture_output=True, timeout=timeout, shell=False)
            checks.append(
                {
                    "kind": "user_command",
                    "argv": [redact(a) for a in argv],
                    "status": "passed" if result.returncode == 0 else "failed",
                    "exit_code": result.returncode,
                    "stdout": redact(result.stdout.decode("utf-8", errors="replace")[-16000:]),
                    "stderr": redact(result.stderr.decode("utf-8", errors="replace")[-16000:]),
                }
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            checks.append(
                {
                    "kind": "user_command",
                    "argv": [redact(a) for a in argv],
                    "status": "failed",
                    "error": redact(str(e)),
                }
            )
    runtime = {
        "status": "not_verified",
        "connections": [],
        "reason": "No runtime observation file supplied. No OS network isolation was performed.",
    }
    if observations:
        data = load_json(observations)
        if not isinstance(data.get("connections"), list):
            raise ValueError("Observation JSON requires a connections array")
        records = []
        for c in data["connections"]:
            if not isinstance(c, dict) or not isinstance(c.get("destination"), str):
                raise ValueError("Every connection needs a destination string")
            records.append(
                {
                    k: redact(str(c[k]))
                    for k in (
                        "destination",
                        "port",
                        "protocol",
                        "process",
                        "timestamp",
                        "scenario",
                        "outcome",
                    )
                    if k in c
                }
            )
        runtime = {
            "status": "evidence_imported",
            "connections": records,
            "source": str(observations),
            "coverage": redact(str(data.get("coverage", "unknown"))),
            "reason": "Caller-supplied observations; not a proof of zero outbound traffic.",
        }
    for filename, digest in hashes.items():
        path = safe_path(root, filename)
        matches = (
            not path.exists()
            if digest == "ABSENT"
            else path.is_file() and sha256(path.read_bytes()) == digest
        )
        if not matches:
            checks.append({"kind": "source_changed_during_checks", "file": filename, "status": "failed"})
    result = {
        "verified_at": utcnow(),
        "repo_path": str(root),
        "scan_timestamp": load_json(state / "audit_candidates.json").get("scan_timestamp")
        if (state / "audit_candidates.json").exists()
        else None,
        "manifest_id": manifest.get("id") if manifest else None,
        "manifest_status": manifest.get("status") if manifest else None,
        "file_hashes": hashes,
        "checks": checks,
        "runtime": runtime,
        "status": "failed"
        if any(c["status"] == "failed" for c in checks)
        else "checks_passed"
        if commands
        else "not_verified",
        "airgap_verified": False,
        "limitations": [
            "verify runs supplied commands; it does not disconnect the host network.",
            "Non-Python syntax/build and offline critical flows require supplied commands.",
            "Untriggered code paths and traffic outside the observation backend remain uncovered.",
        ],
    }
    dump_json(state / "verification.json", result)
    return result
