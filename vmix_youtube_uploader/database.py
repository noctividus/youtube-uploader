"""SQLite repository. A single monitor worker owns this connection."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import Recording, Snapshot, Status


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self._migrate()
        except Exception:
            self.connection.close()
            raise

    def _migrate(self) -> None:
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version > 1:
            raise RuntimeError("This database belongs to a newer application version.")
        if version == 0:
            with self.connection:
                self.connection.execute("BEGIN IMMEDIATE")
                self.connection.execute("""
                    CREATE TABLE recordings (
                        id INTEGER PRIMARY KEY, path TEXT NOT NULL,
                        identity TEXT NOT NULL UNIQUE, size INTEGER NOT NULL,
                        mtime_ns INTEGER NOT NULL, detected_at TEXT NOT NULL,
                        completed_at TEXT,
                        status TEXT NOT NULL CHECK(status IN (
                            'Detecting', 'Waiting for File Completion', 'Ready for Review',
                            'Approved / Queued', 'Uploading', 'Uploaded', 'Failed', 'Skipped')),
                        title TEXT NOT NULL, description TEXT NOT NULL,
                        privacy TEXT NOT NULL DEFAULT 'private'
                            CHECK(privacy IN ('private', 'unlisted', 'public')),
                        deferred INTEGER NOT NULL DEFAULT 0,
                        hidden INTEGER NOT NULL DEFAULT 0,
                        available INTEGER NOT NULL DEFAULT 1,
                        approved INTEGER NOT NULL DEFAULT 0,
                        bytes_uploaded INTEGER NOT NULL DEFAULT 0,
                        youtube_id TEXT, youtube_url TEXT, error TEXT,
                        retry_count INTEGER NOT NULL DEFAULT 0, fingerprint TEXT,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    )
                """)
                self.connection.execute("PRAGMA user_version=1")

    def close(self) -> None:
        self.connection.close()

    def all(self, include_hidden: bool = False) -> list[Recording]:
        query = "SELECT * FROM recordings"
        if not include_hidden:
            query += " WHERE hidden=0"
        return [self._record(row) for row in self.connection.execute(query + " ORDER BY id DESC")]

    @staticmethod
    def _record(row: sqlite3.Row) -> Recording:
        values = dict(row)
        values["status"] = Status(values["status"])
        for key in ("deferred", "hidden", "available", "approved"):
            values[key] = bool(values[key])
        return Recording(**values)

    def get(self, record_id: int) -> Recording:
        row = self.connection.execute("SELECT * FROM recordings WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise ValueError("Recording no longer exists.")
        return self._record(row)

    def observe(self, snapshot: Snapshot, description: str) -> Recording:
        row = self.connection.execute(
            "SELECT * FROM recordings WHERE identity=?", (snapshot.identity,)
        ).fetchone()
        timestamp = now()
        with self.connection:
            if row is None:
                cursor = self.connection.execute("""
                    INSERT INTO recordings
                        (path, identity, size, mtime_ns, detected_at, status,
                         title, description, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (snapshot.path, snapshot.identity, snapshot.size, snapshot.mtime_ns,
                      timestamp, Status.WAITING, Path(snapshot.path).stem.replace("_", " "),
                      description, timestamp, timestamp))
                record_id = cursor.lastrowid
            else:
                record_id = row["id"]
                if (row["size"], row["mtime_ns"]) != (snapshot.size, snapshot.mtime_ns):
                    # Changed content invalidates all earlier approval/suppression.
                    self.connection.execute("""
                        UPDATE recordings SET path=?, size=?, mtime_ns=?, status=?,
                            completed_at=NULL, fingerprint=NULL, approved=0,
                            deferred=0, hidden=0, available=1, bytes_uploaded=0,
                            youtube_id=NULL, youtube_url=NULL, error=NULL,
                            retry_count=0, updated_at=? WHERE id=?
                    """, (snapshot.path, snapshot.size, snapshot.mtime_ns, Status.WAITING,
                          timestamp, record_id))
                elif row["path"] != snapshot.path or not row["available"]:
                    self.connection.execute(
                        "UPDATE recordings SET path=?, available=1, updated_at=? WHERE id=?",
                        (snapshot.path, timestamp, record_id),
                    )
        return self.get(record_id)

    def available(self, record_id: int, available: bool) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE recordings SET available=?, updated_at=? WHERE id=? AND available<>?",
                (int(available), now(), record_id, int(available)),
            )

    def waiting(self, record_id: int) -> None:
        with self.connection:
            self.connection.execute("""
                UPDATE recordings SET status=?, approved=0, completed_at=NULL,
                    updated_at=? WHERE id=? AND status IN (?, ?)
            """, (Status.WAITING, now(), record_id, Status.DETECTING, Status.READY))

    def ready(self, record_id: int, fingerprint: str) -> None:
        with self.connection:
            self.connection.execute("""
                UPDATE recordings SET status=?, completed_at=?, fingerprint=?, updated_at=?
                WHERE id=? AND status=? AND available=1
            """, (Status.READY, now(), fingerprint, now(), record_id, Status.WAITING))

    def recover(self) -> None:
        with self.connection:
            self.connection.execute("""
                UPDATE recordings SET status=?, approved=0, error=?, updated_at=?
                WHERE status IN (?, ?)
            """, (Status.FAILED, "Interrupted operation: review required; do not automatically retry.",
                  now(), Status.APPROVED, Status.UPLOADING))
            self.connection.execute("""
                UPDATE recordings SET status=?, completed_at=NULL, updated_at=?
                WHERE status IN (?, ?)
            """, (Status.WAITING, now(), Status.READY, Status.DETECTING))
            self.connection.execute("UPDATE recordings SET available=0")

    def review_action(
        self, record_id: int, version: tuple[str, int, int, str | None], action: str,
        title: str, description: str, privacy: str,
    ) -> None:
        record = self.get(record_id)
        if version != (record.identity, record.size, record.mtime_ns, record.fingerprint):
            raise ValueError("The file changed. Close this review and review the new version.")
        if record.status != Status.READY or not record.available:
            raise ValueError("This recording is no longer ready. Wait for file completion.")
        if action not in {"later", "skip"} or privacy not in {"private", "unlisted", "public"}:
            raise ValueError("Invalid review action or privacy.")
        if not title.strip():
            raise ValueError("Title cannot be empty.")
        with self.connection:
            self.connection.execute("""
                UPDATE recordings SET title=?, description=?, privacy=?, status=?,
                    deferred=?, approved=0, updated_at=? WHERE id=?
            """, (title.strip(), description, privacy,
                  Status.SKIPPED if action == "skip" else Status.READY,
                  int(action == "later"), now(), record_id))

    def remove(self, record_id: int) -> None:
        record = self.get(record_id)
        if record.status in {Status.UPLOADING, Status.UPLOADED}:
            raise ValueError("Cannot remove an uploading or uploaded recording.")
        with self.connection:
            self.connection.execute(
                "UPDATE recordings SET hidden=1, status=?, approved=0, updated_at=? WHERE id=?",
                (Status.SKIPPED, now(), record_id),
            )
