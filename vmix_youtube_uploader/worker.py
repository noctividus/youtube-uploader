"""One worker owns filesystem checks, config writes, and the SQLite connection."""

import logging
import queue
import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
import requests

from .config import Settings, load_settings, save_settings
from .database import Database
from .files import revalidate
from .monitor import Monitor
from .vmix import VmixClient, VmixMonitor

log = logging.getLogger(__name__)


class ChangeHandler(FileSystemEventHandler):
    def __init__(self, dirty: threading.Event):
        self.dirty = dirty

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type in {"created", "modified", "moved", "deleted", "closed"}:
            self.dirty.set()


class Worker(QThread):
    records_changed = Signal(object)
    settings_changed = Signal(object)
    ready = Signal(int)
    error = Signal(str)
    status = Signal(str)
    reviewed = Signal(int)
    vmix_status = Signal(str)
    vmix_test_result = Signal(str)
    recording_stopped = Signal(int)

    def __init__(self, data_dir: Path):
        super().__init__()
        self.data_dir = data_dir
        self.commands: queue.Queue[tuple[str, object]] = queue.Queue()
        self.stopping = threading.Event()
        self.wake = threading.Event()
        self.dirty = threading.Event()
        self.observer = None
        self.watched: set[str] = set()
        self.paused = False
        self.previous_errors: list[str] = []

    def submit(self, command: str, payload: object = None) -> None:
        self.commands.put((command, payload))
        self.wake.set()

    def stop(self) -> None:
        self.stopping.set()
        self.wake.set()

    def _stop_observer(self) -> None:
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer = None

    def _watch(self, settings: Settings) -> None:
        self._stop_observer()
        self.watched.clear()
        if settings.vmix_enabled:
            self.dirty.clear()
            return
        observer = Observer()
        try:
            observer.start()
            self.observer = observer
        except OSError:
            observer.stop()
            if observer.is_alive():
                observer.join()
            self.error.emit("Folder event monitoring failed. Periodic scans will still run if enabled.")
            log.exception("Cannot start directory watcher")
        self._ensure_watches(settings)
        self.dirty.set()

    def _ensure_watches(self, settings: Settings) -> None:
        if not self.observer:
            return
        for directory in dict.fromkeys(settings.directories):
            if directory in self.watched or self.stopping.is_set():
                continue
            try:
                self.observer.schedule(ChangeHandler(self.dirty), directory, recursive=False)
                self.watched.add(directory)
            except OSError:
                log.warning("Cannot watch %s; will retry on next scan", directory)

    def run(self) -> None:
        database = None
        client = None
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            database = Database(self.data_dir / "queue.sqlite3")
            database.recover()
            try:
                settings = load_settings(self.data_dir / "settings.json")
            except (OSError, ValueError) as exc:
                self.error.emit(str(exc) + " Defaults are in use; the existing file is preserved until Save.")
                settings = Settings()
            self.settings_changed.emit(settings)
            monitor = Monitor(database, settings)
            vmix = VmixMonitor(database, settings)
            client = VmixClient()
            next_poll = 0.0
            vmix_connection = "Waiting for vMix"
            self._watch(settings)
            previous_records = None
            last_scan = float("-inf")
            last_tick = time.monotonic()
            while not self.stopping.is_set():
                self.wake.clear()
                while not self.commands.empty() and not self.stopping.is_set():
                    command, payload = self.commands.get_nowait()
                    try:
                        if command == "settings":
                            payload.validate()
                            save_settings(self.data_dir / "settings.json", payload)
                            if (settings.vmix_enabled, settings.vmix_url) != (payload.vmix_enabled, payload.vmix_url):
                                vmix.disconnect()
                                vmix = VmixMonitor(database, payload)
                            vmix.settings = payload
                            next_poll = 0.0
                            settings = payload
                            monitor.configure(settings)
                            self._watch(settings)
                            self.settings_changed.emit(settings)
                        elif command == "pause":
                            self.paused = bool(payload)
                            monitor.tracker.reset()
                            vmix.disconnect()
                            next_poll = 0.0
                            if not self.paused:
                                self.dirty.set()
                                if not settings.vmix_enabled:
                                    database.recover()
                        elif command == "vmix_test":
                            try:
                                state = client.fetch(payload)
                                self.vmix_test_result.emit(
                                    f"Connected — Recording: {'Yes' if state.recording else 'No'}; "
                                    f"Streaming: {'Unknown' if state.streaming is None else 'Yes' if state.streaming else 'No'}"
                                )
                            except (requests.RequestException, ValueError) as exc:
                                detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                                self.vmix_test_result.emit(f"Connection test failed: {detail}")
                        elif command == "review":
                            record_id, version, action, title, description, privacy = payload
                            record = database.get(record_id)
                            if not revalidate(record.path, version[:3], version[3]):
                                database.waiting(record_id)
                                monitor.tracker.forget(record.identity)
                                raise ValueError("File changed or is in use. Wait for completion, then review again.")
                            database.review_action(record_id, version, action, title, description, privacy)
                            self.reviewed.emit(record_id)
                        elif command == "remove":
                            database.remove(payload)
                        elif command == "scan":
                            self.dirty.set()
                    except Exception as exc:
                        log.exception("Command failed: %s", command)
                        self.error.emit(str(exc))
                clock = time.monotonic()
                # A long scheduling gap (e.g. sleep) is not observed stability.
                if clock - last_tick > max(10, settings.vmix_poll_seconds * 2):
                    monitor.tracker.reset()
                    if settings.vmix_enabled:
                        vmix.disconnect()
                        next_poll = 0.0
                    else:
                        database.recover()
                    self.dirty.set()
                last_tick = clock
                if not self.paused:
                    if settings.vmix_enabled:
                        if clock >= next_poll:
                            try:
                                state = client.fetch(settings.vmix_url)
                                stopped_ids = vmix.update(state)
                                vmix.check_completed()
                                for record_id in stopped_ids:
                                    self.recording_stopped.emit(record_id)
                                streaming = 'unknown' if state.streaming is None else 'yes' if state.streaming else 'no'
                                vmix_connection = f"Connected — {vmix.message}; Streaming: {streaming}"
                            except (requests.RequestException, ValueError) as exc:
                                vmix.disconnect()
                                detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                                vmix_connection = f"Disconnected — {detail}; retrying"
                            next_poll = time.monotonic() + settings.vmix_poll_seconds
                        self.vmix_status.emit(vmix_connection)
                        self.status.emit(f"vMix: {vmix_connection}")
                    else:
                        self.vmix_status.emit("Disabled — using file stability trigger")
                        last_scan = self._filesystem_tick(monitor, database, settings, clock, last_scan)
                else:
                    self.status.emit("Monitoring paused")
                    self.vmix_status.emit("Monitoring paused")
                records = database.all()
                if records != previous_records:
                    self.records_changed.emit(records)
                    previous_records = records
                self.wake.wait(0.25 if settings.vmix_enabled else 1)
        except Exception as exc:
            log.exception("Monitor failed")
            self.error.emit(f"Monitoring stopped: {exc}. Exit and restart after correcting the problem.")
            self.status.emit("Error: monitoring stopped")
        finally:
            self._stop_observer()
            if client:
                client.close()
            if database:
                database.close()

    def _filesystem_tick(self, monitor: Monitor, database: Database, settings: Settings,
                         clock: float, last_scan: float) -> float:
        scan = self.dirty.is_set() or (
            settings.periodic_scan and clock - last_scan >= settings.scan_seconds
        )
        if scan:
            self.dirty.clear()
            last_scan = clock
            self._ensure_watches(settings)
        ready_ids = monitor.tick(clock, scan, self.stopping.is_set)
        for record_id in ready_ids:
            if record_id in monitor.auto_review_ids and not database.get(record_id).deferred:
                self.ready.emit(record_id)
            monitor.auto_review_ids.discard(record_id)
        self.status.emit("Error: folder unavailable" if monitor.errors else "Idle")
        if monitor.errors != self.previous_errors:
            self.error.emit("\n".join(monitor.errors))
            self.previous_errors = monitor.errors.copy()
        return last_scan
