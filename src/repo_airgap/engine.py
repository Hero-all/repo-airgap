from collections import Counter
from pathlib import Path

from .analyzer.adapters import semgrep_findings
from .analyzer.context_extractor import extract_context
from .io import load_json, redact, sha256, utcnow
from .models import SEVERITIES, Candidate, ScanReport, ScanSummary
from .patcher.dockerfile_auditor import config_match
from .scanner.auth_detector import AUTH_SIGNALS
from .scanner.dep_auditor import dependency_names, imported_packages
from .scanner.file_walker import read_source, walk_files
from .scanner.sink_hunter import network_sinks, related_signals
from .scanner.ui_detector import ui_matches
from .scanner.url_hunter import comment_line, destinations, domain_matches

LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".vue": "vue",
}


def rules_directory():
    bundled = Path(__file__).parent / "rules"
    return bundled if bundled.is_dir() else Path(__file__).resolve().parents[2] / "rules"


def scan(root, *, rules_dir=None, ignore_ids=(), min_severity="INFO", semgrep_report=None):
    rules = Path(rules_dir) if rules_dir else rules_directory()
    telemetry = load_json(rules / "telemetry_domains.json")
    whitelist = load_json(rules / "whitelist_domains.json")["domains"]
    promo = load_json(rules / "promo_keywords.json")["keywords"]
    packages = set(telemetry["telemetry_packages"])
    warnings, candidates, documents = [], [], {}

    def add(path, lines, digest, n, category, rule, content, severity="MEDIUM", **extra):
        start, end, snippet, function = extract_context(lines, n)
        candidates.append(
            Candidate(
                id="",
                category=category,
                severity=severity,
                file_path=path.relative_to(root).as_posix(),
                line_number=n,
                matched_rule=rule,
                matched_content=redact(content),
                language=LANGUAGES.get(path.suffix.lower(), path.suffix.lstrip(".") or "text"),
                context_start_line=start,
                context_end_line=end,
                code_snippet=snippet,
                file_sha256=digest,
                function=function,
                **extra,
            )
        )

    for path in walk_files(root, warnings):
        try:
            doc = read_source(path)
        except OSError as e:
            warnings.append(f"Read failed: {path.relative_to(root)} ({type(e).__name__})")
            continue
        if doc is None:
            warnings.append(f"Skipped non-UTF8/binary/oversized file: {path.relative_to(root).as_posix()}")
            continue
        raw, text = doc
        lines, digest = text.splitlines(), sha256(raw)
        documents[path] = (lines, digest)
        if path.suffix.lower() in (".md", ".rst", ".txt") and not path.name.startswith("requirements"):
            continue
        deps = dependency_names(path, text, warnings)
        for n, line in enumerate(lines, 1):
            if comment_line(line):
                continue
            if AUTH_SIGNALS.search(line):
                add(path, lines, digest, n, "AUTH_PROVIDER", "google_authentication", line.strip(), "MEDIUM")
            sinks = network_sinks(line)
            nearby = "\n".join(lines[max(0, n - 6) : n + 5])
            signals = related_signals(nearby) if sinks else []
            targets = list(destinations(line, whitelist))
            for target in targets:
                severity = target.pop("severity")
                content = target.pop("content")
                rule = target.pop("rule")
                if domain_matches(target["destination"], telemetry["telemetry_domains"]):
                    target_signals = [*signals, "known_telemetry_domain"]
                else:
                    target_signals = signals
                add(
                    path,
                    lines,
                    digest,
                    n,
                    "OUTBOUND_URL",
                    rule,
                    content,
                    severity,
                    **target,
                    sink_type=",".join(sinks) or None,
                    related_signals=target_signals,
                )
            for sink in sinks:
                add(
                    path,
                    lines,
                    digest,
                    n,
                    "NETWORK_SINK",
                    "sink_" + sink,
                    line.strip(),
                    "HIGH",
                    destination=targets[0]["destination"] if len(targets) == 1 else "dynamic",
                    port=targets[0].get("port") if len(targets) == 1 else None,
                    sink_type=sink,
                    related_signals=signals,
                )
            for pkg in sorted(packages):
                is_dep = pkg in deps and (f'"{pkg}"' in line or re_package(pkg, line))
                is_import = any(
                    p == pkg or p.replace("_", "-") == pkg or p.startswith(pkg + "/")
                    for p in imported_packages(line)
                )
                if is_dep or is_import:
                    add(path, lines, digest, n, "TELEMETRY_SDK", "telemetry_package", pkg)
            words = ui_matches(path, line, promo)
            if words:
                add(path, lines, digest, n, "PROMO_UI", "promo_keyword", ", ".join(words), "LOW")
            if config_match(path, line):
                add(path, lines, digest, n, "ENV_CONFIG", "external_config_flag", line.strip())

    if semgrep_report:
        for path, n, rule in semgrep_findings(semgrep_report, root):
            if path not in documents:
                warnings.append(f"Imported finding outside scanned scope: {path.relative_to(root)}")
                continue
            lines, digest = documents[path]
            if n > len(lines):
                warnings.append(f"Imported line out of range: {path.relative_to(root)}:{n}")
                continue
            add(
                path,
                lines,
                digest,
                n,
                "NETWORK_SINK",
                "semgrep:" + rule,
                lines[n - 1],
                related_signals=["external_analysis_unclassified"],
            )
    candidates.sort(key=lambda c: (c.file_path, c.line_number, c.matched_rule, c.matched_content))
    # IDs do not change merely because a severity/rule filter was applied.
    for i, c in enumerate(candidates, 1):
        c.id = f"CAND-{i:05d}"
    filtered = [
        c
        for c in candidates
        if c.matched_rule not in ignore_ids and SEVERITIES[c.severity] >= SEVERITIES[min_severity]
    ]
    counts = Counter(c.category for c in filtered)
    return ScanReport(
        str(root),
        utcnow(),
        len(documents),
        ScanSummary(**counts),
        filtered,
        warnings,
        len(candidates) - len(filtered),
    )


def re_package(package, line):
    # Manifest names can have Python requirement version constraints.
    import re

    return bool(re.search(r"(?<![\w@/-])" + re.escape(package) + r"(?=[\s\[<>=!~;\"']|$)", line))
