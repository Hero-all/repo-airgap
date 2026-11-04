import os
import re
from pathlib import Path

import pathspec

EXCLUDED_DIRS = {
    ".git",
    ".airgap",
    "node_modules",
    "dist",
    "build",
    "out",
    ".next",
    ".nuxt",
    "vendor",
    "__pycache__",
    ".venv",
    "venv",
    "coverage",
    ".pytest_cache",
    ".ruff_cache",
}
EXCLUDED_SUFFIXES = {
    ".min.js",
    ".min.css",
    ".map",
    ".lock",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".mp3",
    ".mp4",
    ".wav",
    ".webm",
    ".pdf",
    ".zip",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".pyc",
    ".bin",
    ".patch",
}
EXCLUDED_NAMES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "go.sum",
    "audit_candidates.json",
    "outbound_inventory.json",
    "airgap_report.md",
}


def protected_name(path):
    name = Path(path).name.lower()
    return any(
        name == n or name.startswith(n + ".") or name.startswith(n + "-")
        for n in ("license", "licence", "notice", "copying", "copyright")
    )


TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs|e2e|fixtures?|__mocks__|mocks?|testdata|test-data)(/|$)"
    r"|\.(test|spec|stories)\.[cm]?[jt]sx?$|(^|/)test_[^/]+\.py$|(^|/)[^/]+_test\.(py|go)$|(^|/)conftest\.py$",
    re.I,
)


def is_test_path(relative_posix):
    return bool(TEST_PATH.search(relative_posix))


LOCALE_DATA = re.compile(r"(^|/)(i18n|locales?|lang|langs|translations?)/.*\.(json|ya?ml|po|properties)$", re.I)


def is_locale_data(relative_posix):
    """Translation catalogs: URLs there are display links, not network calls."""
    return bool(LOCALE_DATA.search(relative_posix))


def looks_minified(text, lines):
    """Vendored/minified bundles: a few enormous lines. Their URLs are mostly library internals."""
    if len(text) < 20_000:
        return False
    longest = max((len(line) for line in lines), default=0)
    return longest > 5_000 and len(text) / max(1, len(lines)) > 300


def is_link(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def walk_files(root: Path, warnings: list[str]):
    """Honor nested gitignore files and never follow symlinks/junctions."""

    def visit(folder, inherited):
        ignore = folder / ".gitignore"
        specs = list(inherited)
        if ignore.is_file() and not is_link(ignore):
            try:
                specs.append(
                    (folder, pathspec.GitIgnoreSpec.from_lines(ignore.read_text("utf-8").splitlines()))
                )
            except (OSError, UnicodeError, ValueError) as e:
                warnings.append(f"Cannot parse {ignore.relative_to(root)}: {type(e).__name__}")
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError as e:
            warnings.append(f"Cannot list {folder.relative_to(root)}: {type(e).__name__}")
            return
        for entry in entries:
            path = Path(entry.path)
            if is_link(path):
                warnings.append(f"Skipped link: {path.relative_to(root).as_posix()}")
                continue
            is_dir = entry.is_dir(follow_symlinks=False)
            if (is_dir and entry.name in EXCLUDED_DIRS) or protected_name(path):
                continue
            ignored = False
            for base, spec in specs:
                match = spec.check_file(path.relative_to(base).as_posix() + ("/" if is_dir else ""))
                if match.include is not None:
                    ignored = match.include
            if ignored:
                continue
            if is_dir:
                yield from visit(path, specs)
            elif entry.is_file(follow_symlinks=False):
                name = entry.name.lower()
                if name not in EXCLUDED_NAMES and not any(name.endswith(s) for s in EXCLUDED_SUFFIXES):
                    yield path

    yield from visit(root, [])


def read_source(path, max_bytes=2 * 1024 * 1024):
    if path.stat().st_size > max_bytes:
        return None
    data = path.read_bytes()
    if b"\0" in data:
        return None
    try:
        return data, data.decode("utf-8-sig")
    except UnicodeError:
        return None
