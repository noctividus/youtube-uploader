import os
from pathlib import Path

import pytest

from vmix_youtube_uploader.config import Settings
from vmix_youtube_uploader.database import Database
from vmix_youtube_uploader.files import CompletionTracker, exclusive_access, revalidate
from vmix_youtube_uploader.models import Snapshot, Status
from vmix_youtube_uploader.monitor import Monitor


def video(path: Path, content=b"recording") -> Path:
    path.write_bytes(content)
    return path


def test_configured_filter_controls_discovery_and_keeps_existing_queue(database, tmp_path):
    for name in ("one.WEBM", "two.mp4", "three.wmv"):
        video(tmp_path / name)
    monitor = Monitor(database, Settings(directories=[str(tmp_path)], file_extensions=[".webm"]))
    monitor.tick(0, scan=True)
    assert {Path(r.path).name for r in database.all()} == {"one.WEBM"}
    monitor.configure(Settings(directories=[str(tmp_path)], file_extensions=[".wmv"]))
    monitor.tick(1, scan=True)
    assert {Path(r.path).name for r in database.all()} == {"one.WEBM", "three.wmv"}
    monitor.configure(Settings(directories=[str(tmp_path)], file_extensions=[]))
    video(tmp_path / "four.wmv")
    monitor.tick(2, scan=True)
    assert len(database.all()) == 2


def version(record):
    return record.identity, record.size, record.mtime_ns, record.fingerprint


def ready_monitor(database, directory):
    monitor = Monitor(database, Settings(directories=[str(directory)], stability_seconds=10))
    monitor.tick(0, scan=True)
    monitor.tick(10)
    return monitor


def test_growing_video_restarts_stability_and_duplicate_events_do_not_duplicate(database, tmp_path):
    path = video(tmp_path / "recording.MP4")
    monitor = Monitor(database, Settings(directories=[str(tmp_path)], stability_seconds=10))
    assert monitor.tick(0, scan=True) == []
    monitor.tick(9, scan=True)
    path.write_bytes(b"recording plus more")
    assert monitor.tick(10, scan=True) == []
    assert monitor.tick(19) == []
    ids = monitor.tick(20)
    assert len(ids) == 1
    assert monitor.tick(30, scan=True) == []
    assert len(database.all()) == 1
    assert database.get(ids[0]).status == Status.READY


def test_existing_multiple_recordings_and_supported_extensions(database, tmp_path):
    for name in ("a.mp4", "b.mkv", "c.mov", "d.avi", "ignore.txt"):
        video(tmp_path / name)
    monitor = ready_monitor(database, tmp_path)
    assert len(database.all()) == 4
    assert all(r.status == Status.READY and not r.approved and r.privacy == "private" for r in database.all())
    video(tmp_path / "missed.mp4")
    monitor.tick(11)
    assert len(database.all()) == 4
    monitor.tick(12, scan=True)
    monitor.tick(22)
    assert len(database.all()) == 5


def test_later_skip_and_removal_persist_and_sources_remain(tmp_path):
    for name in ("later.mp4", "skip.mp4", "remove.mp4"):
        video(tmp_path / name)
    db_path = tmp_path / "queue.sqlite3"
    db = Database(db_path)
    ready_monitor(db, tmp_path)
    records = {Path(r.path).stem: r for r in db.all()}
    for action in ("later", "skip"):
        record = records[action]
        db.review_action(record.id, version(record), action, "Edited title", "Description", "private")
    db.remove(records["remove"].id)
    db.close()
    db = Database(db_path)
    try:
        db.recover()
        ready_monitor(db, tmp_path)
        assert len(db.all()) == 2
        assert len(db.all(include_hidden=True)) == 3
        assert db.get(records["later"].id).status == Status.READY
        assert db.get(records["later"].id).deferred
        assert db.get(records["later"].id).title == "Edited title"
        assert db.get(records["skip"].id).status == Status.SKIPPED
        assert db.get(records["remove"].id).hidden
        assert all(Path(r.path).exists() for r in records.values())
    finally:
        db.close()


def test_changed_file_invalidates_skip_and_stale_review(database, tmp_path):
    path = video(tmp_path / "recording.mp4")
    monitor = ready_monitor(database, tmp_path)
    old = database.all()[0]
    database.review_action(old.id, version(old), "skip", old.title, old.description, "private")
    path.write_bytes(b"replacement data that is larger")
    monitor.tick(11)
    updated = database.get(old.id)
    assert updated.status == Status.WAITING
    assert not updated.approved and not updated.hidden
    monitor.tick(21)
    with pytest.raises(ValueError, match="changed"):
        database.review_action(old.id, version(old), "later", old.title, old.description, "private")


