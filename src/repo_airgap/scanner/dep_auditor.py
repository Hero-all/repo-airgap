import json
import re
import tomllib


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
                re.split(r"[\s\[<>=!~;]", s.strip())[0].lower().replace("_", "-")
                for s in text.splitlines()
                if s.strip() and not s.lstrip().startswith(("#", "-"))
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
