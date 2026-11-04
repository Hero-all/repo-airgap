"""Run the complete workflow on a disposable copy, without real network requests."""

import argparse
import json
import shutil
import sys
from pathlib import Path

from repo_airgap.cli import main
from repo_airgap.io import dump_json, load_json, sha256

PROJECT = Path(__file__).resolve().parents[1]


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT / ".airgap-demo")
    args = parser.parse_args()
    output = args.output.resolve()
    # Never delete or overwrite a previous demo/user directory.
    output.mkdir(parents=True, exist_ok=False)
    root = output / "app"
    shutil.copytree(PROJECT / "tests/fixtures/dirty_open_source_app", root)
    baseline = {
        p.relative_to(root).as_posix(): sha256(p.read_bytes())
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }

    def command(*argv):
        status = main(list(argv))
        if status:
            raise RuntimeError(f"CLI failed ({status}): {argv[0]}")

    command("scan", str(root))
    audit = load_json(root / ".airgap/audit_candidates.json")
    command("review", str(root))
    review_inventory = load_json(root / ".airgap/review_inventory.json")
    choices = {
        "src/utils/telemetry.ts": (
            "MOCK_SILENT",
            """// Copyright (c) Fixture author.
export async function checkUpdateAndAds() {
  return { hasUpdate: false, latestVersion: '0.0.0', ads: [], showSponsorModal: false };
}
""",
        ),
        "telemetry.py": (
            "MOCK_SILENT",
            """# Copyright (c) Fixture author.
import urllib.request


def send_usage_telemetry(payload: dict) -> dict:
    return {"status": "ok", "sanitized": True}
""",
        ),
        "src/components/SponsorModal.tsx": (
            "REMOVE_UI",
            """// Copyright (c) Fixture author.
export function SponsorModal() { return null; }
""",
        ),
    }
    reviews, edits = [], []
    selected_candidate_ids = set()
    for filename, (decision, replacement) in choices.items():
        found = [c for c in audit["candidates"] if c["file_path"] == filename]
        primary = next(c for c in found if c["category"] in ("OUTBOUND_URL", "PROMO_UI"))
        selected_candidate_ids.add(primary["id"])
        for c in found:
            reviews.append(
                {
                    "candidate_id": c["id"],
                    "decision": decision,
                    "reviewer": "demo-review",
                    "reason": "Fixture-specific optional feature; caller return shape inspected",
                }
            )
        edits.append(
            {
                "candidate_id": primary["id"],
                "file_path": filename,
                "old": (root / filename).read_bytes().decode("utf-8"),
                "new": replacement,
            }
        )
    selected_items = [
        item
        for item in review_inventory["items"]
        if selected_candidate_ids.intersection(item["candidate_ids"])
    ]
    review_args = ["review", str(root)]
    for item in selected_items:
        review_args.extend(["--set", f"{item['id']}=DISABLE"])
    command(*review_args)
    for c in audit["candidates"]:
        if c["file_path"] == "src/api/openai.ts":
            reviews.append(
                {
                    "candidate_id": c["id"],
                    "decision": "KEEP_CORE",
                    "reviewer": "demo-review",
                    "reason": "Core user-configured model API; preserve function and request",
                }
            )
    decisions = output / "decisions.json"
    dump_json(decisions, {"reviews": reviews, "edits": edits})
    command("plan", str(root), "--decisions", str(decisions))
    command("apply", str(root), "--plan", str(root / ".airgap/plan.json"))
    checks = [
        [
            sys.executable,
            "-c",
            "import socket, urllib.request; "
            "socket.socket=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('network disabled')); "
            "import telemetry; assert telemetry.send_usage_telemetry({}) == {'status':'ok','sanitized':True}",
        ]
    ]
    node = shutil.which("node")
    if node:
        checks.append([node, "--experimental-strip-types", "offline-check.mjs"])
    argv = ["verify", str(root)]
    for check in checks:
        argv.extend(["--command", json.dumps(check)])
    command(*argv)
    command("report", str(root))
    shutil.copyfile(root / ".airgap/AIRGAP_REPORT.md", output / "APPLIED_REPORT.md")
    shutil.copyfile(root / ".airgap/verification.json", output / "verification.json")
    shutil.copyfile(root / ".airgap/airgap-sanitized.patch", output / "airgap-sanitized.patch")
    command("revert", str(root))
    assert all(sha256((root / filename).read_bytes()) == digest for filename, digest in baseline.items())
    command("report", str(root))
    dump_json(
        output / "demo-result.json",
        {
            "byte_exact_rollback": True,
            "checks_run": len(checks),
            "node_checked": bool(node),
            "source_file_count": len(baseline),
            "airgap_verified": False,
            "note": "Process-level guards only; no host network isolation or proxy capture performed.",
        },
    )
    print(f"Demo complete: {output}")


if __name__ == "__main__":
    run()
