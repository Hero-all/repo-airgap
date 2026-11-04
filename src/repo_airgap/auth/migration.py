"""Bounded adapter for module-level Flask apps using a session user dictionary."""

import ast
import base64
import re
import uuid
from pathlib import Path

from ..engine import scan
from ..io import load_json, sha256, utcnow
from ..patcher.contracts import preserve_signatures
from ..patcher.patch_manager import _legal_unchanged, _patch
from ..patcher.patch_manifest import git_head
from ..review import GOOGLE_LOGIN, assert_selection, review
from ..safety import safe_path, state_dir
from ..scanner.auth_detector import AUTH_SIGNALS

GOOGLE_SERVICES = re.compile(
    r"googleapiclient|google\.cloud|google\.generativeai|googlemaps|gmail\.|drive\.google|"
    r"www\.googleapis\.com/auth/(?!userinfo\b)[\w.]+|https://(?:www\.)?googleapis\.com/(?:drive|gmail|calendar)",
    re.I,
)
GOOGLE_OAUTH_CLIENTS = re.compile(
    r"authlib|flask[-_]?dance|google[-_]?auth[-_]?(?:oauthlib|oauth2)|google\.oauth|oauthlib|oauth2client",
    re.I,
)


def inspect_auth(root):
    state = state_dir(root)
    manifest = state / "manifest.json"
    if manifest.exists() and load_json(manifest).get("status") in ("applied", "applying", "reverting"):
        raise ValueError("Active transaction: revert before refreshing authentication evidence")
    audit = scan(root).to_dict()
    findings = []
    # The regular scan supplies evidence; this pass only enumerates static files already in scope.
    from ..scanner.file_walker import read_source, walk_files

    warnings = []
    for path in walk_files(root, warnings):
        if path.suffix.lower() not in (".py", ".js", ".ts", ".tsx", ".jsx", ".json"):
            continue
        document = read_source(path)
        if document:
            for number, line in enumerate(document[1].splitlines(), 1):
                if AUTH_SIGNALS.search(line):
                    findings.append(
                        {
                            "file_path": path.relative_to(root).as_posix(),
                            "line_number": number,
                            "provider": "google" if GOOGLE_LOGIN.search(line) else "unknown",
                            "default_action": "KEEP",
                        }
                    )
    return {
        "repo_path": str(root),
        "inspected_at": utcnow(),
        "findings": findings,
        "supported_adapter": "flask-session-v1",
        "warnings": warnings,
        "note": "Findings include Google and generic auth signals; only a confirmed Google item may select local-auth replacement. Other frameworks require explicit adapters.",
    }, audit


def _validate_mapping(root, mapping):
    if mapping.get("adapter") != "flask-session-v1":
        raise ValueError("Only flask-session-v1 is implemented; other frameworks remain unchanged")
    filename = mapping.get("entrypoint", "")
    if "/" in filename or not filename.endswith(".py"):
        raise ValueError("First adapter requires a module-level root Python entrypoint, such as app.py")
    source = safe_path(root, filename).read_bytes().decode("utf-8")
    if GOOGLE_SERVICES.search(source):
        raise ValueError(
            "Google business-service dependencies detected; replacing login alone is insufficient"
        )
    if GOOGLE_OAUTH_CLIENTS.search(source):
        raise ValueError(
            "Existing Google OAuth client code requires a dedicated adapter; local auth is not injected over it"
        )
    tree = ast.parse(source.lstrip("\ufeff"))
    variable = mapping.get("app_variable", "app")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", variable):
        raise ValueError("Invalid Flask app variable")
    initialized = any(
        isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == variable for t in n.targets)
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name)
        and n.value.func.id == "Flask"
        for n in tree.body
    )
    if not initialized:
        raise ValueError("Expected a top-level app = Flask(...) assignment")
    if "flask_login" in source or any(
        isinstance(n, ast.Attribute) and n.attr in ("register_blueprint", "before_request")
        for n in ast.walk(tree)
    ):
        raise ValueError(
            "Flask-Login, blueprints and existing before_request hooks require a dedicated adapter"
        )
    paths, first_route = [], None
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == variable
                and decorator.func.attr in ("route", "get", "post", "put", "delete", "patch")
            ):
                if (
                    not decorator.args
                    or not isinstance(decorator.args[0], ast.Constant)
                    or not isinstance(decorator.args[0].value, str)
                ):
                    raise ValueError("Dynamic route declaration requires manual review")
                paths.append(decorator.args[0].value)
                declaration_start = min(d.lineno for d in node.decorator_list)
                first_route = min(first_route or declaration_start, declaration_start)
    if not paths:
        raise ValueError("No supported Flask route declarations found")
    required = (
        "google_login_paths",
        "google_callback_paths",
        "public_paths",
        "protected_paths",
        "admin_paths",
        "logout_paths",
    )
    for key in required:
        values = mapping.get(key)
        if not isinstance(values, list) or not all(
            isinstance(p, str)
            and p.startswith("/")
            and not p.startswith("//")
            and "?" not in p
            and "\\" not in p
            for p in values
        ):
            raise ValueError(f"{key} must list explicit internal paths")
    if (
        not mapping["google_login_paths"]
        or not mapping["google_callback_paths"]
        or not mapping["protected_paths"]
    ):
        raise ValueError("Google entry/callback and protected business paths must be explicit")
    for p in mapping["google_login_paths"] + mapping["google_callback_paths"] + mapping["logout_paths"]:
        if p not in paths:
            raise ValueError(f"Configured original auth route does not exist: {p}")
    for path in paths:
        exact = (
            mapping["public_paths"]
            + mapping["google_login_paths"]
            + mapping["google_callback_paths"]
            + mapping["logout_paths"]
        )
        prefixes = mapping["protected_paths"] + mapping["admin_paths"]
        if path not in exact and not any(path == p or path.startswith(p.rstrip("/") + "/") for p in prefixes):
            raise ValueError(f"Route has no reviewed access policy: {path}")
    user_key = mapping.get("session_user_key")
    fields = mapping.get("session_fields")
    if not isinstance(user_key, str) or not user_key.isidentifier() or user_key.startswith("_"):
        raise ValueError("session_user_key must be an explicit non-reserved identifier")
    if (
        not isinstance(fields, dict)
        or not fields
        or any(
            not isinstance(k, str) or v not in ("business_id", "email", "display_name", "role", "google_sub")
            for k, v in fields.items()
        )
    ):
        raise ValueError(
            "session_fields must explicitly map existing user fields to verified local identity fields"
        )
    if any(path.startswith("/auth/local") for path in paths):
        raise ValueError("Target already defines /auth/local routes")
    return filename, source, first_route, paths


