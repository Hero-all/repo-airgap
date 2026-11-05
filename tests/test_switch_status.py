"""Switch status must separate "variable found", "value matches" and "deployment effect"."""

import json

from repo_airgap.cli import main
from repo_airgap.engine import scan


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def env_status(root, framework="nextjs"):
    hints = {h["id"]: h for h in scan(root).to_dict()["offline_hints"]}
    return {e["name"]: e for e in hints[framework]["env"]}


def next_project(root):
    write(root, "package.json", json.dumps({"dependencies": {"next": "14.0.0"}}))


def test_reported_case_example_value_0_is_not_set_and_env_file_is_active(tmp_path):
    # Reproduces the review finding: .env.example with NEXT_TELEMETRY_DISABLED=0 was "present"
    # and offline.env claimed "# already set: NEXT_TELEMETRY_DISABLED=1".
    next_project(tmp_path)
    write(tmp_path, ".env.example", "NEXT_TELEMETRY_DISABLED=0\n")
    item = env_status(tmp_path)["NEXT_TELEMETRY_DISABLED"]
    assert item["status"] == "example_only"
    assert item["evidence"][0]["value"] == "0" and item["evidence"][0]["source"] == "example"
    assert item["deployment_effect"] == "unverified"
    assert main(["scan", str(tmp_path)]) == 0
    lines = (tmp_path / ".airgap/offline.env").read_text("utf-8").splitlines()
    assert "NEXT_TELEMETRY_DISABLED=1" in lines
    assert not any("already set" in line for line in lines)
    assert any("example file only" in line and "'0'" in line for line in lines)


def test_wrong_value_in_real_config_is_mismatch(tmp_path):
    next_project(tmp_path)
    write(tmp_path, "Dockerfile", "FROM node:20\nENV NEXT_TELEMETRY_DISABLED=0\n")
    item = env_status(tmp_path)["NEXT_TELEMETRY_DISABLED"]
    assert item["status"] == "mismatch"
    assert item["evidence"][0]["source"] == "dockerfile"
    assert main(["scan", str(tmp_path)]) == 0
    lines = (tmp_path / ".airgap/offline.env").read_text("utf-8").splitlines()
    assert "NEXT_TELEMETRY_DISABLED=1" in lines
    assert any(line.startswith("# value differs: Dockerfile:2 sets '0'") for line in lines)


def test_conflicting_and_matching_values(tmp_path):
    next_project(tmp_path)
    write(tmp_path, "Dockerfile", "ENV NEXT_TELEMETRY_DISABLED 1\n")
    write(tmp_path, "docker-compose.yml", "services:\n  web:\n    environment:\n      - NEXT_TELEMETRY_DISABLED=0\n")
    assert env_status(tmp_path)["NEXT_TELEMETRY_DISABLED"]["status"] == "conflict"
    write(tmp_path, "docker-compose.yml", "services:\n  web:\n    environment:\n      NEXT_TELEMETRY_DISABLED: \"1\"\n")
    assert env_status(tmp_path)["NEXT_TELEMETRY_DISABLED"]["status"] == "configured"


def test_interpolated_or_inherited_values_are_unresolved(tmp_path):
    next_project(tmp_path)
    write(tmp_path, "docker-compose.yml", "services:\n  web:\n    environment:\n      - NEXT_TELEMETRY_DISABLED\n")
    assert env_status(tmp_path)["NEXT_TELEMETRY_DISABLED"]["status"] == "unresolved"
    write(
        tmp_path,
        "docker-compose.yml",
        "services:\n  web:\n    environment:\n      NEXT_TELEMETRY_DISABLED: ${NEXT_TELEMETRY_DISABLED:-0}\n",
    )
    item = env_status(tmp_path)["NEXT_TELEMETRY_DISABLED"]
    assert item["status"] == "unresolved" and item["evidence"][0]["value"] is None


def test_reads_and_comparisons_are_not_settings(tmp_path):
    next_project(tmp_path)
    write(
        tmp_path,
        "src/a.py",
        "import os\nos.environ.get('NEXT_TELEMETRY_DISABLED', '0')\nos.getenv(\"NEXT_TELEMETRY_DISABLED\", '1')\n"
        "if os.environ['NEXT_TELEMETRY_DISABLED'] == '1': pass\n",
    )
    write(
        tmp_path,
        "src/b.ts",
        "if (process.env.NEXT_TELEMETRY_DISABLED === '1') {}\n"
        "const off = process.env.NEXT_TELEMETRY_DISABLED !== undefined\n"
        "type Env = { NEXT_TELEMETRY_DISABLED: string }\n",
    )
    assert env_status(tmp_path)["NEXT_TELEMETRY_DISABLED"]["status"] == "missing"


def test_code_assignments_and_npm_script_prefix(tmp_path):
    write(
        tmp_path,
        "package.json",
        json.dumps({"dependencies": {"next": "14"}, "scripts": {"build": "NEXT_TELEMETRY_DISABLED=1 next build"}}),
    )
    assert env_status(tmp_path)["NEXT_TELEMETRY_DISABLED"]["status"] == "configured"
    write(tmp_path, "requirements.txt", "gradio\n")
    write(tmp_path, "app.py", "import os\nos.environ['GRADIO_ANALYTICS_ENABLED'] = flag\n")
    # Assigned from a variable: the value cannot be read statically.
    assert env_status(tmp_path, "gradio")["GRADIO_ANALYTICS_ENABLED"]["status"] == "unresolved"
    write(tmp_path, "app.py", "process_env = None\nimport os\nos.environ.setdefault('GRADIO_ANALYTICS_ENABLED', 'false')\n")
    assert env_status(tmp_path, "gradio")["GRADIO_ANALYTICS_ENABLED"]["status"] == "configured"


def test_documented_alternative_spellings_only_where_confirmed(tmp_path):
    write(tmp_path, "requirements.txt", "huggingface-hub\nchromadb\n")
    write(tmp_path, "deploy/app.env", "HF_HUB_OFFLINE=true\nHF_HUB_DISABLE_TELEMETRY=2\nANONYMIZED_TELEMETRY=0\n")
    hf = env_status(tmp_path, "huggingface-hub")
    assert hf["HF_HUB_OFFLINE"]["status"] == "configured"  # huggingface_hub accepts 1/ON/YES/TRUE
    assert hf["HF_HUB_DISABLE_TELEMETRY"]["status"] == "mismatch"
    assert env_status(tmp_path, "chromadb")["ANONYMIZED_TELEMETRY"]["status"] == "configured"
    # Next.js has no confirmed alternative spelling: "true" is reported, not silently accepted.
    next_project(tmp_path)
    write(tmp_path, "Dockerfile", "ENV NEXT_TELEMETRY_DISABLED=true\n")
    assert env_status(tmp_path)["NEXT_TELEMETRY_DISABLED"]["status"] == "mismatch"


def test_example_and_docs_locations_never_count_as_configuration(tmp_path):
    next_project(tmp_path)
    for name in (".env.sample", "env.example", "docker-compose.example.yml", "docs/deploy.env", ".env.template"):
        write(tmp_path, name, "NEXT_TELEMETRY_DISABLED=1\n")
    item = env_status(tmp_path)["NEXT_TELEMETRY_DISABLED"]
    assert item["status"] == "example_only"
    assert {e["source"] for e in item["evidence"]} == {"example"}
