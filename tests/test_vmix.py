from unittest.mock import Mock
import time

import pytest
from vmix_youtube_uploader.config import Settings, save_settings, validate_vmix_url
from vmix_youtube_uploader.models import Snapshot, Status
from vmix_youtube_uploader.vmix import VmixClient, VmixMonitor, VmixState, parse_state
from vmix_youtube_uploader.worker import Worker


def test_parse_recording_filename_and_streaming():
    state = parse_state('<vmix><recording filename1="C:\\Recordings\\A &amp; B.mp4" duration="20">True</recording><streaming>False</streaming></vmix>')
    assert state == VmixState(True, False, r"C:\Recordings\A & B.mp4")
    assert parse_state('<vmix><recording>False</recording></vmix>') == VmixState(False, None)


@pytest.mark.parametrize("xml", ["<broken", "<html/>", "<vmix/>", "<vmix><recording>unknown</recording></vmix>"])
def test_invalid_state_never_becomes_a_stop(xml):
    with pytest.raises(ValueError):
        parse_state(xml)


@pytest.mark.parametrize("url", ["http://localhost/api/?Function=StopRecording", "http://localhost/api/#x", "file:///api", "not a URL"])
def test_url_cannot_execute_a_vmix_command(url):
    with pytest.raises(ValueError):
        validate_vmix_url(url)


def test_client_is_read_only_bounded_and_rejects_redirects(monkeypatch):
    response = Mock(status_code=200, content=b"<vmix><recording>False</recording></vmix>")
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    session = Mock()
    session.get.return_value = context
    monkeypatch.setattr("vmix_youtube_uploader.vmix.requests.Session", lambda: session)
    client = VmixClient()
    assert not client.fetch("http://localhost:8088/api/").recording
    session.get.assert_called_once_with("http://localhost:8088/api/", timeout=(1, 2), allow_redirects=False)
    response.status_code = 302
    with pytest.raises(ValueError, match="302"):
        client.fetch("http://localhost:8088/api/")
    client.close()
    session.close.assert_called_once()


def test_start_queues_and_stop_completes_without_stability_wait(database, tmp_path):
    path = tmp_path / "recording.mp4"
    monitor = VmixMonitor(database, Settings(stability_seconds=86400))
    assert monitor.update(VmixState(False, False, str(path))) == []
    assert database.all() == []  # Stale idle filename is not a new recording.
    assert monitor.update(VmixState(True, False, str(path))) == []
    record = database.all()[0]
    assert record.vmix_state == "recording" and not record.available
    path.write_bytes(b"video")
    monitor.update(VmixState(True, True, str(path)))
    assert len(database.all()) == 1
    assert database.all()[0].id == record.id
    assert database.get(record.id).status == Status.WAITING
    assert monitor.update(VmixState(False, False)) == [record.id]
    monitor.check_completed()
    assert database.get(record.id).status == Status.READY
    assert database.get(record.id).vmix_state == "stopped"
    assert monitor.update(VmixState(False, False)) == []


def test_stop_popup_does_not_wait_for_file_unlock(database, tmp_path, monkeypatch):
    path = tmp_path / "recording.mp4"
    path.write_bytes(b"video")
    monitor = VmixMonitor(database, Settings())
    monitor.update(VmixState(True, False, str(path)))
    record = database.all()[0]
    monkeypatch.setattr("vmix_youtube_uploader.vmix.exclusive_access", lambda _: False)
    assert monitor.update(VmixState(False, False)) == [record.id]
    monitor.check_completed()
    assert database.get(record.id).status == Status.WAITING
    monkeypatch.setattr("vmix_youtube_uploader.vmix.exclusive_access", lambda _: True)
    monitor.check_completed()
    assert database.get(record.id).status == Status.READY


def test_disconnect_and_reconnect_idle_never_fabricate_stop(database, tmp_path):
    path = tmp_path / "recording.mp4"
    path.write_bytes(b"video")
    monitor = VmixMonitor(database, Settings())
    monitor.update(VmixState(True, False, str(path)))
    record_id = database.all()[0].id
    monitor.disconnect()
    assert database.get(record_id).vmix_state == "interrupted"
    assert monitor.update(VmixState(False, False)) == []
    monitor.check_completed()
    assert database.get(record_id).status != Status.READY
    monitor.update(VmixState(True, False, str(path)))
    assert len(database.all()) == 1  # Reattach to the explicitly named recording.
    assert monitor.update(VmixState(False, False)) == [record_id]


