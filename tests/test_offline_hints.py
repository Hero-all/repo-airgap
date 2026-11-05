import json

from repo_airgap.cli import main
from repo_airgap.engine import rules_directory, scan
from repo_airgap.io import load_json


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def hints_by_id(root):
    return {h["id"]: h for h in scan(root).to_dict()["offline_hints"]}


def test_framework_switches_from_manifests(tmp_path):
    write(
        tmp_path,
        "web/package.json",
        json.dumps({"dependencies": {"next": "15"}, "devDependencies": {"@storybook/react": "8"}}),
    )
    write(tmp_path, "requirements.txt", "gradio>=4\nTransformers==4.44\nchromadb\n")
    write(tmp_path, ".env.example", "NEXT_TELEMETRY_DISABLED=1\n# GRADIO_ANALYTICS_ENABLED=False\n")
    hints = hints_by_id(tmp_path)
    assert {"nextjs", "storybook", "gradio", "huggingface-hub", "transformers", "chromadb"} <= set(hints)
    next_env = {e["name"]: e for e in hints["nextjs"]["env"]}
    # An example file documents a value; it is not configuration.
    assert next_env["NEXT_TELEMETRY_DISABLED"]["status"] == "example_only"
    assert next_env["NEXT_TELEMETRY_DISABLED"]["found_in"] == [".env.example:1"]
    # A commented-out assignment is not an active setting.
    assert hints["gradio"]["env"][0]["status"] == "missing"
    assert any("web/package.json" in e for e in hints["storybook"]["evidence"])


def test_ecosystems_do_not_cross_match(tmp_path):
    # A Python package called "next" must not trigger the Next.js hint.
    write(tmp_path, "requirements.txt", "next\n")
    write(tmp_path, "package.json", json.dumps({"dependencies": {"gradio": "1"}}))
    hints = hints_by_id(tmp_path)
    assert "nextjs" not in hints and "gradio" not in hints


def test_code_signals_and_docker(tmp_path):
    write(
        tmp_path,
        "app.py",
        "from transformers import AutoModel\n"
        "# AutoModel.from_pretrained('commented')\n"
        "model = AutoModel.from_pretrained('bert-base-uncased')\n"
        "import nltk; nltk.download('punkt')\n",
    )
    write(tmp_path, "src/layout.tsx", "import { Inter } from 'next/font/google'\n")
    write(
        tmp_path,
        "Dockerfile",
        "FROM node:20 AS build\n"
        "RUN npm ci\n"
        "FROM build AS test\n"
        "FROM registry.corp.local:5000/base/python:3.12\n"
        "FROM ghcr.io/org/runtime:1\n",
    )
    hints = hints_by_id(tmp_path)
    assert hints["hf_from_pretrained"]["evidence"] == ["app.py:3"]
    assert "nltk_download" in hints and "next_font_google" in hints
    assert hints["docker_run_fetch"]["evidence"] == ["Dockerfile:2"]
    # node:20 (Docker Hub) and ghcr.io are public; stage alias and private registry are not.
    assert hints["docker_from_image"]["evidence"] == ["Dockerfile:1", "Dockerfile:5"]


def test_file_trigger_and_no_hints_for_plain_repo(tmp_path):
    write(tmp_path, "main.py", "print('hello')\n")
    assert hints_by_id(tmp_path) == {}
    write(tmp_path, "svc/App.csproj", "<Project Sdk=\"Microsoft.NET.Sdk\"></Project>\n")
    assert "dotnet" in hints_by_id(tmp_path)


def test_cli_writes_env_suggestions_without_touching_source(tmp_path):
    write(tmp_path, "package.json", json.dumps({"dependencies": {"next": "15"}}))
    write(tmp_path, ".env", "NEXT_TELEMETRY_DISABLED=1\n")
    write(tmp_path, "requirements.txt", "gradio\n")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert main(["scan", str(tmp_path)]) == 0
    assert {p: p.read_bytes() for p in before} == before
    env = (tmp_path / ".airgap/offline.env").read_text("utf-8")
    lines = env.splitlines()
    assert "GRADIO_ANALYTICS_ENABLED=False" in lines
    # Configured in a committed .env: listed with its location, not repeated as an active line.
    assert "# NEXT_TELEMETRY_DISABLED=1" in lines and "NEXT_TELEMETRY_DISABLED=1" not in lines
    assert any(line.startswith("# configured in repository (.env:1 sets '1')") for line in lines)
    assert main(["report", str(tmp_path)]) == 0
    assert "Offline readiness hints" in (tmp_path / ".airgap/AIRGAP_REPORT.md").read_text("utf-8")


def test_rules_are_well_formed():
    data = load_json(rules_directory() / "offline_switches.json")
    ids = [fw["id"] for fw in data["frameworks"]] + [s["id"] for s in data["code_signals"]]
    assert len(ids) == len(set(ids))
    for fw in data["frameworks"]:
        assert fw["env"] and fw["reference"].startswith("https://")
        assert fw["ecosystem"] in ("npm", "pypi", "file")
        assert fw.get("packages") or fw.get("package_prefixes") or fw.get("files")


def test_requirement_url_forms_and_python_env_assignments(tmp_path):
    # Regressions found on a real project (text-generation-webui): wheel URLs and
    # os.environ assignments were not recognized.
    write(
        tmp_path,
        "requirements.txt",
        "https://github.com/org/gradio/releases/download/x/gradio-4.37.2+custom.21-py3-none-any.whl\n"
        "chromadb @ https://example.invalid/chromadb.tar.gz\n"
        "git+https://github.com/org/ray.git#egg=ray\n",
    )
    write(
        tmp_path,
        "server.py",
        "import os\n"
        "os.environ.update({'GRADIO_ANALYTICS_ENABLED': 'False'})\n"
        "os.environ['ANONYMIZED_TELEMETRY'] = 'False'\n"
        "os.getenv('RAY_USAGE_STATS_ENABLED')\n",
    )
    hints = hints_by_id(tmp_path)
    assert {"gradio", "chromadb", "ray"} <= set(hints)
    assert hints["gradio"]["env"][0]["status"] == "configured"
    assert hints["chromadb"]["env"][0]["status"] == "configured"
    # Reading a variable is not setting it.
    assert hints["ray"]["env"][0]["status"] == "missing"
