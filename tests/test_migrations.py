import sqlite3

import pytest

from vmix_youtube_uploader.database import Database
from vmix_youtube_uploader.models import Snapshot


def test_database_initializes_schema_and_reopens_without_reset(tmp_path):
    path = tmp_path / "queue.sqlite3"
    database = Database(path)
    assert database.connection.execute("PRAGMA user_version").fetchone()[0] == 2
    database.close()
    reopened = Database(path)
    assert reopened.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    reopened.close()


def test_newer_schema_is_rejected_without_rewriting(tmp_path):
    path = tmp_path / "queue.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(RuntimeError, match="newer"):
        Database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_version_one_database_migrates_without_losing_recordings(tmp_path):
    path = tmp_path / "queue.sqlite3"
    video = tmp_path / "recording.mp4"
    video.write_bytes(b"video")
    database = Database(path)
    record = database.observe(Snapshot.read(video), "Original description")
    database.close()
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE recordings DROP COLUMN source")
        connection.execute("ALTER TABLE recordings DROP COLUMN vmix_state")
        connection.execute("PRAGMA user_version=1")
    migrated = Database(path)
    try:
        assert migrated.get(record.id).description == "Original description"
        assert migrated.get(record.id).source == "filesystem"
        assert migrated.get(record.id).vmix_state == ""
        assert migrated.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        migrated.close()
