from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from .analyzer.adapters import semgrep_findings
from .analyzer.context_extractor import extract_context
from .io import load_json, redact, sha256, utcnow
from .models import SEVERITIES, Candidate, ScanReport, ScanSummary
from .patcher.dockerfile_auditor import config_match
from .scanner.auth_detector import AUTH_SIGNALS
from .scanner.call_context import FileContext, call_span, resolve_argument
from .scanner.dep_auditor import dependency_names, imported_packages
from .scanner.file_walker import is_locale_data, is_test_path, looks_minified, read_source, walk_files
from .scanner.offline_hints import OfflineHintCollector
from .scanner.sink_hunter import CONFIG_NAME, dynamic_destination, network_sinks, related_signals
from .scanner.ui_detector import ui_matches
from .scanner.url_hunter import (
    IDENTIFIER_HOSTS,
    comment_line,
    destinations,
    domain_matches,
    is_local_host,
    is_reference_link,
)

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


def scan(
    root, *, rules_dir=None, ignore_ids=(), min_severity="INFO", semgrep_report=None, include_tests=False
):
    rules = Path(rules_dir) if rules_dir else rules_directory()
    telemetry = load_json(rules / "telemetry_domains.json")
    whitelist = load_json(rules / "whitelist_domains.json")["domains"]
    promo = load_json(rules / "promo_keywords.json")["keywords"]
    packages = set(telemetry["telemetry_packages"])
    switches = rules / "offline_switches.json"
    hints = OfflineHintCollector(load_json(switches) if switches.is_file() else {})
    warnings, candidates, documents = [], [], {}
    skipped = {"tests": 0, "minified": 0, "local_urls": 0, "identifier_urls": 0, "locale": 0}

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
        rel = path.relative_to(root).as_posix()
        hints.add_file(rel)
        if path.suffix.lower() in (".md", ".mdx", ".rst", ".txt") and not path.name.startswith("requirements"):
            continue
        deps = dependency_names(path, text, warnings)
        hints.add_manifest(rel, deps)
        # Manifests above still count for hints; test code and minified bundles do not produce candidates.
        if not include_tests and is_test_path(rel):
            skipped["tests"] += 1
            continue
        if is_locale_data(rel):
            skipped["locale"] += 1
            continue
        if looks_minified(text, lines):
            skipped["minified"] += 1
            warnings.append(f"Skipped likely minified/vendored bundle: {rel}")
            continue
        context = FileContext(lines, path.suffix)
        for n, line in enumerate(lines, 1):
            is_comment = comment_line(line)
            hints.add_line(rel, n, line, is_comment)
            if is_comment:
                continue
            if AUTH_SIGNALS.search(line):
                add(path, lines, digest, n, "AUTH_PROVIDER", "google_authentication", line.strip(), "MEDIUM")
            sinks = network_sinks(line)
            via = context.call_kind(line)
            if via and "http_client" not in sinks:
                sinks.append("http_client")
            nearby = "\n".join(lines[max(0, n - 6) : n + 5])
            signals = related_signals(nearby) if sinks else []
            if via:
                signals.append(via)
            found = list(destinations(line, whitelist))
            targets = [t for t in found if not is_local_host(t["destination"])]
            skipped["local_urls"] += len(found) - len(targets)
            if found and not targets:
                sinks = []  # every destination on this line is loopback/private/service-local
            if not sinks:
                kept = [t for t in targets if t["destination"] not in IDENTIFIER_HOSTS]
                skipped["identifier_urls"] += len(targets) - len(kept)
                targets = kept
            for target in targets:
                severity = target.pop("severity")
                content = target.pop("content")
                rule = target.pop("rule")
                if domain_matches(target["destination"], telemetry["telemetry_domains"]):
                    target_signals = [*signals, "known_telemetry_domain"]
                elif not sinks and is_reference_link(line, content):
                    target_signals = [*signals, "reference_link"]
                    severity = "LOW"
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
            if sinks and len(targets) != 1:
                dynamic, dynamic_severity, dynamic_signal = sink_destination(
                    lines, n, whitelist, context, bool(targets)
                )
            else:
                dynamic = dynamic_severity = dynamic_signal = None
            if dynamic is not None and dynamic_severity is None:
                sinks = []  # resolved to a loopback/private/service-local constant
            for sink in sinks:
                if len(targets) == 1:
                    destination, severity = targets[0]["destination"], "HIGH"
                else:
                    destination, severity = dynamic, dynamic_severity
                add(
                    path,
                    lines,
                    digest,
                    n,
                    "NETWORK_SINK",
                    "sink_" + sink,
                    line.strip(),
                    severity,
                    destination=destination,
                    port=targets[0].get("port") if len(targets) == 1 else None,
                    sink_type=sink,
                    related_signals=signals + ([dynamic_signal] if dynamic_signal else []),
                )
            imports = imported_packages(line) if ("import" in line or "require" in line or "from" in line) else []
            for pkg in sorted(packages):
                is_dep = pkg in deps and (f'"{pkg}"' in line or re_package(pkg, line))
                is_import = any(
                    p == pkg or p.replace("_", "-") == pkg or p.startswith(pkg + "/") for p in imports
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
    if skipped["tests"]:
        warnings.append(
            f"Skipped {skipped['tests']} test/fixture files (use --include-tests to scan them)."
        )
    if skipped["local_urls"]:
        warnings.append(f"Ignored {skipped['local_urls']} loopback/private/single-label URL destinations.")
    if skipped["identifier_urls"]:
        warnings.append(
            f"Ignored {skipped['identifier_urls']} identifier URIs (XML namespaces/JSON Schema) without a network call."
        )
    if skipped["locale"]:
        warnings.append(f"Skipped {skipped['locale']} translation/locale data files (display text).")
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
        hints.results(),
    )


def sink_destination(lines, n, whitelist, context, has_targets):
    """(destination, severity, signal) for a sink without exactly one literal target on its line.

    Severity None means the destination resolved to a local host and the sink should be dropped.
    """
    span = call_span(lines, n)
    code = " ".join(part.strip() for part in span)
    if not has_targets and len(span) > 1:
        # requests.get(\n    "https://host/x",\n): the literal lives inside the same call.
        hosts = {
            t["destination"]
            for part in span[1:]
            if not comment_line(part)
            for t in destinations(part, whitelist)
            if t["destination"] not in IDENTIFIER_HOSTS
        }
        external = {h for h in hosts if not is_local_host(h)}
        if len(external) == 1:
            return external.pop(), "HIGH", "multiline_call"
        if hosts and not external:
            return "local", None, None
    destination = dynamic_destination(code)
    if destination == "dynamic":
        resolved, signal = resolve_argument(code, context.constants, CONFIG_NAME)
        if resolved and resolved.startswith("config:"):
            return resolved, "MEDIUM", signal
        if resolved:
            host = (urlsplit(resolved).hostname or "dynamic").lower()
            if is_local_host(host):
                return host, None, None
            return host, "HIGH", signal
        return "dynamic", "HIGH", None
    return destination, {"self": "LOW"}.get(destination, "MEDIUM"), None


def re_package(package, line):
    # Manifest names can have Python requirement version constraints.
    import re

    return bool(re.search(r"(?<![\w@/-])" + re.escape(package) + r"(?=[\s\[<>=!~;\"']|$)", line))
