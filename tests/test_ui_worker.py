import subprocess
import sys
import time
from dataclasses import replace

import pytest

from PySide6.QtCore import QLockFile, Qt
from PySide6.QtWidgets import QPushButton
from PySide6.QtTest import QTest

from vmix_youtube_uploader.config import Settings, save_settings
from vmix_youtube_uploader.models import Snapshot, Status
from vmix_youtube_uploader.ui import QueueWindow, ReviewWindow, SettingsWindow
from vmix_youtube_uploader.worker import Worker


@pytest.mark.parametrize("status,available", [
    (Status.WAITING, True), (Status.READY, True), (Status.SKIPPED, True), (Status.WAITING, False),
])
def test_mouse_selection_enables_upload_for_any_selected_record(app, database, tmp_path, status, available):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    record = replace(database.observe(Snapshot.read(path), ""), status=status, available=available)
    window = QueueWindow()
    requested = []
    window.review_requested.connect(requested.append)
    try:
        window.update_records([record])
        assert not window.review_button.isEnabled()
        window.show()
        app.processEvents()
        position = window.table.visualItemRect(window.table.item(0, 1)).center()
        QTest.mouseClick(window.table.viewport(), Qt.MouseButton.LeftButton, pos=position)
        assert window.review_button.isEnabled()
        window.review_button.click()
        assert requested == [record.id]
    finally:
        window.close()
        window.deleteLater()


def test_open_confirmation_updates_readiness_without_losing_edits(app, database, tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    record = database.observe(Snapshot.read(path), "")
    window = ReviewWindow(record)
    try:
        window.title.setText("My edited title")
        assert not window.later.isEnabled()
        assert "Checking file completion" in window.notice.text()
        database.ready(record.id, "fingerprint")
        window.update_record(database.get(record.id))
        assert window.title.text() == "My edited title"
        assert window.later.isEnabled()
        assert window.record.fingerprint == "fingerprint"
    finally:
        window.deleteLater()


def test_queue_shortcuts_sorting_and_selected_review(app, database, tmp_path):
    records = []
    for name, size in (("large.mp4", 10_000_000), ("small.mp4", 2_000_000)):
        path = tmp_path / name
        path.write_bytes(b"video")
        snapshot = Snapshot.read(path)
        record = database.observe(Snapshot(snapshot.path, snapshot.identity, size, snapshot.mtime_ns), "")
        database.ready(record.id, "fingerprint")
        records.append(database.get(record.id))
    window = QueueWindow()
    reviewed, scans, settings = [], [], []
    window.review_requested.connect(reviewed.append)
    window.scan_requested.connect(lambda: scans.append(True))
    window.settings_requested.connect(lambda: settings.append(True))
    try:
        window.update_records(records)
        assert {button.text() for button in window.findChildren(QPushButton)} == {"Settings", "Upload", "Add Video"}
        window.show()
        assert scans == [True]
        window.settings_button.click()
        assert settings == [True]
        for column in range(window.table.columnCount()):
            for order in (Qt.SortOrder.AscendingOrder, Qt.SortOrder.DescendingOrder):
                window.table.sortItems(column, order)
                values = [window.table.item(row, column).sort_value for row in range(2)]
                assert values == sorted(values, reverse=order == Qt.SortOrder.DescendingOrder)
        window.table.sortItems(3, Qt.SortOrder.AscendingOrder)
        window.table.selectRow(0)
        assert window.selected().id == records[1].id  # 2 MB sorts before 10 MB.
        assert window.table.item(0, 1).text() == tmp_path.name
        window.update_records(records)
        assert window.selected().id == records[1].id
        window.table.itemDoubleClicked.emit(window.table.item(0, 0))
        assert not reviewed
        window.review_button.click()
        assert reviewed == [records[1].id]
        window.close()
        window.show()
        assert len(scans) == 2
    finally:
        window.close()
        window.deleteLater()


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


def test_tabbed_settings_add_remove_validate_and_restore_webhooks(app):
    window = SettingsWindow(Settings(description="Keep my description", notifications=False))
    saved = []
    window.saved.connect(saved.append)
    try:
        assert [window.tabs.tabText(i) for i in range(4)] == ["Files", "Triggers", "Webhooks", "General"]
        window.add_webhook_button.click()
        window.save()
        assert not saved
        assert window.validation_error.text()
        first = window.webhook_editors[0]
        first.url.setText("https://example.com/hook")
        first.events["recording_started"].setChecked(True)
        first.events["error"].setChecked(True)
        window.add_webhook_button.click()
        window.remove_webhook(window.webhook_editors[1])
        window.save()
        assert saved[0].webhooks[0].events == ["recording_started", "error"]
        assert saved[0].description == "Keep my description"
        assert not saved[0].notifications
        restored = SettingsWindow(saved[0])
        try:
            assert restored.webhook_editors[0].url.text() == "https://example.com/hook"
            assert restored.webhook_editors[0].events["error"].isChecked()
            assert not restored.webhook_editors[0].events["upload_started"].isChecked()
        finally:
            restored.deleteLater()
    finally:
        window.deleteLater()


def test_vmix_settings_roundtrip_and_connection_test_signal(app):
    window = SettingsWindow(Settings(vmix_enabled=True, vmix_url="http://localhost:8088/api/"))
    saved, tested = [], []
    window.saved.connect(saved.append)
    window.vmix_test_requested.connect(tested.append)
    try:
        assert not window.stability.isEnabled()
        window.vmix_test.click()
        assert tested == ["http://localhost:8088/api/"]
        assert not window.vmix_test.isEnabled()
        window.show_vmix_test_result("Connected")
        assert window.vmix_test.isEnabled()
        window.vmix_poll.setValue(1)
        window.save()
        assert saved[0].vmix_enabled
        assert saved[0].vmix_poll_seconds == 1
        window.vmix_enabled.setChecked(False)
        assert window.stability.isEnabled()
    finally:
        window.deleteLater()


def test_worker_discovers_watchdog_events_and_persists_review(app, tmp_path):
    watched = tmp_path / "recordings"
    watched.mkdir()
    (watched / "existing.mp4").write_bytes(b"existing video")
    data = tmp_path / "data"
    save_settings(data / "settings.json", Settings(
        directories=[str(watched)], stability_seconds=1, periodic_scan=False, notifications=False,
        vmix_enabled=False,
    ))
    worker = Worker(data)
    updates, errors, saved, automatic = [], [], [], []
    worker.records_changed.connect(updates.append)
    worker.error.connect(errors.append)
    worker.reviewed.connect(saved.append)
    worker.ready.connect(automatic.append)
    worker.start()
    try:
        wait_for(app, lambda: bool(updates))
        wait_for(app, lambda: bool(updates[-1]) and updates[-1][0].status == Status.READY)
        assert automatic == []
        (watched / "video.mp4").write_bytes(b"video bytes")
        wait_for(app, lambda: len(updates[-1]) == 2 and updates[-1][0].status == Status.READY)
        record = updates[-1][0]
        assert automatic == [record.id]
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


def test_manual_picker_and_upload_percent(app, database, tmp_path, monkeypatch):
    from vmix_youtube_uploader.ui import QFileDialog
    path = tmp_path / "manual.mp4"
    path.write_bytes(b"video")
    record = database.observe(Snapshot.read(path), "")
    window = QueueWindow()
    added = []
    window.add_video_requested.connect(added.append)
    try:
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
        window.add_video_button.click()
        assert added == [str(path)]
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))
        window.add_video_button.click()
        assert len(added) == 1
        for status, sent, expected in [(Status.UPLOADING, 0, "0.0%"),
                                       (Status.UPLOADING, 125, "12.5%"),
                                       (Status.UPLOADING, 1000, "99.9%"),
                                       (Status.UPLOADED, 1000, "100.0%")]:
            window.update_records([replace(record, status=status, size=1000, bytes_uploaded=sent)])
            window.select_record(record.id)
            assert window.table.item(0, 5).text() == expected
            assert window.selected().id == record.id
    finally:
        window.deleteLater()


