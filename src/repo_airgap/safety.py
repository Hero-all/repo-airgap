from pathlib import Path, PurePosixPath

from .scanner.file_walker import is_link, protected_name


def repository(path):
    root = Path(path).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Target must be a directory")
    return root


def safe_path(root, relative, *, protected=True):
    if not isinstance(relative, str) or "\\" in relative or ":" in relative:
        raise ValueError("Use a repository-relative POSIX path")
    rel = PurePosixPath(relative)
    if rel.is_absolute() or not rel.parts or any(p in (".", "..") for p in rel.parts):
        raise ValueError("Path escapes repository")
    if any(p.lower() in (".git", ".airgap") for p in rel.parts):
        raise ValueError("Cannot edit metadata directories")
    target = root
    for part in rel.parts:
        target = target / part
        if is_link(target):
            raise ValueError(f"Refusing linked path: {relative}")
    if not target.resolve().is_relative_to(root):
        raise ValueError("Path escapes repository")
    if protected and protected_name(target):
        raise ValueError(f"Protected legal file: {relative}")
    return target


def state_dir(root, create=False):
    state = root / ".airgap"
    if is_link(state):
        raise ValueError("Refusing linked .airgap directory")
    if create:
        state.mkdir(exist_ok=True)
    if state.exists():
        for item in state.iterdir():
            if is_link(item):
                raise ValueError(f"Refusing linked state artifact: {item.name}")
    return state
