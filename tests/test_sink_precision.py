from repo_airgap.engine import scan


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sinks(root):
    return {(c.file_path, c.line_number) for c in scan(root).candidates if c.category == "NETWORK_SINK"}


def test_non_call_lookalikes_are_not_sinks(tmp_path):
    # Each line is a real false positive seen in dify / lobe-chat.
    write(
        tmp_path,
        "a.py",
        "def __init__(self, client: httpx.Client) -> None:\n"
        "except httpx.TimeoutException as e:\n"
        "upload_file = FileService(db.engine).upload_file(name)\n"
        "r = httpx.get(url, timeout=httpx.Timeout(10.0))\n"
        "s3.upload_file('a', 'bucket', 'key')\n",
    )
    write(
        tmp_path,
        "b.ts",
        "export type NewScm = typeof scmChangeRequests.$inferInsert;\n"
        "function PatchedWebSocket(this: WebSocket, url: string) {}\n"
        "return this.getActiveSocket()?.connected === true\n"
        "const ws = new WebSocket(url)\n",
    )
    found = sinks(tmp_path)
    assert {n for f, n in found if f == "a.py"} == {4, 5}
    assert {n for f, n in found if f == "b.ts"} == {4}


def test_dynamic_sinks_are_labeled_self_or_configured(tmp_path):
    # Patterns from open-webui / lobe-chat frontends.
    write(
        tmp_path,
        "api.ts",
        "const a = await fetch('/api/config');\n"
        "const b = await fetch(`${WEBUI_API_BASE_URL}/auths/ldap`, {\n"
        "const c = await fetch(\n"
        "  `${OLLAMA_API_BASE_URL}/api/tags`,\n"
        "const d = await fetch(statusUrl, {\n"
        "const e = await fetch(`${url}/chat/completions`);\n",
    )
    write(tmp_path, "client.py", "r = requests.post(f'{self.base_url}/v1/x', json=p)\n")
    by_line = {
        (c.file_path, c.line_number): (c.destination, c.severity)
        for c in scan(tmp_path).candidates
        if c.category == "NETWORK_SINK"
    }
    assert by_line[("api.ts", 1)] == ("self", "LOW")
    assert by_line[("api.ts", 2)] == ("config:WEBUI_API_BASE_URL", "MEDIUM")
    assert by_line[("api.ts", 3)] == ("config:OLLAMA_API_BASE_URL", "MEDIUM")
    assert by_line[("api.ts", 5)] == ("dynamic", "HIGH")
    assert by_line[("api.ts", 6)] == ("dynamic", "HIGH")
    assert by_line[("client.py", 1)] == ("config:base_url", "MEDIUM")


def test_reference_links_downgraded_but_fetches_and_telemetry_kept(tmp_path):
    write(
        tmp_path,
        "page.tsx",
        "<a href=\"https://docs.vendor.io/setup\">Docs</a>\n"
        "const help = 'https://github.com/org/app/issues/new'\n"
        "<script src=\"https://cdn.vendor.io/lib.js\"></script>\n"
        "<a href=\"https://www.googletagmanager.com/ns.html\">x</a>\n"
        "const API = 'https://api.vendor.io/v1'\n",
    )
    urls = {c.line_number: c for c in scan(tmp_path).candidates if c.category == "OUTBOUND_URL"}
    assert "reference_link" in urls[1].related_signals and urls[1].severity == "LOW"
    assert "reference_link" in urls[2].related_signals
    assert "reference_link" not in urls[3].related_signals  # browser fetches script src
    assert "known_telemetry_domain" in urls[4].related_signals
    assert "reference_link" not in urls[4].related_signals
    assert "reference_link" not in urls[5].related_signals


def test_review_inventory_groups_self_config_and_reference(tmp_path):
    from repo_airgap.review import build_inventory

    write(
        tmp_path,
        "api.ts",
        "fetch('/api/a');\nfetch('/api/b');\n"
        "fetch(`${API_BASE_URL}/x`);\nfetch(`${API_BASE_URL}/y`);\n"
        "const l1 = '<a href=\"https://docs.one.io\">'\nconst l2 = '<a href=\"https://docs.two.io\">'\n",
    )
    items = build_inventory(scan(tmp_path).to_dict())["items"]
    kinds = sorted(i["kind"] for i in items)
    assert kinds == ["configured_backend", "reference", "self_api"]
    assert all(i["default_action"] == "KEEP" for i in items)
