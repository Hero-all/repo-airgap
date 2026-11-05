"""Offline-readiness hints: official opt-out switches and build/runtime download signals.

Hints never create edit candidates. They point at configuration a deployer can set
without patching source, and at downloads that must be staged before going offline.

Switch evidence is classified on three separate axes:

* found     - where the variable name is *assigned* (reads such as os.getenv / process.env.X === ...
              are not settings);
* value     - whether the literal value equals the documented recommendation;
* effect    - never claimed. A value in a Dockerfile, compose file or committed .env shows intent in
              the repository, not that the deployed process receives it. Example/template files and
              documentation are recorded but are not counted as configuration at all.
"""

import fnmatch
import re

PUBLIC_REGISTRIES = {
    "docker.io",
    "registry-1.docker.io",
    "ghcr.io",
    "quay.io",
    "gcr.io",
    "mcr.microsoft.com",
    "public.ecr.aws",
    "registry.k8s.io",
    "nvcr.io",
}
STAGE = re.compile(r"^\s*FROM\s+\S+(?:\s+AS\s+([\w.-]+))?", re.I)
MAX_LOCATIONS = 5

# Files that document or template configuration. Values there are suggestions for a human,
# frequently placeholders, and never loaded by the application as-is.
EXAMPLE_FILE = re.compile(
    r"\.(?:example|sample|template|tmpl|dist)$"  # .env.example, config.yml.sample, .env.dist
    r"|(?:^|[./_-])(?:example|examples|sample|samples)(?:[./_-]|$)",  # example.env, compose.sample.yml
    re.I,
)
DOC_DIR = re.compile(r"(?:^|/)(?:docs?|examples?|samples?|documentation)/", re.I)
CODE_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte", ".go")
VALUE = r"(\"[^\"]*\"|'[^']*'|`[^`]*`|[^\s,;}#)\]]*)"


def _file_matches(name, patterns):
    name = name.lower()
    return any(fnmatch.fnmatch(name, p.lower()) for p in patterns)


def manifest_ecosystem(name):
    name = name.lower()
    if name == "package.json":
        return "npm"
    if name == "pyproject.toml" or (name.startswith("requirements") and name.endswith(".txt")):
        return "pypi"
    if name == "go.mod":
        return "go"
    return None


def _public_image(image):
    first = image.split("/", 1)[0]
    if "/" not in image or ("." not in first and ":" not in first and first != "localhost"):
        return True  # Docker Hub short name, e.g. node:20 or library/python
    return first.lower() in PUBLIC_REGISTRIES


def source_kind(rel_path):
    """Where a setting lives. Only non-example kinds count as repository configuration."""
    name = rel_path.rsplit("/", 1)[-1].lower()
    if DOC_DIR.search(rel_path) or EXAMPLE_FILE.search(name):
        return "example"
    if _file_matches(name, ["dockerfile*", "containerfile*", "*.dockerfile"]):
        return "dockerfile"
    if _file_matches(name, ["docker-compose*.y*ml", "compose*.y*ml"]):
        return "compose"
    if name == ".env" or name.startswith(".env."):
        return "env_file"
    if rel_path.startswith((".github/", ".gitlab", ".circleci/")) or name in (".gitlab-ci.yml", "jenkinsfile"):
        return "ci"
    if name.endswith(CODE_SUFFIXES):
        return "code"
    return "config"


def _literal(raw, kind):
    """Literal value of an assignment, or None if it is computed/interpolated."""
    raw = raw.strip()
    quoted = len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'`"
    value = raw[1:-1] if quoted else raw
    if not quoted and not value:
        return None  # `X:` / `- X`: null or inherited from the host environment
    if "${" in value or "$(" in value or value.startswith("$") or "{{" in value:
        return None
    if kind == "code" and not quoted and not re.fullmatch(r"-?\d+(?:\.\d+)?|True|False|true|false", value):
        return None  # a variable or expression, e.g. os.environ["X"] = flag
    return value


