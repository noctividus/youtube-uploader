"""Release safety checks; no GitHub calls or actual builds."""
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("publish_release", Path(__file__).parents[1] / "scripts/publish_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release, "github_cli", lambda: "gh")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    folder = tmp_path / "releases/0.1.0"
    folder.mkdir(parents=True)
    sums = []
    assets = []
    for suffix in ("setup.exe", "portable.zip"):
        path = folder / f"vMixYouTubeUploader-0.1.0-windows-x64-{suffix}"
        path.write_bytes(b"test asset")
        sums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
        assets.append(path)
    (folder / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n")
    (folder / "RELEASE-NOTES.md").write_text("Test release")
    assets.append(folder / "SHA256SUMS.txt")
    calls = []
    state = {"dirty": False, "existing": False, "incomplete": False, "failure": None, "changed": False}

    def run(*args, **kwargs):
        args = tuple(map(str, args))
        calls.append(args)
        if args[:2] == ("git", "status"):
            return " M README.md" if state["dirty"] else ""
        if args[:2] == ("git", "symbolic-ref"):
            return "main"
        if args[:2] == ("git", "rev-parse"):
            return "b" * 40 if state["changed"] else "a" * 40
        if args[:2] == ("git", "remote"):
            return "https://github.com/example/uploader.git"
        if args[:2] == ("gh", "api"):
            return "v0.1.0" if state["existing"] else ""
        stage = "build" if args[-1].endswith("build_release.py") else "push" if args[:2] == ("git", "push") else "upload" if args[:3] == ("gh", "release", "create") else None
        if stage and state["failure"] == stage:
            raise subprocess.CalledProcessError(1, args)
        if stage == "build" and state.get("change_during_build"):
            state["changed"] = True
        if args[:3] == ("gh", "release", "view"):
            return json.dumps({"assets": [] if state["incomplete"] else [{"name": p.name, "size": p.stat().st_size} for p in assets]})
        return ""

    monkeypatch.setattr(release, "run", run)
    return state, calls, assets


@pytest.mark.parametrize("remote", ["https://github.com/example/uploader.git", "git@github.com:example/uploader.git", "ssh://git@github.com/example/uploader.git"])
def test_repository(remote):
    assert release.repository(remote) == "example/uploader"


def test_success_publishes_built_commit(pipeline):
    _, calls, _ = pipeline
    release.publish()
    push = next(c for c in calls if c[:2] == ("git", "push"))
    assert push[-1] == f"{'a' * 40}:refs/heads/main"
    create = next(c for c in calls if c[:3] == ("gh", "release", "create"))
    assert create[create.index("--target") + 1] == "a" * 40
    assert "--draft" in create
    assert calls[-1][-1] == "--draft=false"


@pytest.mark.parametrize("condition", ["dirty", "existing", "change_during_build"])
def test_unsafe_source_never_pushes(pipeline, condition):
    state, calls, _ = pipeline
    state[condition] = True
    with pytest.raises(RuntimeError):
        release.publish()
    assert not any(c[:2] == ("git", "push") for c in calls)


@pytest.mark.parametrize("stage", ["build", "push", "upload"])
def test_failure_never_publishes(pipeline, stage):
    state, calls, _ = pipeline
    state["failure"] = stage
    with pytest.raises(subprocess.CalledProcessError):
        release.publish()
    assert not any(c[:3] == ("gh", "release", "edit") for c in calls)


def test_incomplete_upload_stays_draft(pipeline):
    state, calls, _ = pipeline
    state["incomplete"] = True
    with pytest.raises(RuntimeError, match="Uploaded assets"):
        release.publish()
    assert not any(c[:3] == ("gh", "release", "edit") for c in calls)


def test_corrupt_asset_never_pushes(pipeline):
    _, calls, assets = pipeline
    assets[0].write_bytes(b"corrupted")
    with pytest.raises(RuntimeError, match="checksums"):
        release.publish()
    assert not any(c[:2] == ("git", "push") for c in calls)
