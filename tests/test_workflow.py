import json
import shutil
import subprocess
import sys

import pytest
from conftest import reviewed

from repo_airgap.cli import main
from repo_airgap.io import dump_json, load_json
from repo_airgap.patcher.patch_manager import apply_plan, save_plan
from repo_airgap.patcher.verifier import verify


def test_cli_six_commands_end_to_end(target, tmp_path, capsys):
    original_license = (target / "LICENSE").read_bytes()
    assert main(["scan", str(target)]) == 0
    state = target / ".airgap"
    scan_report = load_json(state / "audit_candidates.json")
    _, decisions = reviewed(target, scan_report)
    decisions_path = tmp_path / "decisions.json"
    dump_json(decisions_path, decisions)
    assert main(["plan", str(target), "--decisions", str(decisions_path)]) == 0
    assert main(["report", str(target)]) == 0
    assert "No applied transaction" in (state / "AIRGAP_REPORT.md").read_text("utf-8")
    assert main(["apply", str(target), "--plan", str(state / "plan.json")]) == 0
    assert (
        main(
            [
                "verify",
                str(target),
                "--command",
                json.dumps(
                    [
                        sys.executable,
                        "-c",
                        "import telemetry; assert telemetry.send_usage_telemetry({}) == {'status':'ok','sanitized':True}",
                    ]
                ),
            ]
        )
        == 0
    )
    assert main(["report", str(target)]) == 0
    content = (state / "AIRGAP_REPORT.md").read_text("utf-8")
    assert "checks_passed" in content and "api.openai.com" in content
    assert "fixture-not-a-real-credential" not in content
    assert main(["revert", str(target)]) == 0
    assert (target / "LICENSE").read_bytes() == original_license
    assert main(["report", str(target)]) == 0
    assert "STALE" in (state / "AIRGAP_REPORT.md").read_text("utf-8")


def test_actual_node_return_contract_with_process_network_guard(target, planned):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node not installed")
    command = [node, "--experimental-strip-types", "offline-check.mjs"]
    before = subprocess.run(command, cwd=target, capture_output=True, timeout=30)
    assert before.returncode != 0
    assert b"network-disabled-fixture" in before.stderr
    apply_plan(target, save_plan(target, planned))
    result = verify(target, [command])
    assert result["status"] == "checks_passed", result
    assert result["airgap_verified"] is False
    assert "core API retained" in result["checks"][-1]["stdout"]


def test_python_socket_guard_after_patch(target, planned):
    apply_plan(target, save_plan(target, planned))
    command = [
        sys.executable,
        "-c",
        "import socket, urllib.request; "
        "socket.socket=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('network disabled')); "
        "import telemetry; assert telemetry.send_usage_telemetry({})['sanitized']",
    ]
    assert verify(target, [command])["status"] == "checks_passed"


def test_verify_without_checks_cannot_claim_airgap(target):
    result = verify(target)
    assert result["status"] == "not_verified"
    assert result["airgap_verified"] is False


def test_failed_checks_and_observation_import(target, tmp_path):
    observations = tmp_path / "observations.json"
    dump_json(
        observations,
        {
            "coverage": "HTTP only",
            "connections": [{"destination": "api.openai.com", "protocol": "https", "outcome": "blocked"}],
        },
    )
    result = verify(target, [[sys.executable, "-c", "raise SystemExit(1)"]], observations)
    assert result["status"] == "failed"
    assert result["runtime"]["status"] == "evidence_imported"
    assert result["runtime"]["connections"][0]["destination"] == "api.openai.com"


def test_command_timeout_and_bad_command(target):
    result = verify(target, [[sys.executable, "-c", "import time; time.sleep(2)"]], timeout=1)
    assert result["status"] == "failed"
    with pytest.raises(ValueError, match="JSON array"):
        verify(target, ['"npm test"'])


def test_scan_cannot_overwrite_source(target):
    source = target / "telemetry.py"
    data = source.read_bytes()
    assert main(["scan", str(target), "--output", str(source)]) == 2
    assert source.read_bytes() == data


def test_commands_that_modify_source_invalidate_verification(target, planned):
    apply_plan(target, save_plan(target, planned))
    result = verify(
        target,
        [[sys.executable, "-c", "from pathlib import Path; Path('telemetry.py').write_text('# replaced')"]],
    )
    assert result["status"] == "failed"
    assert any(c["kind"] == "source_changed_during_checks" for c in result["checks"])


def test_active_transaction_keeps_original_scan(target, planned):
    assert main(["scan", str(target)]) == 0
    data = (target / ".airgap/audit_candidates.json").read_bytes()
    apply_plan(target, save_plan(target, planned))
    assert main(["scan", str(target)]) == 2
    assert (target / ".airgap/audit_candidates.json").read_bytes() == data


def test_rescan_does_not_reuse_old_candidate_decisions(target, planned):
    assert main(["scan", str(target)]) == 0
    apply_plan(target, save_plan(target, planned))
    assert main(["revert", str(target)]) == 0
    assert main(["scan", str(target)]) == 0
    assert main(["report", str(target)]) == 0
    content = (target / ".airgap/AIRGAP_REPORT.md").read_text("utf-8")
    assert "Optional fixture heartbeat; return contract inspected" not in content


def test_symlinks_not_read_or_modified(target, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text('fetch("https://private.evil.io")', encoding="utf-8")
    link = target / "linked.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation requires Windows developer mode or privilege")
    from repo_airgap.engine import scan
    from repo_airgap.safety import safe_path

    assert not any(c.file_path == "linked.py" for c in scan(target).candidates)
    with pytest.raises(ValueError, match="linked"):
        safe_path(target, "linked.py")