@pytest.mark.parametrize("vmix_enabled", [False, True])
def test_worker_manual_add_without_triggers(app, tmp_path, monkeypatch, vmix_enabled):
    from vmix_youtube_uploader.vmix import VmixState
    monkeypatch.setattr("vmix_youtube_uploader.worker.VmixClient.fetch", lambda *_: VmixState(False, False))
    data = tmp_path / "data"
    save_settings(data / "settings.json", Settings(vmix_enabled=vmix_enabled, notifications=False,
                  directories=[], file_extensions=[".mov"], stability_seconds=3600))
    path = tmp_path / "manual.mp4"
    path.write_bytes(b"test video")
    worker = Worker(data)
    updates, added, automatic, errors = [], [], [], []
    worker.records_changed.connect(updates.append)
    worker.manual_added.connect(added.append)
    worker.ready.connect(automatic.append)
    worker.error.connect(errors.append)
    worker.start()
    try:
        worker.submit("pause", True)
        worker.submit("add_video", str(path))
        wait_for(app, lambda: bool(added) and bool(updates[-1]))
        record = updates[-1][0]
        assert record.id == added[0] and record.status == Status.READY
        assert record.source == "manual" and record.fingerprint
        assert not record.approved and record.privacy == "private"
        worker.submit("add_video", str(path))
        wait_for(app, lambda: len(added) == 2)
        assert added[0] == added[1] and len(updates[-1]) == 1
        assert not automatic and not any(errors)
    finally:
        worker.stop()
        assert worker.wait(5000)
