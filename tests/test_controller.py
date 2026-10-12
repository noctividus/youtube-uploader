import subprocess
import sys

from PySide6.QtCore import QCoreApplication, QEvent

from vmix_youtube_uploader.config import Settings, save_settings
from vmix_youtube_uploader.main import Controller
from vmix_youtube_uploader.models import Snapshot
from dataclasses import replace
from vmix_youtube_uploader.models import Status


def test_controller_holds_reviews_while_locked_and_shows_one_window(app, database, tmp_path, monkeypatch):
    data = tmp_path / "app"
    save_settings(data / "settings.json", Settings(notifications=False, vmix_enabled=False))
    controller = Controller(app, data)
    controller.timer.stop()
    records = []
    for name in ("one.mp4", "two.mp4"):
        path = tmp_path / name
        path.write_bytes(b"content")
        record = database.observe(Snapshot.read(path), "")
        database.ready(record.id, "fingerprint")
        records.append(database.get(record.id))
    try:
        controller.records_changed(records)
        controller.settings = Settings(directories=[str(tmp_path)], notifications=False)
        controller.pending.update(r.id for r in records)
        monkeypatch.setattr("vmix_youtube_uploader.main.desktop_available", lambda: False)
        controller.present_next()
        assert controller.review_window is None
        assert len(controller.pending) == 2
        monkeypatch.setattr("vmix_youtube_uploader.main.desktop_available", lambda: True)
        controller.present_next()
        first = controller.review_window
        assert first.isVisible()
        controller.present_next()
        assert controller.review_window is first
        first.close()
        controller.present_next()
        assert controller.review_window is not first
        assert controller.review_window.record.id == records[1].id
        controller.show_review(records[0].id)
        assert controller.review_window.record.id == records[0].id
        controller.review_window.close()
        controller.records[records[0].id] = replace(records[0], status=Status.WAITING)
        controller.show_review(records[0].id, automatic=True)
        assert not controller.review_window.isVisible()
        controller.show_review(records[0].id)
        assert controller.review_window.isVisible()
        assert not controller.review_window.later.isEnabled()
        controller.review_window.close()
        stopped = replace(records[0], source="vmix", vmix_state="stopped", status=Status.WAITING, available=False)
        controller.settings = Settings(directories=[], notifications=False)
        controller.vmix_recording_stopped(stopped.id)
        controller.records_changed([stopped])
        assert controller.review_window.isVisible()
        assert controller.review_window.record.id == stopped.id
        assert "vMix stopped" in controller.review_window.notice.text()
    finally:
        if controller.review_window:
            controller.review_window.close()
        controller.cleanup()
        controller.queue_window.close()
        app.processEvents()
        controller.queue_window.deleteLater()
        if controller.review_window:
            controller.review_window.deleteLater()
        controller.menu.deleteLater()
        controller.worker.deleteLater()
        controller.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_real_windows_tray_startup_and_graceful_exit(tmp_path):
    import os
    import pytest

    if os.name != "nt":
        pytest.skip("Windows desktop smoke check")
    data = tmp_path / "app"
    save_settings(data / "settings.json", Settings(notifications=False, vmix_enabled=False))
    # Launch the real entry point with the Windows platform plugin. Exit through
    # the tray controller, with a hard subprocess timeout to catch stuck workers.
    script = '''
import sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
import vmix_youtube_uploader.main as entry
Original = entry.Controller
class SmokeController(Original):
    def __init__(self, app, data):
        super().__init__(app, data)
        self.worker.error.connect(lambda text: print("WORKER ERROR:", text, flush=True))
        QTimer.singleShot(1500, self.shutdown)
entry.Controller = SmokeController
sys.exit(entry.main())
'''
    environment = dict(os.environ, QT_QPA_PLATFORM="windows")
    result = subprocess.run([sys.executable, "-c", script, "--data-dir", str(data)],
                            capture_output=True, text=True, timeout=15, env=environment)
    assert result.returncode == 0, result.stderr
    assert "WORKER ERROR:" not in result.stdout
    assert (data / "queue.sqlite3").exists()
    assert not (data / "instance.lock").exists()
