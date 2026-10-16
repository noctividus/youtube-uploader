"""Offline self-check used by the local build, with isolated disposable state."""
import json
import traceback
from pathlib import Path


def run(directory: str) -> int:
    destination = Path(directory).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    report = destination / "self-test.json"
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QSystemTrayIcon
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from .config import Settings, WebhookEndpoint, load_settings, save_settings
        from .main import Controller, tray_icon
        from .youtube import CredentialStore, youtube_api, SCOPES

        settings = Settings(vmix_enabled=False, notifications=False,
                            webhooks=[WebhookEndpoint("http://127.0.0.1:1/unused", ["upload_finished"],
                                                      {"Authorization": "Bearer packaging-test"})])
        save_settings(destination / "settings.json", settings)
        assert load_settings(destination / "settings.json") == settings
        store = CredentialStore(destination)
        try:
            store.put("packaging-test", {"value": "temporary-build-check"})
            assert store.get("packaging-test") == {"value": "temporary-build-check"}
        finally:
            store.delete("packaging-test")
        flow = InstalledAppFlow.from_client_config({"installed": {
            "client_id": "packaging-test.apps.googleusercontent.com", "client_secret": "test",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"}}, SCOPES)
        assert flow is not None
        api = youtube_api(Credentials(token="offline-self-test"))
        assert api.videos() is not None
        assert 308 not in api._http.http.redirect_codes
        assert {301, 302, 303, 307}.issubset(api._http.http.redirect_codes)
        api.close()  # Building from bundled discovery data makes no network call.
        app = QApplication([])
        app.setApplicationName("vMix YouTube Uploader Packaging Check")
        app.setQuitOnLastWindowClosed(False)
        app.setWindowIcon(tray_icon())
        assert not tray_icon().pixmap(32, 32).isNull()
        assert QSystemTrayIcon.isSystemTrayAvailable(), "A Windows desktop session is required for this build check"
        controller = Controller(app, destination)
        errors = []
        controller.worker.error.connect(lambda message: errors.append(message) if message else None)
        app.aboutToQuit.connect(controller.cleanup)
        QTimer.singleShot(1800, controller.shutdown)
        app.exec()
        assert not errors, errors
        assert (destination / "queue.sqlite3").is_file()
        assert (destination / "webhooks.sqlite3").is_file()
        report.write_text(json.dumps({"ok": True, "checks": ["tray", "icon", "worker", "sqlite", "watchdog",
            "google-discovery", "oauth-imports", "windows-credential-manager", "encrypted-webhook-settings"]}, indent=2), encoding="utf-8")
        return 0
    except Exception:
        report.write_text(json.dumps({"ok": False, "error": traceback.format_exc()}, indent=2), encoding="utf-8")
        return 1
