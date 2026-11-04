import json
import re
import tomllib


def _normalize(name):
    return name.lower().replace("_", "-").replace(".", "-")


def requirement_name(line):
    """Package name from a requirements line, including `name @ url`, wheel URLs and #egg= links."""
    s = line.split(" #", 1)[0].strip()
    if not s or s.startswith(("#", "-")):
        return None
    if " @ " in s:
        return _normalize(re.split(r"[\s\[]", s.split(" @ ", 1)[0].strip())[0])
    if "://" in s or s.startswith(("git+", "file:")):
        egg = re.search(r"[#&]egg=([A-Za-z0-9_.-]+)", s)
        if egg:
            return _normalize(egg[1])
        url = s.split(";", 1)[0].strip()
        filename = url.rsplit("/", 1)[-1].split("?", 1)[0]
        wheel = re.match(r"([A-Za-z0-9_.]+?)-\d", filename)
        if filename.endswith((".whl", ".tar.gz", ".zip")) and wheel:
            return _normalize(wheel[1])
        return None
    return _normalize(re.split(r"[\s\[<>=!~;]", s)[0])


def dependency_names(path, text, warnings):
    name = path.name.lower()
    try:
        if name == "package.json":
            data = json.loads(text)
            return set(data.get("dependencies", {})) | set(data.get("devDependencies", {}))
        if name == "pyproject.toml":
            data = tomllib.loads(text)
            names = list(data.get("project", {}).get("dependencies", []))
            for group in data.get("project", {}).get("optional-dependencies", {}).values():
                names.extend(group)
            names.extend(data.get("tool", {}).get("poetry", {}).get("dependencies", {}))
            return {re.split(r"[\s\[<>=!~;]", n)[0].lower().replace("_", "-") for n in names}
        if name.startswith("requirements") and name.endswith(".txt"):
            return {
                n
                for n in (requirement_name(s) for s in text.splitlines())
                if n
            }
        if name == "go.mod":
            return set(re.findall(r"(?m)^\s*(?:require\s+)?([\w.-]+/[\w./-]+)\s+v[\w.+-]+", text))
    except (ValueError, TypeError, AttributeError) as e:
        warnings.append(f"Cannot parse manifest {path.name}: {type(e).__name__}")
    return set()


def imported_packages(line):
    packages = re.findall(r"(?:from\s*|require\s*\(\s*|import\s*)[\"']([^\"']+)[\"']", line)
    if not packages:
        packages.extend(re.findall(r"^\s*(?:from|import)\s+([\w.]+)", line))
    return packages
