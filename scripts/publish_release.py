"""Explicitly invoked local build and GitHub publication; never auto-commit files."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*args, capture=False):
    result = subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True,
                            text=True, stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ""


def github_cli():
    candidates = [shutil.which("gh"),
                  Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "GitHub CLI/gh.exe",
                  Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/GitHub CLI/gh.exe"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise RuntimeError("Run 'Uploader: Install GitHub CLI', then 'Uploader: Sign in to GitHub'.")


def repository(remote):
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?", remote)
    if not match:
        raise RuntimeError("origin must be a github.com HTTPS or SSH repository URL.")
    return match.group(1)


def source_state():
    if run("git", "status", "--porcelain", "--untracked-files=all", capture=True):
        raise RuntimeError("Commit your changes in VS Code Source Control before publishing. No files were committed automatically.")
    branch = run("git", "symbolic-ref", "--quiet", "--short", "HEAD", capture=True)
    commit = run("git", "rev-parse", "HEAD", capture=True)
    return branch, commit


def release_files(version):
    folder = ROOT / "releases" / version
    names = [f"vMixYouTubeUploader-{version}-windows-x64-setup.exe",
             f"vMixYouTubeUploader-{version}-windows-x64-portable.zip"]
    expected = {}
    for name in names:
        with (folder / name).open("rb") as stream:
            expected[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    recorded = {}
    for line in (folder / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines():
        digest, name = line.split("  ", 1)
        recorded[name] = digest
    if recorded != expected:
        raise RuntimeError("Release checksums do not match the installer and ZIP; publication stopped.")
    notes = folder / "RELEASE-NOTES.md"
    if not notes.read_text(encoding="utf-8").strip():
        raise RuntimeError("Release notes are empty.")
    return [folder / name for name in names] + [folder / "SHA256SUMS.txt"], notes


def publish():
    gh = github_cli()
    branch, commit = source_state()
    remote = run("git", "remote", "get-url", "origin", capture=True)
    # Use the same explicit destination for source and release, ignoring pushurl overrides.
    repo = repository(remote)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError("Set a three-part numeric version in pyproject.toml.")
    tag = f"v{version}"
    run(gh, "auth", "status", "--hostname", "github.com")
    run(gh, "repo", "view", repo, "--json", "nameWithOwner", capture=True)
    if run("git", "ls-remote", "--tags", remote, f"refs/tags/{tag}", capture=True):
        raise RuntimeError(f"Tag {tag} already exists. Bump the version for a new release.")
    # Include drafts, including a draft left by a previously interrupted upload.
    releases = run(gh, "api", "--paginate", f"repos/{repo}/releases", "--jq", ".[].tag_name", capture=True)
    if tag in releases.splitlines():
        raise RuntimeError(f"Release {tag} already exists (possibly a draft). Finish it on GitHub or use a new version.")
    print(f"Building {tag} from {commit} for https://github.com/{repo}", flush=True)
    run(sys.executable, ROOT / "scripts/build_release.py")
    if source_state() != (branch, commit) or run("git", "remote", "get-url", "origin", capture=True) != remote:
        raise RuntimeError("Source or origin changed during the build. Publication stopped; rebuild from a clean commit.")
    assets, notes = release_files(version)
    run("git", "push", remote, f"{commit}:refs/heads/{branch}")
    try:
        run(gh, "release", "create", tag, *assets, "--repo", repo,
            "--target", commit, "--title", f"vMix YouTube Uploader {version}",
            "--notes-file", notes, "--draft")
        # Do not publish a draft with missing or incomplete assets.
        uploaded = json.loads(run(gh, "release", "view", tag, "--repo", repo,
                                  "--json", "assets", capture=True))["assets"]
        if {item["name"]: item["size"] for item in uploaded} != {p.name: p.stat().st_size for p in assets}:
            raise RuntimeError("Uploaded assets do not match the local release.")
        run(gh, "release", "edit", tag, "--repo", repo, "--draft=false")
    except (subprocess.CalledProcessError, RuntimeError):
        print(f"Publication did not complete. Inspect https://github.com/{repo}/releases before retrying. "
              "A draft may contain partially uploaded files; existing releases are never overwritten.", file=sys.stderr)
        raise
    print(f"Published: https://github.com/{repo}/releases/tag/{tag}", flush=True)


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--login"]:
            run(github_cli(), "auth", "login", "--hostname", "github.com", "--web", "--git-protocol", "https")
            run(github_cli(), "auth", "setup-git", "--hostname", "github.com")
        elif sys.argv[1:]:
            raise RuntimeError("Usage: publish_release.py [--login]")
        else:
            publish()
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError) as exc:
        print(f"Release task stopped: {exc}", file=sys.stderr)
        sys.exit(1)
