from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH).parent
datas = [(str(root / "vmix_youtube_uploader" / "assets" / "app.ico"), "vmix_youtube_uploader/assets")]
datas += collect_data_files("googleapiclient", includes=["discovery_cache/documents/youtube.v3.json"])
for package in ("google-api-python-client", "google-auth-httplib2", "httplib2", "google-auth", "keyring"):
    datas += copy_metadata(package)

a = Analysis(
    [str(root / "packaging" / "launcher.py")], pathex=[str(root)], datas=datas,
    hiddenimports=["keyring.backends.Windows", "win32ctypes.pywin32", "googleapiclient.discovery_cache.documents"],
    excludes=["tkinter", "pytest", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="vMixYouTubeUploader",
          console=False, debug=False, strip=False, upx=False,
          icon=str(root / "vmix_youtube_uploader" / "assets" / "app.ico"),
          version=str(root / "build" / "version-info.txt"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="vMixYouTubeUploader")
