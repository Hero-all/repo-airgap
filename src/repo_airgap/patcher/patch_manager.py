import ast
import base64
import difflib
import re
import uuid
from pathlib import Path

from ..io import atomic_write, dump_json, load_json, sha256, utcnow
from ..models import DECISIONS
from ..safety import safe_path, state_dir
from .contracts import preserve_signatures
from .patch_manifest import git_head, mutation_lock

LEGAL = re.compile(
    r"(?i)copyright|spdx-license-identifier|license\s*key|"
    r"(?:verify|validate|check)[_\s-]*license|license[_\s-]*(?:verify|validation)|\bDRM\b"
)
SUPPORTED = {".py", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".html"}


def _utf8(data):
    if b"\0" in data:
        raise ValueError("Binary edits are not supported")
    return data.decode("utf-8")  # Keep BOM and newline bytes intact.


def _legal_unchanged(before, after):
    def notices(text):
        return [line for line in text.splitlines() if LEGAL.search(line)]

    if notices(before) != notices(after):
        raise ValueError("Edit changes copyright, attribution or license-verification evidence")


def _syntax(path, content):
    if Path(path).suffix == ".py":
        ast.parse(content.lstrip("\ufeff"), filename=path)


def _patch(files):
    # Sequence follows manifest order, with exact before/after snapshots as authority.
    chunks = []
    for f in files:
        diff = difflib.unified_diff(
            _utf8(base64.b64decode(f["before_b64"])).splitlines(keepends=True),
            _utf8(base64.b64decode(f["after_b64"])).splitlines(keepends=True),
            fromfile="/dev/null" if f.get("operation") == "create" else "a/" + f["file_path"],
            tofile="b/" + f["file_path"],
        )
        for line in diff:
            chunks.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(chunks)


def create_plan(root, scan_report, decisions):
    if scan_report.get("repo_path") != str(root):
        raise ValueError("Scan belongs to a different repository")
    candidates = {c["id"]: c for c in scan_report["candidates"]}
    reviews = decisions.get("reviews", [])
    if not isinstance(reviews, list):
        raise ValueError("reviews must be an array")
    review_by_id = {}
    for review in reviews:
        cid = review["candidate_id"]
        if cid not in candidates or cid in review_by_id:
            raise ValueError(f"Unknown or duplicate candidate: {cid}")
        if review.get("decision") not in DECISIONS or not review.get("reason") or not review.get("reviewer"):
            raise ValueError("Every review needs a decision, reason and reviewer")
        review_by_id[cid] = review

    # Check the complete scan baseline, including core files outside the requested edits.
    baseline = {c["file_path"]: c["file_sha256"] for c in candidates.values()}
    for filename, digest in baseline.items():
        if sha256(safe_path(root, filename).read_bytes()) != digest:
            raise ValueError(f"Scan is stale: {filename}; scan and review again")
    grouped = {}
    for edit in decisions.get("edits", []):
        cid = edit["candidate_id"]
        c = candidates.get(cid)
        review = review_by_id.get(cid, {})
        if c is None or review.get("decision") not in ("MOCK_SILENT", "REMOVE_UI"):
            raise ValueError(f"Edit requires a reviewed MOCK_SILENT/REMOVE_UI candidate: {cid}")
        from ..review import assert_selection

        assert_selection(root, scan_report, cid, "DISABLE")
        filename = edit["file_path"]
        if filename != c["file_path"]:
            raise ValueError("An edit must reference its candidate's source file")
        path = safe_path(root, filename)
        if path.suffix.lower() not in SUPPORTED:
            raise ValueError(f"No automatic edit support for {path.suffix}; provide a manual patch")
        grouped.setdefault(filename, []).append(edit)

    files = []
    for filename, edits in grouped.items():
        before = safe_path(root, filename).read_bytes()
        original = _utf8(before)
        ranges = []
        for edit in edits:
            old, new = edit.get("old"), edit.get("new")
            if not isinstance(old, str) or not old or not isinstance(new, str) or old == new:
                raise ValueError("Each exact edit needs nonempty old text and different new text")
            if original.count(old) != 1:
                raise ValueError(f"Old text must match exactly once: {filename}")
            start = original.index(old)
            end = start + len(old)
            first_line = original[:start].count("\n") + 1
            last_line = original[: end - 1].count("\n") + 1
            if not first_line <= candidates[edit["candidate_id"]]["line_number"] <= last_line:
                raise ValueError("Edited range must contain its candidate's evidence line")
            for other in candidates.values():
                if other["file_path"] != filename:
                    continue
                decision = review_by_id.get(other["id"], {}).get("decision")
                if (
                    decision in ("KEEP_CORE", "SKIP_LEGAL")
                    and first_line <= other["line_number"] <= last_line
                ):
                    raise ValueError(f"Edit overlaps protected candidate {other['id']}")
            if any(start < b and end > a for a, b, _ in ranges):
                raise ValueError("Edits overlap; combine or narrow them")
            ranges.append((start, end, new))
        updated = original
        for start, end, new in sorted(ranges, reverse=True):
            updated = updated[:start] + new + updated[end:]
        _legal_unchanged(original, updated)
        _syntax(filename, updated)
        preserve_signatures(filename, original, updated)
        after = updated.encode("utf-8")
        files.append(
            {
                "file_path": filename,
                "candidate_ids": [e["candidate_id"] for e in edits],
                "before_sha256": sha256(before),
                "after_sha256": sha256(after),
                "before_b64": base64.b64encode(before).decode("ascii"),
                "after_b64": base64.b64encode(after).decode("ascii"),
            }
        )
    return {
        "schema_version": 1,
        "id": uuid.uuid4().hex,
        "repo_path": str(root),
        "created_at": utcnow(),
        "baseline_commit": git_head(root),
        "baseline_files": baseline,
        "scan_timestamp": scan_report["scan_timestamp"],
        "reviews": reviews,
        "files": files,
        "expected_diff": _patch(files),
        "status": "planned",
    }


