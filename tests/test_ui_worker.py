import subprocess
import sys
import time

from PySide6.QtCore import QLockFile

from vmix_youtube_uploader.config import Settings, save_settings
from vmix_youtube_uploader.models import Snapshot, Status
from vmix_youtube_uploader.ui import QueueWindow, ReviewWindow, SettingsWindow
from vmix_youtube_uploader.worker import Worker


def wait_for(app, predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.02)
    assert predicate(), "Timed out waiting for worker"


def test_settings_extension_checkboxes_save_and_restore(app):
    window = SettingsWindow(Settings(file_extensions=[".webm", ".flv"]))
    saved = []
    window.saved.connect(saved.append)
    try:
        assert window.extension_checkboxes[".webm"].isChecked()
        assert not window.extension_checkboxes[".mp4"].isChecked()
        window.extension_checkboxes[".flv"].setChecked(False)
        window.extension_checkboxes[".wmv"].setChecked(True)
        window.save()
        assert set(saved[0].file_extensions) == {".webm", ".wmv"}
    finally:
        window.close()
        window.deleteLater()


def test_worker_discovers_watchdog_events_and_persists_review(app, tmp_path):
    watched = tmp_path / "recordings"
    watched.mkdir()
    data = tmp_path / "data"
    save_settings(data / "settings.json", Settings(
        directories=[str(watched)], stability_seconds=1, periodic_scan=False, notifications=False,
    ))
    worker = Worker(data)
    updates, errors, saved = [], [], []
    worker.records_changed.connect(updates.append)
    worker.error.connect(errors.append)
    worker.reviewed.connect(saved.append)
    worker.start()
    try:
        wait_for(app, lambda: bool(updates))
        (watched / "video.mp4").write_bytes(b"video bytes")
        wait_for(app, lambda: bool(updates[-1]) and updates[-1][0].status == Status.READY)
        record = updates[-1][0]
        worker.submit("review", (record.id, (record.identity, record.size, record.mtime_ns, record.fingerprint),
                                  "later", "Edited", "Description", "private"))
        wait_for(app, lambda: bool(saved))
        assert not errors
        wait_for(app, lambda: updates[-1][0].deferred)
        assert updates[-1][0].title == "Edited"
    finally:
        worker.stop()
        assert worker.wait(5000)


def test_ui_review_payload_and_close_keeps_application_alive(app, database, tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    record = database.observe(Snapshot.read(path), "Default")
    database.ready(record.id, "fingerprint")
    record = database.get(record.id)
    queue = QueueWindow()
    queue.update_records([record])
    queue.table.selectRow(0)
    assert queue.review_button.isEnabled()
    review = ReviewWindow(record)
    actions = []
    review.action_requested.connect(actions.append)
    review.title.setText("New title")
    review.later.click()
    assert actions[0][2:] == ("later", "New title", "Default", "private")
    queue.show()
    queue.close()
    review.close()
    assert not app.quitOnLastWindowClosed()


def test_single_instance_lock_blocks_another_process_and_releases(tmp_path):
    path = str(tmp_path / "instance.lock")
    lock = QLockFile(path)
    lock.setStaleLockTime(0)
    assert lock.tryLock(0)
    script = "from PySide6.QtCore import QLockFile; import sys; lock=QLockFile(sys.argv[1]); lock.setStaleLockTime(0); print(lock.tryLock(0))"
    try:
        result = subprocess.run([sys.executable, "-c", script, path], capture_output=True, text=True, check=True)
        assert result.stdout.strip() == "False"
    finally:
        lock.unlock()
    result = subprocess.run([sys.executable, "-c", script, path], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "True"