def test_split_primary_recordings_are_queued_individually(database, tmp_path):
    paths = [tmp_path / "one.mp4", tmp_path / "two.mp4"]
    monitor = VmixMonitor(database, Settings())
    for path in paths:
        path.write_bytes(b"video")
        monitor.update(VmixState(True, False, str(path)))
    assert len(database.all()) == 2
    stopped = monitor.update(VmixState(False, False))
    assert len(stopped) == 2
    monitor.check_completed()
    assert all(record.status == Status.READY for record in database.all())


def test_missing_filename_never_guesses_from_watch_folder(database, tmp_path):
    (tmp_path / "unrelated.mp4").write_bytes(b"video")
    monitor = VmixMonitor(database, Settings(directories=[str(tmp_path)]))
    monitor.update(VmixState(True, False))
    record = database.all()[0]
    assert record.path == ""
    assert monitor.update(VmixState(False, False)) == [record.id]
    monitor.check_completed()
    assert "filename" in database.get(record.id).error
    assert database.get(record.id).status != Status.READY


def test_existing_file_identity_does_not_duplicate_queue_row(database, tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    existing = database.observe(Snapshot.read(path), "")
    monitor = VmixMonitor(database, Settings())
    monitor.update(VmixState(True, False, str(path)))
    assert len(database.all()) == 1
    assert database.all()[0].id == existing.id
    assert monitor.update(VmixState(False, False)) == [existing.id]


def test_restart_preserves_stop_evidence_but_does_not_reopen_window(database, tmp_path):
    path = tmp_path / "recording.mp4"
    path.write_bytes(b"video")
    monitor = VmixMonitor(database, Settings())
    monitor.update(VmixState(True, False, str(path)))
    monitor.update(VmixState(False, False))
    database.recover()
    restarted = VmixMonitor(database, Settings())
    assert restarted.update(VmixState(False, False)) == []
    restarted.check_completed()
    assert database.all()[0].status == Status.READY


def test_filename_arrives_after_start_and_file_arrives_after_stop(database, tmp_path):
    path = tmp_path / "delayed.mp4"
    monitor = VmixMonitor(database, Settings())
    monitor.update(VmixState(True, False))
    record_id = database.all()[0].id
    monitor.update(VmixState(True, False, str(path)))
    assert len(database.all()) == 1
    assert monitor.update(VmixState(False, False)) == [record_id]
    monitor.check_completed()
    assert not database.get(record_id).available
    path.write_bytes(b"completed video")
    monitor.check_completed()
    assert database.get(record_id).status == Status.READY


def test_restart_during_recording_requires_a_confirmed_new_stop(database, tmp_path):
    path = tmp_path / "recording.mp4"
    path.write_bytes(b"video")
    monitor = VmixMonitor(database, Settings())
    monitor.update(VmixState(True, False, str(path)))
    record_id = database.all()[0].id
    database.recover()
    restarted = VmixMonitor(database, Settings())
    assert restarted.update(VmixState(False, False)) == []
    restarted.check_completed()
    assert database.get(record_id).vmix_state == "interrupted"
    assert database.get(record_id).status != Status.READY


def test_worker_vmix_mode_ignores_folders_and_emits_one_stop(app, tmp_path, monkeypatch):
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "unrelated.mp4").write_bytes(b"video")
    actual = tmp_path / "outside-watchlist.mp4"
    actual.write_bytes(b"recording")
    data = tmp_path / "data"
    save_settings(data / "settings.json", Settings(directories=[str(watched)], vmix_poll_seconds=1))
    state = [VmixState(False, False)]
    monkeypatch.setattr(VmixClient, "fetch", lambda self, url: state[0])
    worker = Worker(data)
    updates, stopped, errors = [], [], []
    worker.records_changed.connect(updates.append)
    worker.recording_stopped.connect(stopped.append)
    worker.error.connect(errors.append)

    def wait_for(predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(0.02)
        assert predicate()

    worker.start()
    try:
        wait_for(lambda: bool(updates))
        assert updates[-1] == []
        assert worker.observer is None
        state[0] = VmixState(True, False, str(actual))
        wait_for(lambda: bool(updates[-1]))
        assert updates[-1][0].vmix_state == "recording"
        assert stopped == []
        state[0] = VmixState(False, False)
        wait_for(lambda: len(stopped) == 1 and updates[-1][0].status == Status.READY)
        assert updates[-1][0].path == str(actual)
        assert errors == []
    finally:
        worker.stop()
        assert worker.wait(5000)
