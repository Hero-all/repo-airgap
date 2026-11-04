import shutil
from pathlib import Path

import pytest

from repo_airgap.engine import scan
from repo_airgap.patcher.patch_manager import create_plan
from repo_airgap.review import build_inventory, save_choices

FIXTURE = Path(__file__).parent / "fixtures" / "dirty_open_source_app"
JS_AFTER = """// Copyright (c) Fixture author.
export async function checkUpdateAndAds() {
  return { hasUpdate: false, latestVersion: '0.0.0', ads: [], showSponsorModal: false };
}
"""
PY_AFTER = """# Copyright (c) Fixture author.
import urllib.request


def send_usage_telemetry(payload: dict) -> dict:
    return {"status": "ok", "sanitized": True}
"""


@pytest.fixture
def target(tmp_path):
    root = tmp_path / "app"
    shutil.copytree(FIXTURE, root)
    return root.resolve()


def reviewed(root, scan_report=None):
    report = scan_report or scan(root).to_dict()
    reviews, edits = [], []
    for filename, replacement in (("src/utils/telemetry.ts", JS_AFTER), ("telemetry.py", PY_AFTER)):
        candidate = next(
            c
            for c in report["candidates"]
            if c["file_path"] == filename and c["matched_rule"] == "hardcoded_external_url"
        )
        reviews.append(
            {
                "candidate_id": candidate["id"],
                "decision": "MOCK_SILENT",
                "reason": "Optional fixture heartbeat; return contract inspected",
                "reviewer": "test",
            }
        )
        edits.append(
            {
                "candidate_id": candidate["id"],
                "file_path": filename,
                "old": (root / filename).read_bytes().decode("utf-8"),
                "new": replacement,
            }
        )
    core = next(
        c
        for c in report["candidates"]
        if c["file_path"] == "src/api/openai.ts" and c["matched_rule"] == "hardcoded_external_url"
    )
    reviews.append(
        {"candidate_id": core["id"], "decision": "KEEP_CORE", "reason": "Core model API", "reviewer": "test"}
    )
    decisions = {"reviews": reviews, "edits": edits}
    selected = {edit["candidate_id"] for edit in edits}
    inventory = build_inventory(report)
    save_choices(
        root,
        inventory,
        {
            "scan_timestamp": report["scan_timestamp"],
            "choices": [
                {"item_id": item["id"], "action": "DISABLE"}
                for item in inventory["items"]
                if selected.intersection(item["candidate_ids"])
            ],
        },
    )
    return report, decisions


def select_disable(root, report, *candidate_ids):
    inventory = build_inventory(report)
    selected = set(candidate_ids)
    save_choices(
        root,
        inventory,
        {
            "scan_timestamp": report["scan_timestamp"],
            "choices": [
                {"item_id": item["id"], "action": "DISABLE"}
                for item in inventory["items"]
                if selected.intersection(item["candidate_ids"])
            ],
        },
    )


@pytest.fixture
def planned(target):
    report, decisions = reviewed(target)
    return create_plan(target, report, decisions)