def _validate_bundle(root, bundle):
    if bundle.get("schema_version") != 1 or bundle.get("repo_path") != str(root):
        raise ValueError("Plan/manifest repository or schema does not match")
    if not re.fullmatch(r"[0-9a-f]{32}", bundle.get("id", "")):
        raise ValueError("Invalid transaction ID")
    seen = set()
    for f in bundle["files"]:
        filename = f["file_path"]
        if filename in seen:
            raise ValueError("Duplicate file in transaction")
        seen.add(filename)
        path = safe_path(root, filename)
        if path.suffix.lower() not in SUPPORTED:
            raise ValueError("Unsupported file in transaction")
        for kind in ("before", "after"):
            data = base64.b64decode(f[kind + "_b64"], validate=True)
            if sha256(data) != f[kind + "_sha256"]:
                raise ValueError("Snapshot digest mismatch")
        before, after = (base64.b64decode(f[k + "_b64"]) for k in ("before", "after"))
        _legal_unchanged(_utf8(before), _utf8(after))
        _syntax(filename, _utf8(after))
        preserve_signatures(filename, _utf8(before), _utf8(after))
        operation = f.get("operation", "modify")
        if operation not in ("modify", "create"):
            raise ValueError("Unsupported file operation")
        if operation == "create" and (before or bundle.get("kind") != "authentication_migration"):
            raise ValueError("Only authentication migration may create a new runtime file")
        if operation == "modify" and bundle.get("baseline_files", {}).get(filename) != f["before_sha256"]:
            raise ValueError("File snapshot is not part of the scan baseline")
        reviews = {r["candidate_id"]: r for r in bundle.get("reviews", [])}
        allowed = (
            ("MIGRATE_AUTH",)
            if bundle.get("kind") == "authentication_migration"
            else ("MOCK_SILENT", "REMOVE_UI")
        )
        if not f.get("candidate_ids") or any(
            reviews.get(cid, {}).get("decision") not in allowed for cid in f["candidate_ids"]
        ):
            raise ValueError("File snapshot lacks mutable review decisions")
    if bundle.get("expected_diff") != _patch(bundle["files"]):
        raise ValueError("Reviewed diff does not match the transaction snapshots")
    return bundle


def _matches(root, file, kind):
    target = safe_path(root, file["file_path"])
    if kind == "before" and file.get("operation") == "create":
        return not target.exists()
    return target.is_file() and sha256(target.read_bytes()) == file[kind + "_sha256"]


