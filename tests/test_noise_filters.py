from repo_airgap.engine import scan


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_tests_skipped_by_default_but_optional(tmp_path):
    write(tmp_path, "src/app.py", "requests.get('https://telemetry.vendor.io/x')\n")
    write(tmp_path, "tests/test_app.py", "requests.get('https://mock.vendor.io/x')\n")
    write(tmp_path, "web/__tests__/page.spec.tsx", "fetch('https://mock2.vendor.io')\n")
    default = {c.destination for c in scan(tmp_path).candidates}
    assert "telemetry.vendor.io" in default
    assert not default & {"mock.vendor.io", "mock2.vendor.io"}
    assert any("--include-tests" in w for w in scan(tmp_path).warnings)
    full = {c.destination for c in scan(tmp_path, include_tests=True).candidates}
    assert {"mock.vendor.io", "mock2.vendor.io"} <= full


def test_local_destinations_are_not_egress(tmp_path):
    write(
        tmp_path,
        "client.py",
        "a = requests.get('http://localhost:8000/v1')\n"
        "b = requests.get('http://api:5001/console')\n"
        "c = requests.get('http://10.0.0.5/x')\n"
        "d = requests.get('http://db.internal/x')\n"
        "e = requests.get('https://real.vendor.io/x')\n",
    )
    cands = scan(tmp_path).candidates
    assert {c.destination for c in cands if c.category == "OUTBOUND_URL"} == {"real.vendor.io"}
    # Sinks that only reach local services are not reported either.
    assert [c.line_number for c in cands if c.category == "NETWORK_SINK"] == [5]


def test_minified_bundles_skipped_and_snippets_bounded(tmp_path):
    bundle = "var a=1;" + ";".join(f"x{i}='https://lib{i}.example.io'" for i in range(2000))
    write(tmp_path, "public/vs/editor.js", bundle + "\n")
    long_line = "x = '" + "y" * 10_000 + "'\n"
    write(tmp_path, "app.py", long_line + "requests.get('https://real.vendor.io')\n")
    report = scan(tmp_path)
    assert not any(c.file_path == "public/vs/editor.js" for c in report.candidates)
    assert any("minified" in w for w in report.warnings)
    assert all(len(c.code_snippet) < 5_000 for c in report.candidates)


def test_ip_versions_cidr_namespaces_and_locales(tmp_path):
    # Regressions from real projects (dify, open-webui, text-generation-webui).
    write(
        tmp_path,
        "app.py",
        "s.connect(('8.8.8.8', 53))\n"
        "ua = 'Mozilla/5.0 Chrome/130.0.0.0 Safari/537.36'\n"
        "msg = 'requires OceanBase >= 4.3.5.1'\n"
        "blocked = ['!224.0.0.0/4']\n"
        "icon = '<path d=\"M12 3.138.75.75 0 0 1\"/>'\n"
        "svg = 'http://www.w3.org/2000/svg'\n"
        "requests.get('https://www.w3.org/fetched-for-real')\n",
    )
    write(tmp_path, "requirements.txt", "brotlicffi==1.2.1.0\n")
    write(tmp_path, "src/locales/en/common.json", '{"help": "See https://docs.vendor.io"}\n')
    cands = scan(tmp_path).candidates
    ips = {c.destination for c in cands if c.matched_rule == "public_ip"}
    assert ips == {"8.8.8.8"}
    w3 = [c for c in cands if c.destination == "www.w3.org" and c.category == "OUTBOUND_URL"]
    assert [c.line_number for c in w3] == [7]  # namespace ignored, real fetch kept
    assert not any(c.file_path.startswith("src/locales") for c in cands)
