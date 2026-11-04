"""Offline-readiness hints: official opt-out switches and build/runtime download signals.

Hints never create edit candidates. They point at configuration a deployer can set
without patching source, and at downloads that must be staged before going offline.
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


class OfflineHintCollector:
    def __init__(self, rules):
        self.frameworks = rules.get("frameworks", [])
        self.signals = [dict(s, regex=re.compile(s["pattern"], re.I)) for s in rules.get("code_signals", [])]
        self.env_names = sorted({name for fw in self.frameworks for name in fw.get("env", {})})
        self.env_regex = re.compile(
            r"(?<![\w-])(" + "|".join(map(re.escape, self.env_names)) + r")(?![\w-])[\"']?\s*\]?\s*[=:,]"
        ) if self.env_names else None
        self.deps = {}  # (ecosystem, name) -> [locations]
        self.files = []  # relative paths, for file triggers
        self.env_seen = {}  # name -> [locations]
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
        self._is_docker = _file_matches(name, ["dockerfile*", "containerfile*", "*.dockerfile"])
        self._active = [s for s in self.signals if not s.get("files") or _file_matches(name, s["files"])]

    def add_line(self, rel_path, n, line, is_comment):
        if rel_path != getattr(self, "_current", None):
            self.add_file(rel_path)
        location = f"{rel_path}:{n}"
        if self.env_regex and not is_comment:
            for match in self.env_regex.finditer(line):
                self.env_seen.setdefault(match[1], []).append(location)
        if is_comment:
            return
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

    def results(self):
        hints = []
        for fw in self.frameworks:
            triggers = self._framework_triggers(fw)
            if not triggers:
                continue
            env = []
            for name, value in fw.get("env", {}).items():
                seen = self.env_seen.get(name, [])
                env.append(
                    {
                        "name": name,
                        "recommended": value,
                        "status": "present" if seen else "missing",
                        "found_in": seen[:MAX_LOCATIONS],
                    }
                )
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


def env_file(hints):
    """Suggested environment overrides for missing switches; never written into the target source."""
    lines = [
        "# Generated by repo-airgap: official opt-out / offline switches for detected dependencies.",
        "# Review each value against the deployed version before use. Variables already set in the",
        "# repository are listed as comments. This file is a suggestion, not proof of offline readiness.",
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
            prefix = "# already set: " if item["status"] == "present" else ""
            lines.append(f"{prefix}{item['name']}={item['recommended']}")
        lines.append("")
    return "\n".join(lines)
