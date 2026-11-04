import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from repo_airgap.io import load_json


def test_mitmproxy_exporter_records_metadata_without_secrets(tmp_path, monkeypatch):
    output = tmp_path / "runtime.json"
    fake = SimpleNamespace(ctx=SimpleNamespace(options=SimpleNamespace(airgap_output=str(output))))
    monkeypatch.setitem(sys.modules, "mitmproxy", fake)
    path = Path(__file__).resolve().parents[1] / "scripts/mitmproxy_export.py"
    spec = importlib.util.spec_from_file_location("test_mitmproxy_export", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    flow = SimpleNamespace(
        request=SimpleNamespace(
            host="telemetry.vendor.example",
            port=443,
            scheme="https",
            timestamp_start=0,
            headers={"Authorization": "secret"},
            content=b"private payload",
        ),
        response=SimpleNamespace(status_code=200),
    )
    exporter = module.Exporter()
    exporter.response(flow)
    exporter.done()
    data = load_json(output)
    assert data["connections"][0]["destination"] == "telemetry.vendor.example"
    assert "secret" not in output.read_text()
    assert "private payload" not in output.read_text()
