import pytest

from vmix_youtube_uploader.config import Settings
from vmix_youtube_uploader.manual import ManualQueue
from vmix_youtube_uploader.models import Status
from vmix_youtube_uploader.monitor import Monitor


def test_manual_ready_survives_scan_and_restart(database, tmp_path):
    path = tmp_path / "manual.mp4"
    path.write_bytes(b"video contents")
    manual = ManualQueue(database)
    record_id = manual.add(str(path), "Description")
    record = database.get(record_id)
    assert record.status == Status.READY and record.source == "manual"
    assert record.description == "Description" and record.fingerprint
    monitor = Monitor(database, Settings(directories=[str(tmp_path)]))
    assert monitor.tick(0, scan=True) == []
    assert not monitor.auto_review_ids
    assert database.get(record_id).available
    database.recover()
    manual.refresh()
    assert database.get(record_id).status == Status.READY
    assert database.get(record_id).available
    path.unlink()
    manual.refresh()
    assert not database.get(record_id).available
    assert database.get(record_id).status == Status.WAITING


def test_readding_keeps_upload_history_and_ambiguous_guard(database, tmp_path):
    path = tmp_path / "manual.mp4"
    path.write_bytes(b"video contents")
    manual = ManualQueue(database)
    record_id = manual.add(str(path), "")
    record = database.get(record_id)
    version = (record.identity, record.size, record.mtime_ns, record.fingerprint)
    job = database.approve_upload(record_id, version, "Title", "", "private", "channel")
    with pytest.raises(ValueError, match="already"):
        manual.add(str(path), "")
    database.upload_event("failed", {"attempt_id": job.attempt_id, "error": "uncertain", "ambiguous": True})
    assert manual.add(str(path), "") == record_id
    assert database.get(record_id).upload_ambiguous
    with pytest.raises(ValueError, match="duplicate"):
        database.approve_upload(record_id, version, "Title", "", "private", "channel")
    database.upload_event("created", {"attempt_id": job.attempt_id, "video_id": "video", "url": "https://www.youtube.com/watch?v=video"})
    manual.add(str(path), "")
    assert database.get(record_id).status == Status.UPLOADED
    assert len(database.all()) == 1


@pytest.mark.parametrize("kind", ["empty", "busy", "missing", "unsupported", "changing"])
def test_invalid_manual_add_does_not_queue(database, tmp_path, monkeypatch, kind):
    path = tmp_path / ("file.txt" if kind == "unsupported" else "file.mp4")
    if kind != "missing":
        path.write_bytes(b"" if kind == "empty" else b"video")
    if kind == "busy":
        monkeypatch.setattr("vmix_youtube_uploader.manual.exclusive_access", lambda _: False)
    if kind == "changing":
        def fingerprint(*_):
            path.write_bytes(b"changed recording content")
            return "fingerprint"
        monkeypatch.setattr("vmix_youtube_uploader.manual.sampled_fingerprint", fingerprint)
    with pytest.raises((ValueError, OSError)):
        ManualQueue(database).add(str(path), "")
    assert not database.all()
