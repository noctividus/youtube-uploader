import sqlite3

import pytest

from vmix_youtube_uploader.database import Database


def test_database_initializes_schema_and_reopens_without_reset(tmp_path):
    path = tmp_path / "queue.sqlite3"
    database = Database(path)
    assert database.connection.execute("PRAGMA user_version").fetchone()[0] == 1
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
