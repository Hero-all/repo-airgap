import base64

import pytest
from conftest import reviewed, select_disable

from repo_airgap.engine import scan
from repo_airgap.io import dump_json, sha256
from repo_airgap.patcher.patch_manager import apply_plan, create_plan, revert, save_plan
from repo_airgap.safety import safe_path


def test_dirty_user_baseline_and_exact_roundtrip(target, planned):
    original = {f["file_path"]: (target / f["file_path"]).read_bytes() for f in planned["files"]}
    unrelated = target / "unrelated.txt"
    unrelated.write_text("user content not committed", encoding="utf-8")
    path = save_plan(target, planned)
    apply_plan(target, path)
    assert b"sanitized" in (target / "telemetry.py").read_bytes()
    assert "api.openai.com" in (target / "src/api/openai.ts").read_text("utf-8")
    unrelated.write_text("later user content", encoding="utf-8")
    revert(target)
    assert all((target / filename).read_bytes() == data for filename, data in original.items())
    assert unrelated.read_text() == "later user content"


def test_stale_plan_rejected_before_any_write(target, planned):
    path = save_plan(target, planned)
    first = (target / "src/utils/telemetry.ts").read_bytes()
    (target / "telemetry.py").write_text("# changed by user\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Baseline changed"):
        apply_plan(target, path)
    assert (target / "src/utils/telemetry.ts").read_bytes() == first


def test_later_edit_blocks_whole_revert(target, planned):
    path = save_plan(target, planned)
    apply_plan(target, path)
    updated = (target / "src/utils/telemetry.ts").read_bytes()
    (target / "telemetry.py").write_text("# manual work\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Later edits conflict"):
        revert(target)
    assert (target / "src/utils/telemetry.ts").read_bytes() == updated
    assert (target / "telemetry.py").read_text() == "# manual work\n"


def test_stale_scan_rejected(target):
    report, decisions = reviewed(target)
    (target / "src/api/openai.ts").write_text("// user changed core\n")
    with pytest.raises(ValueError, match="Scan is stale"):
        create_plan(target, report, decisions)


@pytest.mark.parametrize("decision", ["KEEP_CORE", "SKIP_LEGAL", "NEEDS_REVIEW"])
def test_no_edits_for_non_mutable_decisions(target, decision):
    report, decisions = reviewed(target)
    decisions["reviews"][0]["decision"] = decision
    with pytest.raises(ValueError, match="requires a reviewed"):
        create_plan(target, report, decisions)


def test_copyright_cannot_be_deleted(target):
    report, decisions = reviewed(target)
    decisions["edits"][0]["new"] = "export async function checkUpdateAndAds() { return {}; }\n"
    with pytest.raises(ValueError, match="copyright"):
        create_plan(target, report, decisions)


def test_same_file_core_range_protected(target):
    path = target / "combined.py"
    path.write_text(
        'def optional():\n    return fetch("https://ads.vendor.io")\n'
        'def core():\n    return fetch("https://api.openai.com/v1")\n',
        encoding="utf-8",
    )
    report = scan(target).to_dict()
    cs = [
        c for c in report["candidates"] if c["file_path"] == "combined.py" and c["category"] == "OUTBOUND_URL"
    ]
    reviews = [
        {
            "candidate_id": c["id"],
            "decision": "MOCK_SILENT" if c["line_number"] == 2 else "KEEP_CORE",
            "reason": "reviewed",
            "reviewer": "test",
        }
        for c in cs
    ]
    optional = next(c for c in cs if c["line_number"] == 2)
    select_disable(target, report, optional["id"])
    edits = [
        {
            "candidate_id": optional["id"],
            "file_path": "combined.py",
            "old": path.read_bytes().decode("utf-8"),
            "new": "def optional():\n    return {}\n",
        }
    ]
    with pytest.raises(ValueError, match="protected candidate"):
        create_plan(target, report, {"reviews": reviews, "edits": edits})


@pytest.mark.parametrize(
    "filename", ["../outside.py", "/absolute.py", "LICENSE", ".git/config", "C:/outside.py"]
)
def test_paths_rejected(target, filename):
    with pytest.raises(ValueError):
        safe_path(target, filename)


