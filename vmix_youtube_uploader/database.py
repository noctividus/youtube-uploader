"""SQLite repository. A single monitor worker owns this connection."""

import sqlite3
import uuid
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
        if version > 3:
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
        if version < 2:
            with self.connection:
                self.connection.execute("BEGIN IMMEDIATE")
                self.connection.execute("ALTER TABLE recordings ADD COLUMN source TEXT NOT NULL DEFAULT 'filesystem'")
                self.connection.execute("ALTER TABLE recordings ADD COLUMN vmix_state TEXT NOT NULL DEFAULT ''")
                self.connection.execute("PRAGMA user_version=2")
        if version < 3:
            with self.connection:
                self.connection.execute("BEGIN IMMEDIATE")
                self.connection.execute("ALTER TABLE recordings ADD COLUMN current_attempt TEXT NOT NULL DEFAULT ''")
                self.connection.execute("ALTER TABLE recordings ADD COLUMN upload_ambiguous INTEGER NOT NULL DEFAULT 0")
                self.connection.execute("""CREATE TABLE upload_attempts (
                    id TEXT PRIMARY KEY, recording_id INTEGER NOT NULL, job TEXT NOT NULL,
                    status TEXT NOT NULL, bytes_uploaded INTEGER NOT NULL DEFAULT 0,
                    video_id TEXT, youtube_url TEXT, error TEXT, ambiguous INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
                self.connection.execute("PRAGMA user_version=3")

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
        for key in ("deferred", "hidden", "available", "approved", "upload_ambiguous"):
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
                            retry_count=0, current_attempt='', upload_ambiguous=0, updated_at=? WHERE id=?
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

    def mark_manual(self, record_id: int) -> None:
        with self.connection:
            self.connection.execute("""UPDATE recordings SET source='manual',
                hidden=0, deferred=0, status=CASE WHEN status=? THEN ? ELSE status END,
                updated_at=? WHERE id=?""", (Status.SKIPPED, Status.WAITING, now(), record_id))

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

    def recover(self, uploads: bool = True) -> None:
        with self.connection:
            self.connection.execute("""
                UPDATE recordings SET vmix_state='interrupted', error=?
                WHERE source='vmix' AND vmix_state='recording'
            """, ("App restarted during a recording; recording completion is unconfirmed.",))
            if uploads:
                self.connection.execute("""
                    UPDATE recordings SET upload_ambiguous=(status=?), status=?, approved=0, error=?, updated_at=?
                    WHERE status IN (?, ?)
                """, (Status.UPLOADING, Status.FAILED, "Interrupted operation: do not automatically retry; check YouTube Studio first.",
                      now(), Status.APPROVED, Status.UPLOADING))
                self.connection.execute("""UPDATE upload_attempts SET ambiguous=(status='uploading'),
                    status='failed', error='Interrupted operation', updated_at=?
                    WHERE status IN ('queued', 'uploading')""", (now(),))
            self.connection.execute("""
                UPDATE recordings SET status=?, completed_at=NULL, updated_at=?
                WHERE status IN (?, ?)
            """, (Status.WAITING, now(), Status.READY, Status.DETECTING))
            self.connection.execute("UPDATE recordings SET available=0")

    def begin_vmix(self, path: str = "") -> int:
        # Reattach only to an interrupted recording explicitly named by vMix.
        if path:
            for record in self.all(include_hidden=True):
                if record.vmix_state == "interrupted" and Path(record.path) == Path(path):
                    self.set_vmix_state(record.id, "recording")
                    return record.id
        timestamp = now()
        with self.connection:
            cursor = self.connection.execute("""
                INSERT INTO recordings (path, identity, size, mtime_ns, detected_at, status,
                    title, description, created_at, updated_at, source, vmix_state, available)
                VALUES (?, ?, 0, 0, ?, ?, ?, '', ?, ?, 'vmix', 'recording', 0)
            """, (path, f"vmix:{uuid.uuid4()}", timestamp, Status.DETECTING,
                  Path(path).stem if path else "vMix recording (waiting for filename)", timestamp, timestamp))
        return cursor.lastrowid

    def set_vmix_state(self, record_id: int, state: str, error: str | None = None) -> None:
        with self.connection:
            self.connection.execute("""
                UPDATE recordings SET source='vmix', vmix_state=?, error=?, updated_at=?,
                    current_attempt=CASE WHEN ?='recording' THEN '' ELSE current_attempt END,
                    approved=0, status=CASE WHEN ?='recording' THEN ? ELSE status END
                WHERE id=?
            """, (state, error, now(), state, state, Status.WAITING, record_id))

    def observe_vmix(self, record_id: int, path: str, description: str) -> int:
        record = self.get(record_id)
        try:
            snapshot = Snapshot.read(Path(path))
        except OSError:
            with self.connection:
                self.connection.execute("""
                    UPDATE recordings SET path=?, title=CASE WHEN path='' THEN ? ELSE title END,
                        available=0, updated_at=? WHERE id=?
                """, (path, Path(path).stem, now(), record_id))
            return record_id
        existing = self.connection.execute(
            "SELECT id FROM recordings WHERE identity=?", (snapshot.identity,)
        ).fetchone()
        if existing and existing["id"] != record_id:
            # A previously scanned file must not get a second queue row.
            with self.connection:
                self.connection.execute("DELETE FROM recordings WHERE id=? AND identity LIKE 'vmix:%'", (record_id,))
            record_id = existing["id"]
            self.observe(snapshot, description)
            self.set_vmix_state(record_id, record.vmix_state)
        else:
            with self.connection:
                self.connection.execute("""
                    UPDATE recordings SET identity=?, path=?, size=?, mtime_ns=?, available=1,
                        title=CASE WHEN identity LIKE 'vmix:%' THEN ? ELSE title END,
                        description=CASE WHEN identity LIKE 'vmix:%' THEN ? ELSE description END,
                        status=?, approved=0, fingerprint=NULL, completed_at=NULL, current_attempt='', upload_ambiguous=0,
                        hidden=0, deferred=0, updated_at=? WHERE id=?
                """, (snapshot.identity, path, snapshot.size, snapshot.mtime_ns,
                      Path(path).stem.replace('_', ' '), description, Status.WAITING, now(), record_id))
        return record_id

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

    def approve_upload(self, record_id, version, title, description, privacy, channel_id,
                       playlist_id="", acknowledge_duplicate=False):
        import json
        from dataclasses import asdict
        from .youtube import UploadJob
        record = self.get(record_id)
        if version != (record.identity, record.size, record.mtime_ns, record.fingerprint):
            raise ValueError("The file changed. Close this window and review it again.")
        if record.status not in {Status.READY, Status.FAILED} or record.vmix_state in {"recording", "interrupted"}:
            raise ValueError("This recording is not ready for upload.")
        if not record.fingerprint or not channel_id:
            raise ValueError("A completed recording and connected channel are required.")
        if record.upload_ambiguous and not acknowledge_duplicate:
            raise ValueError("Check YouTube Studio and acknowledge the duplicate risk before retrying.")
        if not 1 <= len(title.strip()) <= 100 or any(c in title for c in "<>"):
            raise ValueError("Title must contain 1–100 characters, without < or >.")
        if len(description.encode("utf-8")) > 5000 or any(c in description for c in "<>"):
            raise ValueError("Description must be at most 5,000 UTF-8 bytes, without < or >.")
        if privacy not in {"private", "unlisted", "public"}:
            raise ValueError("Choose a valid privacy setting.")
        job = UploadJob(str(uuid.uuid4()), record.id, record.path, record.identity, record.size,
                        record.mtime_ns, record.fingerprint, title.strip(), description, privacy,
                        channel_id, playlist_id)
        with self.connection:
            self.connection.execute("""INSERT INTO upload_attempts
                (id,recording_id,job,status,created_at,updated_at) VALUES (?,?,?,'queued',?,?)""",
                (job.attempt_id, record.id, json.dumps(asdict(job)), now(), now()))
            self.connection.execute("""UPDATE recordings SET status=?, approved=1, current_attempt=?,
                title=?, description=?, privacy=?, bytes_uploaded=0, error=NULL, upload_ambiguous=0,
                available=1, updated_at=? WHERE id=?""",
                (Status.APPROVED, job.attempt_id, job.title, description, privacy, now(), record.id))
        return job

    def upload_event(self, kind: str, data: dict) -> None:
        attempt = data["attempt_id"]
        with self.connection:
            if kind == "started":
                self.connection.execute("UPDATE upload_attempts SET status='uploading', updated_at=? WHERE id=?", (now(), attempt))
                self.connection.execute("UPDATE recordings SET status=?, updated_at=? WHERE current_attempt=? AND approved=1",
                                        (Status.UPLOADING, now(), attempt))
            elif kind == "progress":
                self.connection.execute("UPDATE upload_attempts SET bytes_uploaded=?, updated_at=? WHERE id=?", (data["bytes"], now(), attempt))
                self.connection.execute("UPDATE recordings SET bytes_uploaded=? WHERE current_attempt=? AND status=?",
                                        (data["bytes"], attempt, Status.UPLOADING))
            elif kind in {"created", "done"}:
                self.connection.execute("""UPDATE upload_attempts SET status='uploaded', video_id=?, youtube_url=?,
                    error=?, ambiguous=0, updated_at=? WHERE id=?""",
                    (data["video_id"], data["url"], data.get("warning", ""), now(), attempt))
                self.connection.execute("""UPDATE recordings SET status=?, approved=0, bytes_uploaded=size,
                    youtube_id=?, youtube_url=?, error=?, upload_ambiguous=0, updated_at=? WHERE current_attempt=?""",
                    (Status.UPLOADED, data["video_id"], data["url"], data.get("warning", ""), now(), attempt))
            elif kind == "failed":
                self.connection.execute("""UPDATE upload_attempts SET status='failed', error=?, ambiguous=?, updated_at=?
                    WHERE id=? AND status<>'uploaded'""", (data["error"], int(data["ambiguous"]), now(), attempt))
                self.connection.execute("""UPDATE recordings SET status=?, approved=0, error=?, upload_ambiguous=?, updated_at=?
                    WHERE current_attempt=? AND status<>?""",
                    (Status.FAILED, data["error"], int(data["ambiguous"]), now(), attempt, Status.UPLOADED))