class _EnvAssignments:
    """Recognize *assignments* of known variable names; reads and comparisons are ignored.

    Supported: dotenv/shell (`X=1`, `export X=1`), Dockerfile `ENV`/`ARG`, YAML/compose
    (`X: 1`, `- X=1`, pass-through `- X`), JSON/dict literals with quoted keys, inline npm-script
    prefixes (`"X=1 next build"`), and explicit APIs (`os.environ[...] =`, `os.environ.setdefault`,
    `os.putenv`, `process.env.X =`, Go `os.Setenv`). Unquoted object keys in code are not treated as
    environment settings.
    """

    def __init__(self, names):
        n = "(" + "|".join(map(re.escape, sorted(names, key=len, reverse=True))) + ")"
        q = r"[\"']"
        eq = r"\s*=(?!=)\s*"
        self.prefilter = re.compile(r"(?<![\w-])" + n + r"(?![\w-])")
        # Explicit environment APIs; valid in any file type. Each has groups (name, value).
        self.api = [
            re.compile(r"(?:os\.environ|process\.env)\[\s*" + q + n + q + r"\s*\]" + eq + VALUE),
            re.compile(r"process\.env\." + n + r"(?![\w$])" + eq + VALUE),
            re.compile(r"(?:os\.environ\.setdefault|os\.putenv|os\.Setenv)\(\s*" + q + n + q + r"\s*,\s*" + VALUE),
        ]
        # Quoted keys inside a dict/object literal: os.environ.update({...}), env={...}, JSON.
        self.quoted_key = re.compile(q + n + q + r"\s*:\s*" + VALUE)
        # Configuration-file syntax, tried in order; the first match wins.
        self.config = [
            re.compile(r"^\s*(?:export\s+|ENV\s+|ARG\s+|-\s+|set\s+)?" + q + "?" + n + q + "?" + eq + VALUE, re.I),
            re.compile(r"^\s*ENV\s+" + n + r"\s+" + VALUE, re.I),
            re.compile(r"^\s*ENV\s+.*?(?<![\w-])" + n + "=" + VALUE, re.I),
            re.compile(r"^\s*-?\s*" + q + "?" + n + q + r"?\s*:\s*" + VALUE),
            re.compile(r"^\s*-\s+" + q + "?" + n + q + r"?\s*()$"),  # compose pass-through from host
            re.compile(r"(?:^|[\s\"'&;])" + n + "=(?!=)" + VALUE),
        ]

    def find(self, line, kind):
        if not self.prefilter.search(line):
            return []
        found = [(m[1], _literal(m[2], "code")) for p in self.api for m in p.finditer(line)]
        if kind == "code":
            found.extend((m[1], _literal(m[2], "code")) for m in self.quoted_key.finditer(line))
            return found
        if not found:
            for pattern in (self.quoted_key, *self.config):
                m = pattern.search(line)
                if m:
                    found.append((m[1], _literal(m[2], kind)))
                    break
        return found


def value_matches(recommended, value, accept=None):
    """Case-insensitive equality with the documented value, or one of the rule's accepted values.

    Other spellings (e.g. "true" for a documented "1") are deliberately reported as mismatch:
    whether a framework accepts them depends on its version and parser.
    """
    if value is None:
        return False
    if accept == "*":
        return bool(value.strip())
    allowed = {str(recommended).lower(), *(str(a).lower() for a in (accept or []))}
    return value.strip().lower() in allowed


