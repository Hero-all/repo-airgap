"""Optional mitmproxy addon: normalized metadata only, without headers/body/query."""

import json
from datetime import datetime, timezone
from pathlib import Path

from mitmproxy import ctx


class Exporter:
    def __init__(self):
        self.connections = []

    def load(self, loader):
        loader.add_option("airgap_output", str, "runtime.json", "Normalized repo-airgap observation file")

    def response(self, flow):
        self.record(flow, "response:" + str(flow.response.status_code))

    def error(self, flow):
        self.record(flow, "failed")

    def record(self, flow, outcome):
        self.connections.append(
            {
                "destination": flow.request.host,
                "port": flow.request.port,
                "protocol": flow.request.scheme,
                "outcome": outcome,
                "timestamp": datetime.fromtimestamp(flow.request.timestamp_start, timezone.utc).isoformat(),
            }
        )

    def done(self):
        output = Path(ctx.options.airgap_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "coverage": "Only flows routed through this mitmproxy instance",
                    "connections": self.connections,
                },
                indent=2,
            ),
            encoding="utf-8",
        )


addons = [Exporter()]
