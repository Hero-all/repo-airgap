from pathlib import Path
from urllib.parse import unquote, urlparse

from ..io import load_json
from ..safety import safe_path


def semgrep_findings(path, root):
    """Read local Semgrep JSON or SARIF. Never execute tools or load remote rules."""
    data = load_json(path)
    if "runs" in data:
        for run in data["runs"]:
            for item in run.get("results", []):
                for location in item.get("locations", []):
                    physical = location["physicalLocation"]
                    yield _finding(
                        root,
                        unquote(physical["artifactLocation"]["uri"]),
                        physical.get("region", {}).get("startLine", 1),
                        item.get("ruleId", "unknown"),
                    )
    else:
        for item in data.get("results", []):
            yield _finding(root, item["path"], item["start"]["line"], item.get("check_id", "unknown"))


def _finding(root, filename, line, rule):
    if filename.startswith("file:"):
        filename = unquote(urlparse(filename).path)
        if len(filename) > 2 and filename[0] == "/" and filename[2] == ":":
            filename = filename[1:]
    path = Path(filename)
    if path.is_absolute():
        filename = path.resolve().relative_to(root).as_posix()
    path = safe_path(root, filename)
    if not path.is_file() or int(line) < 1:
        raise ValueError("Imported finding references invalid source location")
    return path, int(line), str(rule)