def test_rename_retains_skip_but_replacement_at_same_path_is_new(database, tmp_path):
    path = video(tmp_path / "recording.mp4")
    monitor = ready_monitor(database, tmp_path)
    record = database.all()[0]
    database.review_action(record.id, version(record), "skip", record.title, record.description, "private")
    renamed = path.rename(tmp_path / "renamed.mp4")
    monitor.tick(11, scan=True)
    assert len(database.all()) == 1
    assert database.get(record.id).path == str(renamed)
    assert database.get(record.id).status == Status.SKIPPED
    video(path)
    monitor.tick(12, scan=True)
    assert len(database.all()) == 2


def test_disappearance_revokes_readiness_and_requires_new_observation(database, tmp_path):
    path = video(tmp_path / "recording.mp4")
    monitor = ready_monitor(database, tmp_path)
    record = database.all()[0]
    away = path.rename(tmp_path / "away.tmp")
    monitor.tick(11)
    assert not database.get(record.id).available
    assert database.get(record.id).status == Status.WAITING
    away.rename(path)
    monitor.tick(30)
    assert database.get(record.id).status == Status.WAITING
    monitor.tick(40)
    assert database.get(record.id).status == Status.READY


def test_startup_requires_new_stability_but_preserves_uploaded_history(database, tmp_path):
    video(tmp_path / "recording.mp4")
    ready_monitor(database, tmp_path)
    record = database.all()[0]
    database.recover()
    assert database.get(record.id).status == Status.WAITING
    assert not database.get(record.id).available
    with database.connection:
        database.connection.execute(
            "UPDATE recordings SET status=?, youtube_id=?, youtube_url=? WHERE id=?",
            (Status.UPLOADED, "example", "https://youtu.be/example", record.id),
        )
    database.recover()
    ready_monitor(database, tmp_path)
    assert database.get(record.id).status == Status.UPLOADED
    assert database.get(record.id).youtube_id == "example"


@pytest.mark.parametrize("status", [Status.APPROVED, Status.UPLOADING])
def test_interrupted_operations_never_restart_automatically(database, tmp_path, status):
    record = database.observe(Snapshot.read(video(tmp_path / "recording.mp4")), "")
    with database.connection:
        database.connection.execute("UPDATE recordings SET status=?, approved=1 WHERE id=?", (status, record.id))
    database.recover()
    ready_monitor(database, tmp_path)
    recovered = database.get(record.id)
    assert recovered.status == Status.FAILED
    assert not recovered.approved
    assert "do not automatically retry" in recovered.error


def test_empty_or_busy_files_never_ready_and_lock_resets_interval(tmp_path):
    path = video(tmp_path / "empty.mp4", b"")
    tracker = CompletionTracker(10, access=lambda _: True)
    assert tracker.check(Snapshot.read(path), 0) is None
    assert tracker.check(Snapshot.read(path), 100) is None
    path.write_bytes(b"content")
    tracker.check(Snapshot.read(path), 101)
    tracker.access = lambda _: False
    assert tracker.check(Snapshot.read(path), 111) is None
    tracker.access = lambda _: True
    assert tracker.check(Snapshot.read(path), 112) is None
    assert tracker.check(Snapshot.read(path), 121) is not None


def test_final_verification_detects_change_with_same_size_and_timestamp(database, tmp_path):
    path = video(tmp_path / "recording.mp4", b"abc")
    ready_monitor(database, tmp_path)
    record = database.all()[0]
    assert revalidate(record.path, version(record)[:3], record.fingerprint)
    path.write_bytes(b"def")
    os.utime(path, ns=(record.mtime_ns, record.mtime_ns))
    assert not revalidate(record.path, version(record)[:3], record.fingerprint)


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics")
def test_windows_exclusive_access_rejects_open_writer(tmp_path):
    path = video(tmp_path / "recording.mp4")
    assert exclusive_access(path)
    with path.open("ab") as writer:
        writer.write(b"more")
        writer.flush()
        assert not exclusive_access(path)
    assert exclusive_access(path)


def test_one_missing_directory_does_not_block_other_directories(database, tmp_path):
    video(tmp_path / "recording.mp4")
    monitor = Monitor(database, Settings(directories=[str(tmp_path / "missing"), str(tmp_path)], stability_seconds=1))
    monitor.tick(0, scan=True)
    assert monitor.errors
    monitor.tick(1)
    assert monitor.errors
    assert database.all()[0].status == Status.READY


def test_removed_item_stays_suppressed_until_content_changes(database, tmp_path):
    path = video(tmp_path / "recording.mp4")
    monitor = ready_monitor(database, tmp_path)
    record = database.all()[0]
    database.remove(record.id)
    monitor.tick(11, scan=True)
    assert database.all() == []
    path.write_bytes(b"new recording")
    monitor.tick(12)
    assert len(database.all()) == 1
    assert database.all()[0].status == Status.WAITING
