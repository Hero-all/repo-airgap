# Optional analysis and runtime evidence

## Semgrep

Install Semgrep separately when needed and run trusted local rules. Import an
existing report without executing Semgrep:

```text
repo-airgap scan /target --semgrep-report /reports/semgrep.json
```

Semgrep JSON and SARIF `runs[].results[]` with source locations are supported.
Imported locations must be inside the scanned scope. The tool preserves the
rule ID under `semgrep:<rule>` and treats findings as unclassified evidence.
Version 0.1 does not import a complete taint trace or follow SARIF artifact-index
references; use reports with explicit artifact URIs.

## mitmproxy

The bundled addon exports normalized observation JSON:

```text
mitmdump -s /repo-airgap/scripts/mitmproxy_export.py --set airgap_output=/reports/runtime.json
```

This is an optional separately installed tool; importing evidence does not
install it or configure host proxy/certificates. Capture only the isolated test
application's traffic. HTTPS visibility depends on proxy and certificate setup;
applications may bypass the proxy. DNS/raw socket traffic needs other evidence.

## Runtime schema

`verify --observations /reports/runtime.json` accepts this JSON; OpenSnitch records
can be mapped into it manually. No native OpenSnitch integration is supplied.

```json
{
  "coverage": "Startup and settings view through a configured proxy; DNS not covered",
  "connections": [
    {
      "destination": "telemetry.vendor.example",
      "port": 443,
      "protocol": "https",
      "process": "test-app",
      "timestamp": "2026-10-15T12:00:00Z",
      "scenario": "startup",
      "outcome": "blocked"
    }
  ]
}
```

The CLI marks this as imported evidence, not verified isolation. Validate the
capture source and describe uncovered paths. No backend is required for scan,
plan, apply, report or revert. Do not change the user's system firewall or
certificate store automatically.
