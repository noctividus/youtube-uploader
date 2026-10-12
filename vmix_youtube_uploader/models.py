"""Domain types shared by the monitor, persistence, and UI."""

import os
import stat as stat_module
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

class Status(StrEnum):
    DETECTING = "Detecting"
    WAITING = "Waiting for File Completion"
    READY = "Ready for Review"
    APPROVED = "Approved / Queued"
    UPLOADING = "Uploading"
    UPLOADED = "Uploaded"
    FAILED = "Failed"
    SKIPPED = "Skipped"


@dataclass(frozen=True)
class Snapshot:
    path: str
    identity: str
    size: int
    mtime_ns: int

    @classmethod
    def read(cls, path: Path) -> "Snapshot":
        path = path.absolute()
        stat = path.stat()
        if not stat_module.S_ISREG(stat.st_mode):
            raise OSError(f"Not a regular file: {path}")
        # Birth time distinguishes reused NTFS file IDs. Path is only a fallback
        # for filesystems that do not expose an inode/file ID.
        identity = (
            f"{stat.st_dev}:{stat.st_ino}:{getattr(stat, 'st_birthtime_ns', 0)}"
            if stat.st_ino else f"path:{os.path.normcase(str(path))}"
        )
        return cls(str(path), identity, stat.st_size, stat.st_mtime_ns)

    @property
    def version(self) -> tuple[str, int, int]:
        return self.identity, self.size, self.mtime_ns


@dataclass(frozen=True)
class Recording:
    id: int
    path: str
    identity: str
    size: int
    mtime_ns: int
    detected_at: str
    completed_at: str | None
    status: Status
    title: str
    description: str
    privacy: str
    deferred: bool
    hidden: bool
    available: bool
    approved: bool
    bytes_uploaded: int
    youtube_id: str | None
    youtube_url: str | None
    error: str | None
    retry_count: int
    fingerprint: str | None
    created_at: str
    updated_at: str
