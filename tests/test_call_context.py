"""Common call forms that were missed before 0.4: aliases, instances, multi-line calls, constants."""

from repo_airgap.engine import scan


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sinks(root):
    return {
        (c.file_path, c.line_number): (c.destination, c.severity, c.related_signals)
        for c in scan(root).candidates
        if c.category == "NETWORK_SINK"
    }


def test_python_import_aliases_and_from_imports(tmp_path):
    write(
        tmp_path,
        "a.py",
        "import requests as rq\n"
        "from requests import post as send\n"
        "from urllib.request import urlopen as uo\n"
        "import httpx as hx\n"
        "rq.post(payload_url, json=p)\n"
        "send(target)\n"
        "uo(req)\n"
        "hx.get(u)\n"
        "data.get('key')\n"  # unrelated .get stays quiet
        "send_email(user)\n",
    )
    found = sinks(tmp_path)
    assert {n for f, n in found} == {5, 6, 7, 8}
    assert all("import_alias" in found[("a.py", n)][2] for n in (5, 6, 7, 8))


def test_client_instances_python(tmp_path):
    write(
        tmp_path,
        "b.py",
        "import requests, httpx, aiohttp\n"
        "session = requests.Session()\n"
        "session.get(remote)\n"
        "class C:\n"
        "    def __init__(self):\n"
        "        self.client = httpx.Client(timeout=5)\n"
        "    def run(self, d):\n"
        "        return self.client.post(target, json=d)\n"
        "async def f():\n"
        "    async with aiohttp.ClientSession() as s:\n"
        "        await s.post(target)\n"
        "other.client.post(x)\n",  # attribute of an unrelated object
    )
    found = sinks(tmp_path)
    lines = {n for f, n in found}
    assert {3, 8, 11} <= lines and 12 not in lines
    assert "client_instance" in found[("b.py", 3)][2]
    # The constructor line closes on itself; the next line must not leak into its label.
    assert found[("b.py", 2)][0] == "dynamic"


def test_js_packages_instances_and_xhr(tmp_path):
    write(
        tmp_path,
        "c.ts",
        "import ky from 'ky';\n"
        "import got from 'got';\n"
        "import { request as undiciRequest } from 'undici';\n"
        "const fetchFn = require('node-fetch');\n"
        "const api = axios.create({ baseURL: process.env.API_BASE_URL });\n"
        "const COLLECT = 'https://collect.analytics-vendor.io/e';\n"
        "await ky.post(u, { json: b });\n"
        "await got(u);\n"
        "await undiciRequest(u);\n"
        "await fetchFn(u);\n"
        "await api.post('/events', b);\n"
        "const xhr = new XMLHttpRequest();\n"
        "xhr.open('POST', COLLECT);\n"
        "const es = new EventSource(streamUrl);\n"
        "$.ajax({ url: u });\n"
        "map.get(key);\n",
    )
    found = sinks(tmp_path)
    assert {n for f, n in found} == {5, 7, 8, 9, 10, 11, 13, 14, 15}
    assert found[("c.ts", 5)][:2] == ("config:API_BASE_URL", "MEDIUM")
    assert found[("c.ts", 11)][:2] == ("self", "LOW")
    assert found[("c.ts", 13)][:2] == ("collect.analytics-vendor.io", "HIGH")


def test_multiline_call_takes_literal_from_its_own_arguments(tmp_path):
    write(
        tmp_path,
        "d.py",
        "r = requests.post(\n"
        "    'https://events.vendor-telemetry.io/v1',\n"
        "    json=payload,\n"
        ")\n"
        "s = requests.get(\n"
        "    'http://localhost:8080/health',\n"
        ")\n"
        "requests.get(u)\n"
        "x = 'https://unrelated.vendor.io'\n",
    )
    found = sinks(tmp_path)
    assert found[("d.py", 1)][:2] == ("events.vendor-telemetry.io", "HIGH")
    assert "multiline_call" in found[("d.py", 1)][2]
    assert ("d.py", 5) not in found  # resolves to a loopback target, like a same-line literal
    assert found[("d.py", 8)][:2] == ("dynamic", "HIGH")  # balanced call: no look-ahead


def test_constants_config_references_and_concatenation(tmp_path):
    write(
        tmp_path,
        "e.py",
        "import os\n"
        "TELEMETRY_URL = 'https://t.vendor-telemetry.io/v1'\n"
        "INTERNAL = 'http://10.0.0.5/api'\n"
        "requests.post(TELEMETRY_URL, json=d)\n"
        "requests.post(INTERNAL)\n"
        "requests.get(settings.TELEMETRY_ENDPOINT)\n"
        "requests.get(os.environ['COLLECTOR_URL'])\n"
        "requests.get(API_BASE_URL + '/x')\n"
        "requests.get('https://' + host + '/ping')\n"
        "requests.get(url)\n",
    )
    found = sinks(tmp_path)
    assert found[("e.py", 4)][:2] == ("t.vendor-telemetry.io", "HIGH")
    assert ("e.py", 5) not in found
    assert found[("e.py", 6)][:2] == ("config:TELEMETRY_ENDPOINT", "MEDIUM")
    assert found[("e.py", 7)][:2] == ("config:COLLECTOR_URL", "MEDIUM")
    assert found[("e.py", 8)][:2] == ("config:API_BASE_URL", "MEDIUM")
    assert found[("e.py", 9)][:2] == ("dynamic", "HIGH")
    assert found[("e.py", 10)][:2] == ("dynamic", "HIGH")
    urls = [c for c in scan(tmp_path).candidates if c.category == "OUTBOUND_URL" and c.line_number == 9]
    assert urls and urls[0].matched_rule == "concatenated_url"


def test_aliases_do_not_leak_across_files(tmp_path):
    write(tmp_path, "a.py", "import requests as rq\n")
    write(tmp_path, "b.py", "rq.post(u)\nsession.get(u)\n")
    assert not {k for k in sinks(tmp_path) if k[0] == "b.py"}


def test_false_positives_seen_on_real_projects(tmp_path):
    # lobe-chat: a Worker's `fetch(request, env) {` handler must not borrow a label from its body;
    # undici Agent is a connection dispatcher, not a request.
    write(
        tmp_path,
        "worker.ts",
        "import { Agent, request } from 'undici';\n"
        "export default {\n"
        "  async fetch(request: Request, env: Env) {\n"
        "    const r = await fetch(env.AUTH_SERVICE_URL);\n"
        "  },\n"
        "};\n"
        "setGlobalDispatcher(new Agent());\n",
    )
    found = sinks(tmp_path)
    assert found[("worker.ts", 3)][0] == "dynamic"
    assert found[("worker.ts", 4)][0] == "config:AUTH_SERVICE_URL"
    assert ("worker.ts", 7) not in found
    # A name bound to two different URLs in one file is not resolved to either.
    write(
        tmp_path,
        "multi.py",
        "if PROD:\n    URL = 'https://a.vendor-one.io'\nelse:\n    URL = 'https://b.vendor-two.io'\n"
        "requests.get(URL)\n",
    )
    assert sinks(tmp_path)[("multi.py", 5)][0] == "dynamic"