def _restore_before(root, file):
    target = safe_path(root, file["file_path"])
    if file.get("operation") == "create":
        target.unlink()
    else:
        atomic_write(target, base64.b64decode(file["before_b64"]))


def apply_plan(root, plan_path):
    bundle = _validate_bundle(root, load_json(plan_path))
    if not bundle["files"]:
        raise ValueError("Plan has no edits")
    if bundle.get("status") != "planned":
        raise ValueError("Only a planned transaction can be applied")
    if git_head(root) != bundle.get("baseline_commit"):
        raise ValueError("Git HEAD changed; generate a fresh plan")
    with mutation_lock(root) as state:
        old_path = state / "manifest.json"
        if old_path.exists():
            old = load_json(old_path)
            if old.get("status") not in ("reverted", "apply_failed_rolled_back"):
                raise ValueError("An active transaction exists; revert it before applying another")
        for filename, digest in bundle["baseline_files"].items():
            if sha256(safe_path(root, filename).read_bytes()) != digest:
                raise ValueError(f"Baseline changed: {filename}")
        for f in bundle["files"]:
            if not _matches(root, f, "before"):
                raise ValueError(f"Source changed: {f['file_path']}")
        archive = state / ("manifest-" + bundle["id"] + ".json")
        if archive.exists():
            raise ValueError("This plan ID has already been used")
        bundle["status"] = "applying"
        bundle["started_at"] = utcnow()
        dump_json(old_path, bundle)
        written = []
        try:
            for f in bundle["files"]:
                target = safe_path(root, f["file_path"])
                if not _matches(root, f, "before"):
                    raise ValueError("Source changed during transaction")
                atomic_write(target, base64.b64decode(f["after_b64"]))
                written.append(f)
        except Exception:
            for f in reversed(written):
                target = safe_path(root, f["file_path"])
                if _matches(root, f, "after"):
                    _restore_before(root, f)
                else:
                    raise ValueError(
                        "Interrupted apply has conflicting edits; inspect manifest snapshots"
                    ) from None
            bundle["status"] = "apply_failed_rolled_back"
            dump_json(old_path, bundle)
            raise
        bundle["status"] = "applied"
        bundle["applied_at"] = utcnow()
        dump_json(old_path, bundle)
        dump_json(archive, bundle)
        atomic_write(state / "airgap-sanitized.patch", _patch(bundle["files"]).encode("utf-8"))
        return bundle


def revert(root, manifest_path=None):
    with mutation_lock(root) as state:
        path = Path(manifest_path) if manifest_path else state / "manifest.json"
        bundle = _validate_bundle(root, load_json(path))
        if bundle.get("status") not in ("applied", "applying", "reverting"):
            raise ValueError("Transaction is not active")
        current = state / "manifest.json"
        if not current.exists() or load_json(current)["id"] != bundle["id"]:
            raise ValueError("Manifest is not the active transaction")
        pending = []
        for f in bundle["files"]:
            if _matches(root, f, "after"):
                pending.append(f)
            elif not _matches(root, f, "before"):
                raise ValueError(f"Later edits conflict with rollback: {f['file_path']}; no files changed")
        bundle["status"] = "reverting"
        dump_json(current, bundle)
        for f in reversed(pending):
            if not _matches(root, f, "after"):
                raise ValueError("File changed during rollback; resume after inspecting manifest")
            _restore_before(root, f)
        bundle["status"] = "reverted"
        bundle["reverted_at"] = utcnow()
        dump_json(current, bundle)
        dump_json(state / ("manifest-" + bundle["id"] + ".json"), bundle)
        return bundle


def save_plan(root, plan, output=None):
    state = state_dir(root, create=True)
    path = Path(output).resolve() if output else state / "plan.json"
    if path.suffix.lower() != ".json":
        raise ValueError("Plan output must end in .json (a separate .patch is also generated)")
    if path.is_relative_to(state) and path.name in (
        "manifest.json",
        "audit_candidates.json",
        "verification.json",
        "outbound_inventory.json",
        "review_prompt.json",
        "review_inventory.json",
        "user_choices.json",
        "auth_inventory.json",
    ):
        raise ValueError("Plan output cannot overwrite a reserved state artifact")
    dump_json(path, plan)
    return path
