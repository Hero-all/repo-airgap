from dataclasses import asdict, dataclass, field

SEVERITIES = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
CATEGORIES = ("OUTBOUND_URL", "NETWORK_SINK", "TELEMETRY_SDK", "PROMO_UI", "ENV_CONFIG", "AUTH_PROVIDER")
DECISIONS = {"KEEP_CORE", "MOCK_SILENT", "REMOVE_UI", "SKIP_LEGAL", "NEEDS_REVIEW"}


@dataclass
class Candidate:
    id: str
    category: str
    severity: str
    file_path: str
    line_number: int
    matched_rule: str
    matched_content: str
    language: str
    context_start_line: int
    context_end_line: int
    code_snippet: str
    file_sha256: str
    destination: str | None = None
    port: int | None = None
    protocol: str | None = None
    sink_type: str | None = None
    function: str | None = None
    confidence: str = "MEDIUM"
    related_signals: list[str] = field(default_factory=list)
    decision: str = "NEEDS_REVIEW"


@dataclass
class ScanSummary:
    OUTBOUND_URL: int = 0
    NETWORK_SINK: int = 0
    TELEMETRY_SDK: int = 0
    PROMO_UI: int = 0
    ENV_CONFIG: int = 0
    AUTH_PROVIDER: int = 0


@dataclass
class ScanReport:
    repo_path: str
    scan_timestamp: str
    total_files_scanned: int
    summary: ScanSummary
    candidates: list[Candidate]
    warnings: list[str] = field(default_factory=list)
    suppressed_count: int = 0
    offline_hints: list[dict] = field(default_factory=list)
    limitations: list[str] = field(
        default_factory=lambda: [
            "Heuristic static analysis; no whole-program data-flow proof.",
            "Ignored, binary, generated, oversized and non-UTF8 files are not covered.",
            "Dynamic destinations and untriggered runtime paths may remain unknown.",
            "No source execution, dependency installation or external request during scan.",
        ]
    )

    def to_dict(self):
        return asdict(self)
