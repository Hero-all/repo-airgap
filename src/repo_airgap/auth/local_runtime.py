"""Standalone Flask session adapter, copied into selected target repositories.

Requires Flask, Python 3.11+, a private SQLite file and a persistent secret.
No Google credentials, model APIs or external requests are used.
"""

import hashlib
import hmac
import ipaddress
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA = """
CREATE TABLE IF NOT EXISTS local_users (
 id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, business_id TEXT UNIQUE NOT NULL,
 display_name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('user','admin')),
 google_sub TEXT UNIQUE, password_hash TEXT, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS local_sessions (
 token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES local_users(id), expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS local_attempts (
 key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL
);
"""


def _database(path):
    path = Path(path).expanduser()
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("Authentication database must be an absolute, non-symlink path")
    for parent in path.parents:
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise ValueError("Authentication database must not traverse a linked directory")
    return path


@contextmanager
def _connect(path):
    db = sqlite3.connect(str(_database(path)), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize_database(path):
    path = _database(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    with _connect(path) as db:
        db.executescript(SCHEMA)
    return path


def normalize_email(email):
    if not isinstance(email, str) or len(email) > 254 or "@" not in email or any(c.isspace() for c in email):
        raise ValueError("A valid account email is required")
    return email.strip().casefold()


def hash_password(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError("Password must contain 12–256 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + salt.hex() + "$" + digest.hex()


def verify_password(password, encoded):
    if not isinstance(password, str) or len(password) > 256 or not encoded:
        return False
    try:
        method, n, r, p, salt, expected = encoded.split("$")
        if (method, n, r, p) != ("scrypt", "16384", "8", "1"):
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


def provision_user(path, *, email, business_id, display_name="", role="user", google_sub=None, password=None):
    email = normalize_email(email)
    if not isinstance(business_id, str) or not business_id or len(business_id) > 256:
        raise ValueError("Explicit existing business user ID (or a chosen new ID) is required")
    if role not in ("user", "admin"):
        raise ValueError("Supported roles are user and admin")
    if google_sub is not None and (not isinstance(google_sub, str) or not google_sub):
        raise ValueError("Google subject must be a nonempty explicit ID")
    initialize_database(path)
    encoded = hash_password(password) if password is not None else None
    with _connect(path) as db:
        db.execute(
            "INSERT INTO local_users(email,business_id,display_name,role,google_sub,password_hash) "
            "VALUES(?,?,?,?,?,?)",
            (email, business_id, display_name or email, role, google_sub, encoded),
        )


def import_users(path, records):
    """Explicit admin-reviewed IDs; never merge an account by an unverified OAuth email."""
    initialize_database(path)
    with _connect(path) as db:
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("Each imported identity must be a JSON object")
            if (
                record.get("role", "user") not in ("user", "admin")
                or not record.get("business_id")
                or not record.get("google_sub")
                or not record.get("email")
            ):
                raise ValueError("Import requires email, business_id, google_sub and a valid role")
            if not isinstance(record["business_id"], str) or not isinstance(record["google_sub"], str):
                raise ValueError("Imported IDs must be strings")
            db.execute(
                "INSERT INTO local_users(email,business_id,display_name,role,google_sub) VALUES(?,?,?,?,?)",
                (
                    normalize_email(record["email"]),
                    record["business_id"],
                    record.get("display_name") or record["email"],
                    record.get("role", "user"),
                    record["google_sub"],
                ),
            )
    # Imported identities cannot log in until an administrator sets a fresh local password.


def reset_password(path, email, password):
    encoded = hash_password(password)
    with _connect(path) as db:
        user = db.execute("SELECT id FROM local_users WHERE email=?", (normalize_email(email),)).fetchone()
        if user is None:
            raise ValueError("Account not found")
        db.execute("UPDATE local_users SET password_hash=? WHERE id=?", (encoded, user["id"]))
        db.execute("DELETE FROM local_sessions WHERE user_id=?", (user["id"],))


def disable_user(path, email):
    with _connect(path) as db:
        user = db.execute("SELECT id FROM local_users WHERE email=?", (normalize_email(email),)).fetchone()
        if not user:
            raise ValueError("Account not found")
        db.execute("UPDATE local_users SET active=0 WHERE id=?", (user["id"],))
        db.execute("DELETE FROM local_sessions WHERE user_id=?", (user["id"],))


def _token_hash(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _safe_next(value):
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        return "/"
    if "\\" in value or any(ord(c) < 32 for c in value):
        return "/"
    return value


def install_local_auth(app, config):
    from flask import Blueprint, g, jsonify, redirect, render_template_string, request, session, url_for

    if "repo_airgap_local_auth" in app.extensions:
        raise ValueError("Local auth already installed")
    secret = os.environ.get("REPO_AIRGAP_AUTH_SECRET", "")
    if len(secret) < 32:
        raise ValueError("Set a persistent REPO_AIRGAP_AUTH_SECRET of at least 32 characters")
    database = _database(os.environ.get("REPO_AIRGAP_AUTH_DB", ""))
    if not database.is_file():
        raise ValueError("Provision the local authentication database before starting the app")
    # Validate schema without silently creating a replacement user store.
    with _connect(database) as db:
        db.execute("SELECT id,email,business_id,role,password_hash FROM local_users LIMIT 0")
    allow_http = os.environ.get("REPO_AIRGAP_AUTH_ALLOW_HTTP") == "1"
    app.secret_key = secret
    app.config.update(
        SESSION_COOKIE_NAME="repo_airgap_local_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=not allow_http,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    app.extensions["repo_airgap_local_auth"] = {"database": str(database), "adapter": "flask-session-v1"}
    bp = Blueprint("repo_airgap_auth", __name__, url_prefix="/auth/local")
    user_key = config["session_user_key"]
    dummy_hash = hash_password(secrets.token_urlsafe(32))

    def csrf_token():
        if "_airgap_csrf" not in session:
            session["_airgap_csrf"] = secrets.token_urlsafe(32)
        return session["_airgap_csrf"]

    def csrf_valid():
        data = request.get_json(silent=True) if request.is_json else request.form
        if not hasattr(data, "get"):
            return False
        token = request.headers.get("X-CSRF-Token") or (data or {}).get("csrf_token")
        expected = session.get("_airgap_csrf")
        origin = request.headers.get("Origin")
        same_origin = not origin or origin.rstrip("/") == request.host_url.rstrip("/")
        return (
            same_origin
            and isinstance(token, str)
            and isinstance(expected, str)
            and hmac.compare_digest(token, expected)
        )

    def under(path, prefixes):
        return any(path == prefix or path.startswith(prefix.rstrip("/") + "/") for prefix in prefixes)

    @app.before_request
    def _airgap_identity():
        if app.secret_key != secret:
            return jsonify(error="Local auth configuration was overwritten"), 503
        host = urlsplit(request.host_url).hostname or ""
        try:
            loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not request.is_secure and not (allow_http and loopback):
            return jsonify(error="HTTPS is required; HTTP development is limited to loopback"), 400
        # Old Google cookies never grant local access; identity is rebuilt from server state.
        session.pop(user_key, None)
        g.airgap_user = None
        token = session.get("_airgap_sid")
        if isinstance(token, str) and len(token) <= 128 and token.isascii():
            with _connect(database) as db:
                user = db.execute(
                    "SELECT u.* FROM local_users u JOIN local_sessions s ON s.user_id=u.id "
                    "WHERE s.token_hash=? AND s.expires>? AND u.active=1",
                    (_token_hash(token), time.time()),
                ).fetchone()
            if user:
                g.airgap_user = dict(user)
                fields = config["session_fields"]
                session[user_key] = {out: user[source] for out, source in fields.items()}
            else:
                session.pop("_airgap_sid", None)
        path = request.path
        if path in config["google_callback_paths"]:
            return jsonify(error="Google sign-in callback retired; use local sign-in"), 410
        if path in config["google_login_paths"]:
            if request.method != "GET":
                return jsonify(error="Method not allowed"), 405
            return redirect(url_for("repo_airgap_auth.login", next=_safe_next(request.args.get("next", "/"))))
        if path in config.get("logout_paths", []):
            if request.method != "POST":
                return jsonify(error="Use POST /auth/local/logout with a CSRF token"), 405
            return logout()
        protected = under(path, config["protected_paths"]) or under(path, config["admin_paths"])
        if protected and g.airgap_user is None:
            if path.startswith("/api/"):
                return jsonify(error="Authentication required"), 401
            return redirect(url_for("repo_airgap_auth.login", next=_safe_next(request.full_path.rstrip("?"))))
        if under(path, config["admin_paths"]) and g.airgap_user["role"] != "admin":
            return jsonify(error="Administrator role required"), 403

    @bp.get("/csrf")
    def csrf():
        return jsonify(csrf_token=csrf_token())

    @bp.route("/login", methods=["GET", "POST"])
    def login():
        next_path = _safe_next(request.args.get("next", "/"))
        if request.method == "GET":
            return render_template_string(LOGIN_HTML, csrf=csrf_token(), next=next_path)
        if request.content_length and request.content_length > 8192:
            return jsonify(error="Login request too large"), 413
        if not csrf_valid():
            return jsonify(error="Invalid CSRF token"), 403
        data = request.get_json(silent=True) if request.is_json else request.form
        if not isinstance(data, dict) and not hasattr(data, "get"):
            return jsonify(error="Invalid request"), 400
        try:
            email = normalize_email(data.get("email", ""))
        except ValueError:
            email = "invalid-account"
        password = data.get("password", "")
        now = time.time()
        keys = [
            ("account:" + hashlib.sha256(email.encode()).hexdigest(), 5),
            ("source:" + hashlib.sha256((request.remote_addr or "unknown").encode()).hexdigest(), 30),
        ]
        with _connect(database) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM local_attempts WHERE expires<=?", (now,))
            for key, maximum in keys:
                attempt = db.execute("SELECT count FROM local_attempts WHERE key=?", (key,)).fetchone()
                if attempt and attempt["count"] >= maximum:
                    return jsonify(error="Too many attempts; try later"), 429, {"Retry-After": "300"}
            user = db.execute("SELECT * FROM local_users WHERE email=?", (email,)).fetchone()
            valid = verify_password(
                password, user["password_hash"] if user and user["password_hash"] else dummy_hash
            )
            if not user or not valid or not user["active"] or not user["password_hash"]:
                for key, _ in keys:
                    db.execute(
                        "INSERT INTO local_attempts(key,count,expires) VALUES(?,1,?) "
                        "ON CONFLICT(key) DO UPDATE SET count=count+1",
                        (key, now + 300),
                    )
                return jsonify(error="Invalid email or password"), 401
            old = session.get("_airgap_sid")
            if isinstance(old, str) and old.isascii():
                db.execute("DELETE FROM local_sessions WHERE token_hash=?", (_token_hash(old),))
            token = secrets.token_urlsafe(32)
            db.execute("DELETE FROM local_sessions WHERE expires<=?", (now,))
            db.execute(
                "INSERT INTO local_sessions(token_hash,user_id,expires) VALUES(?,?,?)",
                (_token_hash(token), user["id"], now + 8 * 3600),
            )
            db.execute("DELETE FROM local_attempts WHERE key=?", (keys[0][0],))
        session.clear()
        session.permanent = True
        session["_airgap_sid"] = token
        csrf_token()
        if request.is_json:
            return jsonify(ok=True, next=_safe_next(data.get("next", next_path)))
        return redirect(_safe_next(data.get("next", next_path)))

    @bp.post("/logout")
    def logout():
        if not csrf_valid():
            return jsonify(error="Invalid CSRF token"), 403
        token = session.get("_airgap_sid")
        if isinstance(token, str) and token.isascii():
            with _connect(database) as db:
                db.execute("DELETE FROM local_sessions WHERE token_hash=?", (_token_hash(token),))
        session.clear()
        return jsonify(ok=True)

    @bp.get("/me")
    def me():
        if g.airgap_user is None:
            return jsonify(error="Authentication required"), 401
        u = g.airgap_user
        return jsonify(id=u["business_id"], email=u["email"], name=u["display_name"], role=u["role"])

    @app.after_request
    def _airgap_headers(response):
        if request.path.startswith("/auth/local"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
            )
        return response

    app.register_blueprint(bp)


LOGIN_HTML = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>本地登录</title>
<style>body{font:16px system-ui;background:#f3f5f8;color:#15233b;margin:0;display:grid;min-height:100vh;place-items:center}
main{background:white;padding:32px;border-radius:16px;width:min(360px,80vw);box-shadow:0 12px 50px #15233b12}
h1{margin:0 0 8px}p{color:#596579;line-height:1.6}label{display:block;margin:20px 0 6px}
input,button{box-sizing:border-box;width:100%;padding:12px;border-radius:8px;border:1px solid #ccd3de;font:inherit}
button{background:#2457d6;color:white;margin-top:24px;border:0;cursor:pointer}a{color:#2457d6}</style>
<main><h1>本地登录</h1><p>使用管理员为你配置的账号登录。</p>
<form method="post" action="{{ url_for('repo_airgap_auth.login') }}">
<input type="hidden" name="csrf_token" value="{{ csrf }}"><input type="hidden" name="next" value="{{ next }}">
<label for="email">邮箱</label><input id="email" name="email" type="email" autocomplete="username" required>
<label for="password">密码</label><input id="password" name="password" type="password" autocomplete="current-password" required>
<button type="submit">登录</button></form></main></html>"""
