"""Local-only release pipeline. Never uploads, tags, or publishes anything."""
import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
import tomllib
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*args, **kwargs):
    print("Running:", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=ROOT, check=True, **kwargs)


def compiler(explicit):
    candidates = [explicit, os.environ.get("ISCC_PATH"), shutil.which("ISCC"),
                  ROOT / ".tools/inno/ISCC.exe",
                  Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Inno Setup 6/ISCC.exe",
                  Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6/ISCC.exe",
                  Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 7/ISCC.exe"]
    for path in candidates:
        if path and Path(path).is_file():
            return Path(path).resolve()
    raise RuntimeError("Install Inno Setup 6/7, or set ISCC_PATH to ISCC.exe. See README's local release build instructions.")


def notices(bundle):
    target = bundle / "third-party-licenses"
    target.mkdir(exist_ok=True)
    shutil.copytree(ROOT / "packaging/licenses", target / "Qt-PySide", dirs_exist_ok=True)
    packages = []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        if name == "vmix-youtube-uploader":
            continue
        packages.append(f"{name}=={dist.version}")
        metadata_dir = target / name
        metadata_dir.mkdir(exist_ok=True)
        (metadata_dir / "PACKAGE-METADATA.txt").write_text(
            dist.read_text("METADATA") or dist.read_text("PKG-INFO") or f"{name}=={dist.version}\n", encoding="utf-8")
        for entry in dist.files or []:
            if ".." in entry.parts:
                continue
            if any(part.lower().startswith(("license", "copying", "notice")) for part in entry.parts):
                source = Path(dist.locate_file(entry))
                if source.is_file():
                    relative = Path(name) / Path(*entry.parts)
                    destination = target / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
    # CPython's distribution license is outside site-packages.
    license_path = Path(sys.base_prefix) / "LICENSE.txt"
    if license_path.is_file():
        shutil.copy2(license_path, target / "Python-LICENSE.txt")
    (bundle / "THIRD-PARTY-NOTICES.txt").write_text(
        "Bundled software and build environment dependency versions\n"
        "License texts are included in third-party-licenses.\n"
        "Qt/PySide6 are dynamically linked; users may replace compatible libraries in _internal.\n"
        "Qt source: https://download.qt.io/official_releases/qt/\n"
        "PySide source: https://code.qt.io/cgit/pyside/pyside-setup.git/\n\n" + "\n".join(packages), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iscc", default="")
    args = parser.parse_args()
    if os.name != "nt" or struct.calcsize("P") != 8 or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("Build on Windows with x64 Python 3.12 or newer.")
    iscc = compiler(args.iscc)
    for line in (ROOT / "requirements-build.lock").read_text().splitlines():
        if line and not line.startswith("#"):
            name, version = line.split("==")
            if metadata.version(name) != version:
                raise RuntimeError(f"Build dependency mismatch: {name}. Install requirements-build.lock first.")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdecimal() and int(p) <= 65535 for p in parts):
        raise RuntimeError("Release version must be three numeric components in pyproject.toml.")
    run(sys.executable, "-m", "pip", "check")
    test_root = ROOT / "build" / "tests" / str(uuid.uuid4())
    test_root.parent.mkdir(parents=True, exist_ok=True)
    run(sys.executable, "-m", "pytest", "-q", f"--basetemp={test_root}")
    run(sys.executable, "scripts/build_icons.py")
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    numeric = tuple(map(int, parts)) + (0,)
    (build / "version-info.txt").write_text(
        f"VSVersionInfo(ffi=FixedFileInfo(filevers={numeric!r}, prodvers={numeric!r}, mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0,0)), "
        f"kids=[StringFileInfo([StringTable('040904B0', [StringStruct('FileDescription', 'vMix YouTube Uploader'), "
        f"StringStruct('ProductName', 'vMix YouTube Uploader'), StringStruct('FileVersion', '{version}'), "
        f"StringStruct('ProductVersion', '{version}'), StringStruct('OriginalFilename', 'vMixYouTubeUploader.exe')])]), "
        "VarFileInfo([VarStruct('Translation', [1033, 1200])])])", encoding="utf-8")
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "packaging/uploader.spec")
    bundle = ROOT / "dist/vMixYouTubeUploader"
    notices(bundle)
    shutil.copy2(ROOT / "README.md", bundle / "README.md")
    shutil.copy2(ROOT / "requirements-build.lock", bundle / "BUILD-DEPENDENCIES.txt")
    check = build / "smoke" / str(uuid.uuid4())
    environment = dict(os.environ, QT_QPA_PLATFORM="windows")
    run(bundle / "vMixYouTubeUploader.exe", "--self-test", check, timeout=60, env=environment)
    if not json.loads((check / "self-test.json").read_text())["ok"]:
        raise RuntimeError(f"Frozen self-test failed; see {check}")
    release = ROOT / "releases" / version
    release.mkdir(parents=True, exist_ok=True)
    run(iscc, f"/DAppVersion={version}", f"/DSourceDir={bundle}", f"/DReleaseDir={release}", "packaging/installer.iss")
    archive = release / f"vMixYouTubeUploader-{version}-windows-x64-portable"
    shutil.make_archive(str(archive), "zip", bundle.parent, bundle.name)
    artifacts = [release / f"vMixYouTubeUploader-{version}-windows-x64-setup.exe", Path(str(archive) + ".zip")]
    checksums = []
    for path in artifacts:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        checksums.append(f"{digest}  {path.name}")
    (release / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="ascii")
    (release / "RELEASE-NOTES.md").write_text(
        f"# vMix YouTube Uploader {version}\n\n"
        "Windows x64 installer and portable ZIP; Python is included.\n\n"
        "Install using the setup EXE, or extract the entire portable ZIP and launch vMixYouTubeUploader.exe.\n"
        "Connect your YouTube channel using your own Desktop OAuth JSON in Settings.\n"
        "This release is unsigned. Windows may display an unknown-publisher warning.\n"
        "Runtime settings/history are retained during upgrades and uninstall.\n\n"
        "Validated: automated suite and offline frozen application self-check.\n"
        "Before publishing, verify on a clean Windows machine and test a private YouTube upload.\n",
        encoding="utf-8")
    print(f"\nRelease ready: {release}\nUpload the installer, ZIP, and SHA256SUMS.txt to a GitHub Release.", flush=True)


if __name__ == "__main__":
    main()
