import pytest

pytest.importorskip("flask")
from flask import Flask, jsonify

from repo_airgap.auth.local_runtime import install_local_auth, provision_user
from repo_airgap.cli import main
from repo_airgap.io import dump_json, load_json


def test_local_auth_enforces_session_csrf_roles_and_retired_google_callback(tmp_path, monkeypatch):
    database = tmp_path / "auth.sqlite3"
    provision_user(
        database,
        email="alice@example.com",
        business_id="U001",
        display_name="Alice",
        role="user",
        password="correct horse battery",
    )
    monkeypatch.setenv("REPO_AIRGAP_AUTH_SECRET", "x" * 48)
    monkeypatch.setenv("REPO_AIRGAP_AUTH_DB", str(database))
    monkeypatch.setenv("REPO_AIRGAP_AUTH_ALLOW_HTTP", "1")

    app = Flask(__name__)

    @app.get("/login/google")
    def google_login():
        return "old"

    @app.get("/oauth/google/callback")
    def google_callback():
        return "old"

    @app.get("/")
    def home():
        return "home"

    @app.get("/dashboard")
    def dashboard():
        return jsonify(ok=True)

    @app.get("/admin")
    def admin():
        return jsonify(admin=True)

    @app.post("/logout")
    def old_logout():
        return "old"

    install_local_auth(
        app,
        {
            "google_login_paths": ["/login/google"],
            "google_callback_paths": ["/oauth/google/callback"],
            "protected_paths": ["/dashboard"],
            "admin_paths": ["/admin"],
            "logout_paths": ["/logout"],
            "session_user_key": "user",
            "session_fields": {"id": "business_id", "email": "email", "role": "role"},
        },
    )
    client = app.test_client()
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert "/auth/local/login" in response.location
    assert client.get("/oauth/google/callback").status_code == 410

    csrf = client.get("/auth/local/csrf").get_json()["csrf_token"]
    assert (
        client.post(
            "/auth/local/login", json={"email": "alice@example.com", "password": "wrong", "csrf_token": csrf}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/auth/local/login",
            json={"email": "alice@example.com", "password": "correct horse battery", "csrf_token": csrf},
        ).status_code
        == 200
    )
    assert client.get("/auth/local/me").get_json()["id"] == "U001"
    assert client.get("/admin").status_code == 403
    csrf = client.get("/auth/local/csrf").get_json()["csrf_token"]
    assert client.post("/logout", json={"csrf_token": csrf}).status_code == 200
    assert client.get("/auth/local/me").status_code == 401


def test_auth_inspect_persists_scan_evidence_for_followup_plan(tmp_path):
    (tmp_path / "app.py").write_text(
        "from flask import Flask\n"
        "app = Flask(__name__)\n"
        "@app.get('/login/google')\n"
        "def login(): return 'https://accounts.google.com/o/oauth2/auth'\n",
        encoding="utf-8",
    )
    assert main(["auth", "inspect", str(tmp_path)]) == 0
    state = tmp_path / ".airgap"
    assert (state / "audit_candidates.json").exists()
    assert load_json(state / "audit_candidates.json")["candidates"]
    assert (state / "outbound_inventory.json").exists()
    assert (state / "review_prompt.json").exists()


def test_google_to_local_auth_plan_uses_explicit_review_choice(tmp_path):
    (tmp_path / "app.py").write_text(
        "from flask import Flask\n"
        "app = Flask(__name__)\n"
        "@app.get('/login/google')\n"
        "def login(): return 'https://accounts.google.com/o/oauth2/auth'\n"
        "@app.get('/oauth/google/callback')\n"
        "def callback(): return 'old'\n"
        "@app.get('/dashboard')\n"
        "def dashboard(): return 'ok'\n"
        "@app.get('/admin')\n"
        "def admin(): return 'admin'\n"
        "@app.post('/logout')\n"
        "def logout(): return 'old'\n",
        encoding="utf-8",
    )
    assert main(["auth", "inspect", str(tmp_path)]) == 0
    assert main(["review", str(tmp_path)]) == 0
    state = tmp_path / ".airgap"
    inventory = load_json(state / "review_inventory.json")
    google = next(item for item in inventory["items"] if item.get("provider") == "google")
    assert main(["review", str(tmp_path), "--set", f"{google['id']}=REPLACE_LOCAL_AUTH"]) == 0
    mapping_path = tmp_path / "mapping.json"
    dump_json(
        mapping_path,
        {
            "adapter": "flask-session-v1",
            "entrypoint": "app.py",
            "app_variable": "app",
            "google_login_paths": ["/login/google"],
            "google_callback_paths": ["/oauth/google/callback"],
            "public_paths": [],
            "protected_paths": ["/dashboard"],
            "admin_paths": ["/admin"],
            "logout_paths": ["/logout"],
            "session_user_key": "user",
            "session_fields": {"id": "business_id", "email": "email", "role": "role"},
        },
    )
    assert main(["auth", "plan", str(tmp_path), "--mapping", str(mapping_path)]) == 0
    plan = load_json(state / "plan.json")
    assert plan["kind"] == "authentication_migration"
    assert any(f["operation"] == "create" for f in plan["files"])