def test_crlf_and_bom_exact_roundtrip(target):
    source = target / "telemetry.py"
    original = b'\xef\xbb\xbf# Copyright (c) Fixture author.\r\ndef ping():\r\n    return fetch("https://author.example/collect")\r\n'
    source.write_bytes(original)
    report = scan(target).to_dict()
    c = next(
        c
        for c in report["candidates"]
        if c["file_path"] == "telemetry.py" and c["category"] == "OUTBOUND_URL"
    )
    select_disable(target, report, c["id"])
    decisions = {
        "reviews": [
            {"candidate_id": c["id"], "decision": "MOCK_SILENT", "reason": "optional", "reviewer": "test"}
        ],
        "edits": [
            {
                "candidate_id": c["id"],
                "file_path": "telemetry.py",
                "old": '    return fetch("https://author.example/collect")',
                "new": "    return {}",
            }
        ],
    }
    plan = create_plan(target, report, decisions)
    apply_plan(target, save_plan(target, plan))
    assert source.read_bytes().startswith(b"\xef\xbb\xbf")
    assert source.read_bytes().count(b"\r\n") == 3
    revert(target)
    assert source.read_bytes() == original


def test_interrupted_apply_recoverable(target, planned):
    state = target / ".airgap"
    state.mkdir(exist_ok=True)
    planned["status"] = "applying"
    first = planned["files"][0]
    (target / first["file_path"]).write_bytes(base64.b64decode(first["after_b64"]))
    dump_json(state / "manifest.json", planned)
    revert(target)
    assert all(sha256((target / f["file_path"]).read_bytes()) == f["before_sha256"] for f in planned["files"])


def test_transaction_failure_rolls_back_written_files(target, planned, monkeypatch):
    from repo_airgap.patcher import patch_manager

    actual = patch_manager.atomic_write

    def fail_second(path, data):
        if path.name == "telemetry.py" and data == base64.b64decode(planned["files"][1]["after_b64"]):
            raise OSError("simulated write failure")
        actual(path, data)

    monkeypatch.setattr(patch_manager, "atomic_write", fail_second)
    with pytest.raises(OSError, match="simulated"):
        apply_plan(target, save_plan(target, planned))
    assert all(sha256((target / f["file_path"]).read_bytes()) == f["before_sha256"] for f in planned["files"])


def test_tampered_diff_rejected(target, planned):
    planned["expected_diff"] = "harmless looking diff"
    with pytest.raises(ValueError, match="diff does not match"):
        apply_plan(target, save_plan(target, planned))


@pytest.mark.parametrize("language", ["python", "typescript"])
def test_function_contract_change_rejected(target, language):
    audit, decisions = reviewed(target)
    index = 1 if language == "python" else 0
    new = decisions["edits"][index]["new"]
    if language == "python":
        new = new.replace("def send_usage_telemetry(payload: dict)", "async def send_usage_telemetry()")
    else:
        new = new.replace("export async function", "function")
    decisions["edits"][index]["new"] = new
    with pytest.raises(ValueError, match="signature/export changed"):
        create_plan(target, audit, decisions)


def test_patch_without_final_newline_is_git_applicable(target):
    import subprocess

    path = target / "tail.py"
    path.write_bytes(b'def ping():\n    return fetch("https://author.example/collect")')
    report = scan(target).to_dict()
    c = next(
        c for c in report["candidates"] if c["file_path"] == "tail.py" and c["category"] == "OUTBOUND_URL"
    )
    select_disable(target, report, c["id"])
    decisions = {
        "reviews": [
            {"candidate_id": c["id"], "decision": "MOCK_SILENT", "reason": "optional", "reviewer": "test"}
        ],
        "edits": [
            {
                "candidate_id": c["id"],
                "file_path": "tail.py",
                "old": '    return fetch("https://author.example/collect")',
                "new": "    return {}",
            }
        ],
    }
    plan = create_plan(target, report, decisions)
    patch = target / "change.patch"
    patch.write_bytes(plan["expected_diff"].encode("utf-8"))
    result = subprocess.run(["git", "apply", "--check", str(patch)], cwd=target, capture_output=True)
    assert result.returncode == 0, result.stderr
