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
        self.baselined_scopes: set[tuple[str, str]] = set()
        self.observed_versions: dict[str, tuple[str, int, int]] = {}
        self.auto_review_ids: set[int] = set()

    def watched_scopes(self) -> set[tuple[str, str]]:
        return {(os.path.normcase(os.path.abspath(directory)), extension)
                for directory in self.settings.directories
                for extension in self.settings.file_extensions}

    @staticmethod
    def scope(path: str) -> tuple[str, str]:
        return os.path.normcase(str(Path(path).absolute().parent)), Path(path).suffix.lower()

    def configure(self, settings: Settings) -> None:
        self.settings = settings
        self.tracker = CompletionTracker(settings.stability_seconds)
        self.errors = []
        scopes = self.watched_scopes()
        self.baselined_scopes.intersection_update(scopes)
        self.auto_review_ids.intersection_update(
            record.id for record in self.database.all() if self.scope(record.path) in scopes
        )

    def tick(self, clock: float, scan: bool = False,
             stopped: Callable[[], bool] = lambda: False) -> list[int]:
        records = self.database.all(include_hidden=True)
        paths = {record.path for record in records if record.source != "manual"}
        manual_paths = {record.path for record in records if record.source == "manual"}
        errors = []
        watched_scopes = self.watched_scopes()
        scanned_scopes: set[tuple[str, str]] = set()
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
                    scanned_scopes.update((os.path.normcase(os.path.abspath(directory)), extension)
                                          for extension in self.settings.file_extensions)
                except OSError as exc:
                    log.warning("Cannot scan %s: %s", directory, exc)
                    errors.append(f"{directory}: {exc}")
        seen: set[str] = set()
        ready = []
        # Check existing records even with periodic discovery disabled.
        for path in sorted(paths):
            if path in manual_paths:
                continue
            if stopped():
                return ready
            try:
                snapshot = Snapshot.read(Path(path))
                seen.add(snapshot.identity)
                record = self.database.observe(snapshot, self.settings.description)
                if record.source == "manual":
                    continue
                scope = self.scope(snapshot.path)
                previous = self.observed_versions.get(snapshot.identity)
                if (scope in watched_scopes and scope in self.baselined_scopes
                        and previous != snapshot.version):
                    self.auto_review_ids.add(record.id)
                self.observed_versions[snapshot.identity] = snapshot.version
                if record.status in {Status.WAITING, Status.DETECTING}:
                    fingerprint = self.tracker.check(snapshot, clock)
                    if fingerprint:
                        if record.vmix_state == "interrupted":
                            # Explicitly choosing filesystem mode uses its own
                            # completion evidence, not a fabricated vMix stop.
                            self.database.set_vmix_state(record.id, "")
                        self.database.ready(record.id, fingerprint)
                        ready.append(record.id)
                elif record.status == Status.READY and not self.tracker.access(Path(path)):
                    self.database.waiting(record.id)
                    self.tracker.forget(record.identity)
            except OSError:
                log.debug("File unavailable: %s", path, exc_info=True)
        for record in self.database.all(include_hidden=True):
            if record.source == "manual":
                continue
            available = record.identity in seen
            self.database.available(record.id, available)
            if not available:
                self.database.waiting(record.id)
                self.tracker.forget(record.identity)
        if scan:
            self.errors = errors
            self.baselined_scopes.update(scanned_scopes)
        return ready
