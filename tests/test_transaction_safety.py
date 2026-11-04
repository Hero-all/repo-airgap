"""Transaction safety: interruption, permissions, locks, links and new-file modes."""

import base64
import json
import os
import stat
import sys

import pytest

from repo_airgap.io import sha256
from repo_airgap.patcher import patch_manager
from repo_airgap.patcher.patch_manager import apply_plan, revert, save_plan
from repo_airgap.safety import safe_path


def at_before(target, plan):
    return [sha256((target / f["file_path"]).read_bytes()) == f["before_sha256"] for f in plan["files"]]


def source_writes(monkeypatch, on_call):
    actual = patch_manager.atomic_write
    calls = {"n": 0}

    def wrapper(path, data, **kwargs):
        if ".airgap" not in path.parts:
            calls["n"] += 1
            on_call(calls["n"])
        actual(path, data, **kwargs)

    monkeypatch.setattr(patch_manager, "atomic_write", wrapper)


def test_ctrl_c_during_apply_rolls_back_written_files(target, planned, monkeypatch):
    def interrupt(n):
        if n == 2:
            raise KeyboardInterrupt

    source_writes(monkeypatch, interrupt)
    with pytest.raises(KeyboardInterrupt):
        apply_plan(target, save_plan(target, planned))
    assert all(at_before(target, planned))
    manifest = json.loads((target / ".airgap/manifest.json").read_text("utf-8"))
    assert manifest["status"] == "apply_failed_rolled_back"
    assert not (target / ".airgap/mutation.lock").exists()


def test_concurrent_edit_during_apply_keeps_user_bytes_and_undoes_ours(target, planned, monkeypatch):
    rel = planned["files"][1]["file_path"]
    second = target / rel
    actual = patch_manager._matches
    calls = {"n": 0}

    def matches(root, file, kind):
        if kind == "before" and file["file_path"] == rel:
            calls["n"] += 1
            if calls["n"] == 2:  # an editor saves between the pre-check and our write
                second.write_text("# user saved this mid-transaction\n", encoding="utf-8")
        return actual(root, file, kind)

    monkeypatch.setattr(patch_manager, "_matches", matches)
    with pytest.raises(ValueError, match="changed by another process"):
        apply_plan(target, save_plan(target, planned))
    assert at_before(target, planned)[0]  # our write to the first file was undone
    assert second.read_text("utf-8") == "# user saved this mid-transaction\n"


def test_crash_without_cleanup_is_resumable_by_revert(target, planned):
    # A killed process cannot run except/finally blocks: simulate the state it leaves behind.
    path = save_plan(target, planned)
    first = planned["files"][0]
    (target / first["file_path"]).write_bytes(base64.b64decode(first["after_b64"]))
    manifest = dict(planned, status="applying")
    (target / ".airgap/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="active transaction"):
        apply_plan(target, path)
    revert(target)
    assert all(at_before(target, planned))


def test_read_only_file_is_refused_before_any_write(target, planned):
    locked = target / planned["files"][1]["file_path"]
    os.chmod(locked, stat.S_IREAD)
    try:
        with pytest.raises(ValueError, match="not writable"):
            apply_plan(target, save_plan(target, planned))
        assert all(at_before(target, planned))
        assert not list(target.rglob(".airgap-write-*"))  # no temp files left in the source tree
    finally:
        os.chmod(locked, stat.S_IREAD | stat.S_IWRITE)


def test_failed_replace_leaves_no_temp_file(tmp_path, monkeypatch):
    from repo_airgap import io

    target = tmp_path / "f.txt"
    target.write_text("x")

    def fail(*_):
        raise PermissionError("simulated")

    monkeypatch.setattr(io.os, "replace", fail)
    os.chmod(target, stat.S_IREAD)
    try:
        with pytest.raises(PermissionError):
            io.atomic_write(target, b"y")
    finally:
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]


def test_stale_lock_reports_owner_and_is_not_auto_removed(target, planned):
    state = target / ".airgap"
    state.mkdir(exist_ok=True)
    from repo_airgap.patcher.patch_manifest import mutation_lock

    with mutation_lock(target):
        info = json.loads((state / "mutation.lock").read_text("utf-8"))
        assert info["pid"] == os.getpid() and info["host"] and info["started_at"]
        with pytest.raises(ValueError, match=f"pid {os.getpid()}"):
            apply_plan(target, save_plan(target, planned))
    assert all(at_before(target, planned))
    (state / "mutation.lock").write_text("legacy lock\n")
    with pytest.raises(ValueError, match="mutation.lock"):
        apply_plan(target, save_plan(target, planned))
    assert (state / "mutation.lock").exists()


def test_permission_bits_preserved(target, planned):
    if sys.platform == "win32":
        pytest.skip("POSIX permission bits")
    script = target / planned["files"][0]["file_path"]
    os.chmod(script, 0o750)
    apply_plan(target, save_plan(target, planned))
    assert stat.S_IMODE(script.stat().st_mode) == 0o750
    revert(target)
    assert stat.S_IMODE(script.stat().st_mode) == 0o750


def test_new_file_mode_follows_umask(tmp_path):
    if sys.platform == "win32":
        pytest.skip("POSIX permission bits")
    from repo_airgap.io import atomic_write

    old = os.umask(0o022)
    try:
        atomic_write(tmp_path / "new.py", b"x", new_file_mode=patch_manager.default_file_mode())
    finally:
        os.umask(old)
    assert stat.S_IMODE((tmp_path / "new.py").stat().st_mode) == 0o644


def _link_dir(link, destination):
    try:
        os.symlink(destination, link, target_is_directory=True)
        return
    except OSError:
        pass
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(destination), str(link))
        return
    pytest.skip("cannot create directory links")


def test_linked_parent_directory_refused(target, planned, tmp_path):
    rel = next(f["file_path"] for f in planned["files"] if "/" in f["file_path"])
    parent = (target / rel).parent
    moved = tmp_path / "moved"
    parent.rename(moved)
    _link_dir(parent, moved)
    with pytest.raises(ValueError, match="linked path"):
        safe_path(target, rel)
    with pytest.raises(ValueError, match="linked path"):
        apply_plan(target, save_plan(target, planned))
    assert sha256((moved / rel.rsplit("/", 1)[1]).read_bytes()) == next(
        f["before_sha256"] for f in planned["files"] if f["file_path"] == rel
    )