def create_auth_plan(root, mapping):
    state = state_dir(root, create=True)
    if (state / "manifest.json").exists() and load_json(state / "manifest.json").get("status") in (
        "applied",
        "applying",
        "reverting",
    ):
        raise ValueError("Revert active transaction before planning authentication migration")
    audit_path = state / "audit_candidates.json"
    if not audit_path.exists():
        raise ValueError("Authentication evidence missing; run auth inspect or scan before auth plan")
    audit = load_json(audit_path)
    selection = load_json(state / "user_choices.json") if (state / "user_choices.json").exists() else {}
    inventory = review(root)
    google_items = [
        i for i in inventory["items"] if i["kind"] == "authentication" and i.get("provider") == "google"
    ]
    if not google_items:
        raise ValueError("No Google authentication candidate in current review inventory")
    # Selection is from the current inventory, not inferred from the presence of Google URLs.
    selected_ids = {
        cid
        for choice in selection.get("choices", [])
        if choice["action"] == "REPLACE_LOCAL_AUTH"
        for cid in choice["candidate_ids"]
    }
    candidate_id = next(
        (cid for item in google_items for cid in item["candidate_ids"] if cid in selected_ids), None
    )
    if not candidate_id:
        raise ValueError("Select REPLACE_LOCAL_AUTH for Google login in review before creating an auth plan")
    assert_selection(root, audit, candidate_id, "REPLACE_LOCAL_AUTH")
    filename, source, line, paths = _validate_mapping(root, mapping)
    before = safe_path(root, filename).read_bytes()
    runtime_name = "repo_airgap_local_auth.py"
    if safe_path(root, runtime_name).exists():
        raise ValueError("Generated runtime file already exists; refusing overwrite")
    config = {
        k: mapping[k]
        for k in (
            "google_login_paths",
            "google_callback_paths",
            "protected_paths",
            "admin_paths",
            "logout_paths",
            "session_user_key",
            "session_fields",
        )
    }
    newline = "\r\n" if "\r\n" in source else "\n"
    injection = (
        "from repo_airgap_local_auth import install_local_auth as _install_local_auth\n"
        f"_install_local_auth({mapping.get('app_variable', 'app')}, {config!r})\n\n"
    ).replace("\n", newline)
    lines = source.splitlines(keepends=True)
    lines.insert(line - 1, injection)
    after = "".join(lines).encode("utf-8")
    _legal_unchanged(source, after.decode("utf-8"))
    preserve_signatures(filename, source, after.decode("utf-8"))
    ast.parse(after.decode("utf-8-sig"))
    runtime = Path(__file__).with_name("local_runtime.py").read_bytes()
    records = []
    for name, original, updated, operation in (
        (filename, before, after, "modify"),
        (runtime_name, b"", runtime, "create"),
    ):
        records.append(
            {
                "file_path": name,
                "operation": operation,
                "candidate_ids": [candidate_id],
                "before_sha256": sha256(original),
                "after_sha256": sha256(updated),
                "before_b64": base64.b64encode(original).decode(),
                "after_b64": base64.b64encode(updated).decode(),
            }
        )
    return {
        "schema_version": 1,
        "id": uuid.uuid4().hex,
        "kind": "authentication_migration",
        "repo_path": str(root),
        "created_at": utcnow(),
        "baseline_commit": git_head(root),
        "baseline_files": {c["file_path"]: c["file_sha256"] for c in audit["candidates"]}
        | {filename: sha256(before)},
        "scan_timestamp": audit["scan_timestamp"],
        "status": "planned",
        "files": records,
        "reviews": [
            {
                "candidate_id": candidate_id,
                "decision": "MIGRATE_AUTH",
                "reviewer": "user-selected-adapter",
                "reason": "Explicitly selected local authentication with credentials, server sessions and route policies",
            }
        ],
        "expected_diff": _patch(records),
        "auth_migration": {
            "adapter": "flask-session-v1",
            "mapping": mapping,
            "routes": paths,
            "provisioning_required": True,
            "runtime_verified": False,
            "limitations": [
                "No automatic changes to business databases; explicit existing ID mapping is required.",
                "Generated auth database and users persist outside source and are not removed by code rollback.",
                "Google Drive/Gmail and external websites cannot be made offline by replacing sign-in.",
            ],
        },
    }
