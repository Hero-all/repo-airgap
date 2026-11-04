import csv
import io

from .io import atomic_write, load_json, redact, sha256
from .safety import safe_path, state_dir


def csv_report(report):
    out = io.StringIO(newline="")
    columns = [
        "id",
        "category",
        "severity",
        "file_path",
        "line_number",
        "matched_rule",
        "matched_content",
        "destination",
        "port",
        "sink_type",
        "decision",
    ]
    writer = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for candidate in report["candidates"]:
        # Defuse spreadsheet formula injection in untrusted source strings.
        writer.writerow(
            {
                k: "'" + str(v) if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v
                for k, v in candidate.items()
                if k in columns
            }
        )
    return out.getvalue()


def _cell(value):
    return redact(str(value)).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def report(root):
    state = state_dir(root, create=True)
    scan = load_json(state / "audit_candidates.json")
    manifest = load_json(state / "manifest.json") if (state / "manifest.json").exists() else None
    plan = load_json(state / "plan.json") if (state / "plan.json").exists() else {}
    review_inventory = (
        load_json(state / "review_inventory.json") if (state / "review_inventory.json").exists() else None
    )
    user_choices = load_json(state / "user_choices.json") if (state / "user_choices.json").exists() else None
    # Candidate IDs belong to a specific scan, not a permanent cross-scan identity.
    review_source = (
        manifest if manifest and manifest.get("scan_timestamp") == scan["scan_timestamp"] else plan
    )
    reviews = (
        review_source.get("reviews", [])
        if review_source.get("scan_timestamp") == scan["scan_timestamp"]
        else []
    )
    decisions = {r["candidate_id"]: r for r in reviews}
    verification = load_json(state / "verification.json") if (state / "verification.json").exists() else None
    lines = [
        "# AIRGAP_REPORT",
        "",
        f"Repository: `{root}`",
        f"Scan time: {scan['scan_timestamp']}",
        "",
        f"Scanned files: {scan['total_files_scanned']}; candidates: {len(scan['candidates'])}; "
        f"suppressed: {scan.get('suppressed_count', 0)}.",
        "",
        "This report contains review evidence. It does not certify zero outbound traffic.",
        "",
        "## Changes",
        "",
    ]
    if (
        review_inventory
        and review_inventory.get("scan_timestamp") == scan["scan_timestamp"]
        and user_choices
        and user_choices.get("scan_timestamp") == scan["scan_timestamp"]
    ):
        selected = {c.get("item_id"): c.get("action", "KEEP") for c in user_choices.get("choices", [])}
        lines.extend(["## User outbound choices", ""])
        for item in review_inventory.get("items", []):
            lines.append(
                f"- `{item['id']}`: **{_cell(selected.get(item['id'], 'KEEP'))}** — "
                f"{_cell(item['title'])}; locations: {_cell(', '.join(item.get('locations', [])))}"
            )
        lines.append("")
    if manifest:
        lines.append(f"Transaction `{manifest['id']}`: **{manifest['status']}**.")
        for f in manifest["files"]:
            lines.append(
                f"- `{_cell(f['file_path'])}`: {', '.join(f['candidate_ids'])}; "
                f"`{f['before_sha256'][:12]}` → `{f['after_sha256'][:12]}`"
            )
        lines.append("\nExact local diff: `airgap-sanitized.patch`. Snapshots: `manifest.json`.")
    else:
        lines.append("No applied transaction. Reviewed plans are not applied changes.")
    for decision, title in (
        ("MOCK_SILENT", "Reviewed non-core outbound"),
        ("REMOVE_UI", "Reviewed promotion UI"),
        ("KEEP_CORE", "Retained core connections"),
        ("SKIP_LEGAL", "Protected legal items"),
        ("MIGRATE_AUTH", "User-selected local authentication migration"),
        ("NEEDS_REVIEW", "Unresolved candidates"),
    ):
        lines.extend(
            [
                "",
                "## " + title,
                "",
                "| ID | Source | Target / evidence | Decision reason |",
                "| --- | --- | --- | --- |",
            ]
        )
        for c in scan["candidates"]:
            r = decisions.get(c["id"], {})
            if r.get("decision", "NEEDS_REVIEW") == decision:
                lines.append(
                    f"| {c['id']} | {_cell(c['file_path'])}:{c['line_number']} | "
                    f"{_cell(c.get('destination') or c['matched_content'])} | {_cell(r.get('reason', 'Pending review'))} |"
                )
    lines.extend(["", "## Verification", ""])
    if verification:
        fresh = (
            verification.get("manifest_id") == (manifest or {}).get("id")
            and verification.get("manifest_status") == (manifest or {}).get("status")
            and verification.get("scan_timestamp") == scan["scan_timestamp"]
        )
        for filename, digest in verification.get("file_hashes", {}).items():
            path = safe_path(root, filename)
            matches = (
                not path.exists()
                if digest == "ABSENT"
                else path.is_file() and sha256(path.read_bytes()) == digest
            )
            fresh = fresh and matches
        lines.append(
            f"Checks: **{verification['status'] if fresh else 'STALE — rerun verify'}**; "
            f"runtime: {verification['runtime']['status']}. OS network isolation is not provided."
        )
        for check in verification["checks"]:
            lines.append(f"- {_cell(check['kind'])}: {check['status']} {_cell(check.get('file', ''))}")
        for c in verification["runtime"]["connections"]:
            lines.append(
                f"- Observed: {_cell(c['destination'])}; outcome: {_cell(c.get('outcome', 'unknown'))}"
            )
    else:
        lines.append(
            "Not verified. Run project builds/tests and critical flows in an isolated offline environment."
        )
    lines.extend(["", "## Offline readiness hints", ""])
    hints = scan.get("offline_hints", [])
    if hints:
        lines.extend(
            [
                "Configuration switches and downloads detected from manifests and code. These are",
                "not source edits; see `.airgap/offline.env` for suggested overrides. Switch status",
                "describes repository files only: `configured` means a non-example file assigns the",
                "recommended value, not that the deployed process receives it; `example_only` values",
                "come from example/template/docs files and are not treated as configuration.",
                "",
                "| Kind | Detected | Evidence | Switch / action | Reference |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for hint in hints:
            action = (
                "<br>".join(
                    f"`{e['name']}={e['recommended']}` ({e['status']}"
                    + (f": {_cell(', '.join(e.get('found_in', [])[:2]))}" if e.get("found_in") else "")
                    + ")"
                    for e in hint.get("env", [])
                )
                or _cell(hint.get("advice", ""))
            )
            lines.append(
                f"| {hint['kind']} | {_cell(hint['name'])} | {_cell(', '.join(hint.get('evidence', [])[:3]))} | "
                f"{action} | {_cell(hint.get('reference') or '')} |"
            )
    else:
        lines.append("No known framework switches or download signals detected.")
    lines.extend(["", "## Coverage and limitations", ""])
    lines.extend("- " + _cell(item) for item in scan.get("limitations", []) + scan.get("warnings", []))
    lines.extend(
        [
            "- Static declarations may not execute. Dynamic targets and untriggered paths require runtime review.",
            "- Secret redaction is best-effort. Plans, patches and snapshots contain source; keep them local.",
            "",
        ]
    )
    output = state / "AIRGAP_REPORT.md"
    atomic_write(output, "\n".join(lines).encode("utf-8"))
    return output
