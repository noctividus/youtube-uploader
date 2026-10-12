"""Synchronous domain service, run exclusively by the background worker."""

import logging
import os
from collections.abc import Callable
from pathlib import Path

from .config import Settings
from .database import Database
from .files import CompletionTracker
from .models import Snapshot, Status

log = logging.getLogger(__name__)


class Monitor:
    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings
        self.tracker = CompletionTracker(settings.stability_seconds)
        self.errors: list[str] = []

    def configure(self, settings: Settings) -> None:
        self.settings = settings
        self.tracker = CompletionTracker(settings.stability_seconds)
        self.errors = []

    def tick(self, clock: float, scan: bool = False,
             stopped: Callable[[], bool] = lambda: False) -> list[int]:
        records = self.database.all(include_hidden=True)
        paths = {record.path for record in records}
        errors = []
        if scan:
            # One disconnected folder must not prevent scanning the others.
            for directory in self.settings.directories:
                if stopped():
                    return []
                try:
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            if stopped():
                                return []
                            if Path(entry.name).suffix.lower() in self.settings.file_extensions and entry.is_file():
                                paths.add(entry.path)
                except OSError as exc:
                    log.warning("Cannot scan %s: %s", directory, exc)
                    errors.append(f"{directory}: {exc}")
        seen: set[str] = set()
        ready = []
        # Check existing records even with periodic discovery disabled.
        for path in sorted(paths):
            if stopped():
                return ready
            try:
                snapshot = Snapshot.read(Path(path))
                seen.add(snapshot.identity)
                record = self.database.observe(snapshot, self.settings.description)
                if record.status in {Status.WAITING, Status.DETECTING}:
                    fingerprint = self.tracker.check(snapshot, clock)
                    if fingerprint:
                        self.database.ready(record.id, fingerprint)
                        ready.append(record.id)
                elif record.status == Status.READY and not self.tracker.access(Path(path)):
                    self.database.waiting(record.id)
                    self.tracker.forget(record.identity)
            except OSError:
                log.debug("File unavailable: %s", path, exc_info=True)
        for record in self.database.all(include_hidden=True):
            available = record.identity in seen
            self.database.available(record.id, available)
            if not available:
                self.database.waiting(record.id)
                self.tracker.forget(record.identity)
        if scan:
            self.errors = errors
        return ready
