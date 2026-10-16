"""Explicit file selection, checked on the worker without a recording trigger."""
from pathlib import Path

from .database import Database
from .files import exclusive_access, sampled_fingerprint
from .formats import EXTENSION_CHOICES
from .models import Snapshot, Status


class ManualQueue:
    def __init__(self, database: Database):
        self.database = database

    def add(self, filename: str, description: str) -> int:
        path = Path(filename)
        if path.suffix.lower() not in EXTENSION_CHOICES:
            raise ValueError("Choose a supported video file.")
        snapshot = Snapshot.read(path)
        if snapshot.size <= 0 or not exclusive_access(path):
            raise ValueError("The video is empty or in use. Wait until recording has finished.")
        fingerprint = sampled_fingerprint(path, snapshot.size)
        if Snapshot.read(path).version != snapshot.version:
            raise ValueError("The file changed while being added. Wait until recording has finished.")
        existing = next((r for r in self.database.all(include_hidden=True) if r.identity == snapshot.identity), None)
        if existing and (existing.vmix_state in {"recording", "interrupted"}
                         or existing.status in {Status.APPROVED, Status.UPLOADING}):
            raise ValueError("This video is already recording or queued for upload. Use its existing queue entry.")
        if existing and existing.source == "vmix":
            return existing.id  # Keep the existing vMix lifecycle and its completion checks.
        record = self.database.observe(snapshot, description)
        # Explicit re-add reveals a hidden/skipped item, but preserves upload
        # history and ambiguous failures so duplicate acknowledgment still applies.
        self.database.mark_manual(record.id)
        self.database.ready(record.id, fingerprint)
        return record.id

    def refresh(self) -> None:
        for record in self.database.all():
            if record.source != "manual" or record.status in {Status.APPROVED, Status.UPLOADING}:
                continue
            path = Path(record.path)
            try:
                snapshot = Snapshot.read(path)
                record = self.database.observe(snapshot, record.description)
                if record.status not in {Status.WAITING, Status.DETECTING, Status.READY}:
                    continue
                if snapshot.size <= 0 or not exclusive_access(path):
                    self.database.waiting(record.id)
                    continue
                if record.status == Status.READY:
                    continue
                fingerprint = sampled_fingerprint(path, snapshot.size)
                if Snapshot.read(path).version == snapshot.version:
                    self.database.ready(record.id, fingerprint)
            except OSError:
                self.database.available(record.id, False)
                self.database.waiting(record.id)