class OfflineHintCollector:
    def __init__(self, rules):
        self.frameworks = rules.get("frameworks", [])
        self.signals = [dict(s, regex=re.compile(s["pattern"], re.I)) for s in rules.get("code_signals", [])]
        self.env_names = sorted({name for fw in self.frameworks for name in fw.get("env", {})})
        self.assignments = _EnvAssignments(self.env_names) if self.env_names else None
        self.deps = {}  # (ecosystem, name) -> [locations]
        self.files = []  # relative paths, for file triggers
        self.env_seen = {}  # name -> [{"location", "source", "value"}]
        self.signal_hits = {}  # signal id -> [locations]
        self._stages = {}

    def add_manifest(self, rel_path, deps):
        ecosystem = manifest_ecosystem(rel_path.rsplit("/", 1)[-1])
        if not ecosystem:
            return
        for dep in deps:
            key = (ecosystem, dep.lower() if ecosystem == "pypi" else dep)
            self.deps.setdefault(key, []).append(rel_path)

    def add_file(self, rel_path):
        self.files.append(rel_path)
        name = rel_path.rsplit("/", 1)[-1]
        self._current = rel_path
        self._kind = source_kind(rel_path)
        self._is_docker = _file_matches(name, ["dockerfile*", "containerfile*", "*.dockerfile"])
        self._active = [s for s in self.signals if not s.get("files") or _file_matches(name, s["files"])]

    def add_line(self, rel_path, n, line, is_comment):
        if rel_path != getattr(self, "_current", None):
            self.add_file(rel_path)
        location = f"{rel_path}:{n}"
        if is_comment:
            return
        if self.assignments:
            for name, value in self.assignments.find(line, self._kind):
                self.env_seen.setdefault(name, []).append(
                    {"location": location, "source": self._kind, "value": value}
                )
        for signal in self._active:
            if not signal["regex"].search(line):
                continue
            if signal["id"] == "docker_from_image" and not self._external_base(rel_path, line):
                continue
            self.signal_hits.setdefault(signal["id"], []).append(location)
        stage = STAGE.match(line) if self._is_docker else None
        if stage and stage[1]:
            self._stages.setdefault(rel_path, set()).add(stage[1].lower())

    def _external_base(self, rel_path, line):
        parts = line.split()
        tokens = [p for p in parts[1:] if not p.startswith("--")]
        if not tokens:
            return False
        image = tokens[0]
        if image.lower() in self._stages.get(rel_path, set()):
            return False  # multi-stage reference to an earlier build stage
        return _public_image(image)

    def _framework_triggers(self, fw):
        found = []
        if fw.get("ecosystem") == "file":
            for rel in self.files:
                if _file_matches(rel.rsplit("/", 1)[-1], fw.get("files", [])):
                    found.append((rel.rsplit("/", 1)[-1], rel))
            return found
        for (ecosystem, dep), locations in self.deps.items():
            if ecosystem != fw.get("ecosystem"):
                continue
            exact = dep in fw.get("packages", [])
            prefixed = any(dep.startswith(p) for p in fw.get("package_prefixes", []))
            if exact or prefixed:
                found.extend((dep, loc) for loc in locations)
        return found

    def _env_status(self, name, recommended, accept):
        seen = self.env_seen.get(name, [])
        evidence = [
            dict(item, matches=value_matches(recommended, item["value"], accept)) for item in seen
        ]
        repo = [e for e in evidence if e["source"] != "example"]
        literal = [e for e in repo if e["value"] is not None]
        if not seen:
            status = "missing"
        elif not repo:
            status = "example_only"
        elif not literal:
            status = "unresolved"
        elif all(e["matches"] for e in literal):
            status = "configured"
        elif any(e["matches"] for e in literal):
            status = "conflict"
        else:
            status = "mismatch"
        return {
            "name": name,
            "recommended": recommended,
            "status": status,
            # Repository evidence only; whether the deployed process receives it is not verified.
            "deployment_effect": "unverified",
            "found_in": [e["location"] for e in evidence][:MAX_LOCATIONS],
            "evidence": evidence[:MAX_LOCATIONS],
        }

    def results(self):
        hints = []
        for fw in self.frameworks:
            triggers = self._framework_triggers(fw)
            if not triggers:
                continue
            accept = fw.get("env_accept", {})
            env = [self._env_status(name, value, accept.get(name)) for name, value in fw.get("env", {}).items()]
            hints.append(
                {
                    "id": fw["id"],
                    "type": "switch",
                    "kind": fw["kind"],
                    "name": fw["name"],
                    "evidence": sorted({f"{dep} ({loc})" for dep, loc in triggers})[:MAX_LOCATIONS],
                    "packages": sorted({dep for dep, _ in triggers}),
                    "manifests": sorted({loc for _, loc in triggers}),
                    "env": env,
                    "advice": fw.get("advice", ""),
                    "reference": fw.get("reference"),
                }
            )
        for signal in self.signals:
            locations = self.signal_hits.get(signal["id"])
            if not locations:
                continue
            hints.append(
                {
                    "id": signal["id"],
                    "type": "signal",
                    "kind": signal["kind"],
                    "name": signal["name"],
                    "destination": signal.get("destination"),
                    "occurrences": len(locations),
                    "evidence": locations[:MAX_LOCATIONS],
                    "env": [],
                    "advice": signal.get("advice", ""),
                    "reference": signal.get("reference"),
                }
            )
        order = {"telemetry": 0, "offline_mode": 1, "runtime_download": 2, "build_download": 3}
        hints.sort(key=lambda h: (order.get(h["kind"], 9), h["type"], h["id"]))
        return hints


STATUS_LABELS = {
    "configured": "configured in repo",
    "conflict": "conflicting values",
    "mismatch": "value differs",
    "unresolved": "set, value not literal",
    "example_only": "example file only",
    "missing": "missing",
}


def _describe(item):
    shown = []
    for e in item.get("evidence", [])[:3]:
        value = "<computed>" if e["value"] is None else repr(e["value"])
        tag = " (example, not configuration)" if e["source"] == "example" else ""
        shown.append(f"{e['location']} sets {value}{tag}")
    return "; ".join(shown)


def env_file(hints):
    """Suggested environment overrides; never written into the target source.

    Every variable that is not already configured with the recommended value in the repository is
    emitted as an active line. Configured ones are listed as comments with their location, because
    the repository cannot show whether the deployment actually loads that file.
    """
    lines = [
        "# Generated by repo-airgap: official opt-out / offline switches for detected dependencies.",
        "# Review each value against the deployed version before use. This file is a suggestion,",
        "# not proof of offline readiness. Values found in the repository are noted per variable;",
        "# example/template files are not treated as configuration, and no value is proven to reach",
        "# the deployed process.",
        "",
    ]
    emitted = set()
    for hint in hints:
        if hint["type"] != "switch":
            continue
        items = [item for item in hint["env"] if item["name"] not in emitted]
        if not items:
            continue
        lines.append(f"# {hint['name']} — {hint['kind']} — {hint.get('reference') or ''}".rstrip(" —"))
        for item in items:
            emitted.add(item["name"])
            status = item["status"]
            detail = _describe(item)
            if status == "configured":
                lines.append(f"# configured in repository ({detail}); confirm the deployment loads it:")
                lines.append(f"# {item['name']}={item['recommended']}")
                continue
            if status != "missing":
                lines.append(f"# {STATUS_LABELS[status]}: {detail}")
            lines.append(f"{item['name']}={item['recommended']}")
        lines.append("")
    return "\n".join(lines)
