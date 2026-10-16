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
from .manual import ManualQueue
from .vmix import VmixClient, VmixMonitor
from .youtube_service import YouTubeService
from .webhooks import Webhooks

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
    youtube_state = Signal(object)
    upload_progress = Signal(object)
    manual_added = Signal(int)
    upload_outcome = Signal(object)
    webhook_status = Signal(str)

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
        self.youtube_account = {}
        self.youtube_busy = False
        self.webhooks = None
        self.settings = Settings()
        self.vmix_failed = False
        self.vmix_announced = set()
        self.filesystem_announced = set()

    def _webhook(self, event, record=None, **details):
        if not self.webhooks or not self.settings.webhooks:
            return
        data = dict(details)
        if record:
            data["recording"] = {"id": record.id, "filename": Path(record.path).name,
                                 "size": record.size, "title": record.title, "source": record.source}
        try:
            self.webhooks.enqueue(self.settings.webhooks, event, data)
        except Exception:
            # Delivery errors must never recursively generate Error webhooks.
            self.webhook_status.emit("Could not save webhook delivery. Check local storage and header settings.")

    def _youtube_event(self, database, kind, data):
        if kind == "operation_error":
            self._webhook("error", **data)
        elif kind == "account":
            self.youtube_account = data
            self.youtube_busy = data.get("busy", False)
            self.youtube_state.emit(data)
        else:
            database.upload_event(kind, data)
            if kind == "progress":
                self.upload_progress.emit(data)
            elif kind in {"done", "failed"}:
                self.upload_outcome.emit(data)
            if kind == "created":
                self._webhook("upload_finished", database.get(data["recording_id"]),
                              attempt_id=data["attempt_id"], youtube_id=data["video_id"], youtube_url=data["url"])
            elif kind == "failed":
                self._webhook("error", database.get(data["recording_id"]),
                              operation="upload", attempt_id=data["attempt_id"], message=data["error"],
                              ambiguous=data["ambiguous"])

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
        youtube = None
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            database = Database(self.data_dir / "queue.sqlite3")
            database.recover()
            youtube = YouTubeService(self.data_dir, lambda kind, data: self.submit("youtube_event", (kind, data)))
            youtube.start()
            try:
                settings = load_settings(self.data_dir / "settings.json")
            except (OSError, ValueError) as exc:
                self.error.emit(str(exc) + " Defaults are in use; the existing file is preserved until Save.")
                settings = Settings()
            self.settings = settings
            try:
                self.webhooks = Webhooks(self.data_dir / "webhooks.sqlite3", self.webhook_status.emit)
                self.webhooks.start()
            except Exception:
                self.webhooks = None
                self.webhook_status.emit("Webhook outbox unavailable. Check local storage and restart to retry.")
            self.settings_changed.emit(settings)
            monitor = Monitor(database, settings)
            manual = ManualQueue(database)
            next_manual_check = 0.0
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
                        if command == "youtube_event":
                            self._youtube_event(database, *payload)
                        elif command == "add_video":
                            record_id = manual.add(payload, settings.description)
                            monitor.auto_review_ids.discard(record_id)
                            self.filesystem_announced.discard(record_id)
                            self.records_changed.emit(database.all())
                            self.manual_added.emit(record_id)
                        elif command == "youtube":
                            if self.youtube_busy:
                                raise ValueError("Wait for the current YouTube operation to finish.")
                            self.youtube_busy = True
                            youtube.submit(*payload)
                        elif command == "upload":
                            if self.youtube_busy:
                                raise ValueError("Wait for the current YouTube operation to finish.")
                            record_id, version, title, description, privacy, channel_id, playlist_id, acknowledge = payload
                            if not self.youtube_account.get("connected") or channel_id != self.youtube_account.get("channel_id"):
                                raise ValueError("Connect the intended YouTube channel in Settings, then review again.")
                            record = database.get(record_id)
                            if not revalidate(record.path, version[:3], version[3]):
                                raise ValueError("The file changed or is busy. Close this window and review again.")
                            job = database.approve_upload(record_id, version, title, description, privacy,
                                                          channel_id, playlist_id, acknowledge)
                            database.upload_event("started", {"attempt_id": job.attempt_id})
                            self.youtube_busy = True
                            youtube.submit("upload", job)
                            self._webhook("upload_started", database.get(record.id), attempt_id=job.attempt_id,
                                          channel_id=job.channel_id, privacy=job.privacy)
                            self.reviewed.emit(record_id)
                        elif command == "settings":
                            payload.validate()
                            save_settings(self.data_dir / "settings.json", payload)
                            if (settings.vmix_enabled, settings.vmix_url) != (payload.vmix_enabled, payload.vmix_url):
                                vmix.disconnect()
                                vmix = VmixMonitor(database, payload)
                            vmix.settings = payload
                            next_poll = 0.0
                            settings = payload
                            self.settings = settings
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
                                    database.recover(uploads=False)
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
                        self._webhook("error", operation=command, message="Application operation failed; check the app.")
                clock = time.monotonic()
                if clock >= next_manual_check:
                    manual.refresh()
                    next_manual_check = clock + 1
                # A long scheduling gap (e.g. sleep) is not observed stability.
                if clock - last_tick > max(10, settings.vmix_poll_seconds * 2):
                    monitor.tracker.reset()
                    if settings.vmix_enabled:
                        vmix.disconnect()
                        next_poll = 0.0
                    else:
                        database.recover(uploads=False)
                    self.dirty.set()
                last_tick = clock
                if not self.paused:
                    if settings.vmix_enabled:
                        if clock >= next_poll:
                            try:
                                state = client.fetch(settings.vmix_url)
                                was_recording = bool(vmix.previous and vmix.previous.recording)
                                stopped_ids = vmix.update(state)
                                if state.recording and not was_recording and vmix.current_id not in self.vmix_announced:
                                    self._webhook("recording_started", database.get(vmix.current_id),
                                                  observed=True)
                                    self.vmix_announced.add(vmix.current_id)
                                self.vmix_failed = False
                                vmix.check_completed()
                                for record_id in stopped_ids:
                                    self.recording_stopped.emit(record_id)
                                    self._webhook("recording_finished", database.get(record_id))
                                    self.vmix_announced.discard(record_id)
                                streaming = 'unknown' if state.streaming is None else 'yes' if state.streaming else 'no'
                                vmix_connection = f"Connected — {vmix.message}; Streaming: {streaming}"
                            except (requests.RequestException, ValueError) as exc:
                                vmix.disconnect()
                                detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                                vmix_connection = f"Disconnected — {detail}; retrying"
                                if not self.vmix_failed:
                                    self._webhook("error", operation="vmix", message="vMix connection failed; retrying.")
                                self.vmix_failed = True
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
            self._webhook("error", operation="monitor", message="Monitoring stopped; check the app.")
        finally:
            if youtube:
                youtube.stopping.set()
                youtube.join()
                # Persist final success/failure even when Exit was requested mid-upload.
                while not self.commands.empty():
                    command, payload = self.commands.get_nowait()
                    if command == "youtube_event" and database:
                        self._youtube_event(database, *payload)
            self._stop_observer()
            if self.webhooks:
                self.webhooks.stop()
                self.webhooks.join()
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
        for record_id in monitor.auto_review_ids - self.filesystem_announced:
            self._webhook("recording_started", database.get(record_id), evidence="new_file_observed")
            self.filesystem_announced.add(record_id)
        for record_id in ready_ids:
            if record_id in monitor.auto_review_ids and not database.get(record_id).deferred:
                self.ready.emit(record_id)
                self._webhook("recording_finished", database.get(record_id), evidence="file_stability")
            monitor.auto_review_ids.discard(record_id)
            self.filesystem_announced.discard(record_id)
        self.status.emit("Error: folder unavailable" if monitor.errors else "Idle")
        if monitor.errors != self.previous_errors:
            if monitor.errors:
                self._webhook("error", operation="filesystem", message="A watched folder is unavailable.")
            self.error.emit("\n".join(monitor.errors))
            self.previous_errors = monitor.errors.copy()
        return last_scan
