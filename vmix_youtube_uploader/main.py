"""Windows tray entry point; no file scanning or database calls on the GUI thread."""

import argparse
import ctypes
import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QStandardPaths, QTimer, QUrl, Slot
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from .config import Settings
from .logging_setup import configure_logging
from .models import Recording, Status
from .ui import QueueWindow, ReviewWindow, SettingsWindow
from .worker import Worker


def desktop_available() -> bool:
    if os.name != "nt":
        return True
    from ctypes import wintypes

    user = ctypes.WinDLL("user32", use_last_error=True)
    user.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user.OpenInputDesktop.restype = wintypes.HANDLE
    user.SwitchDesktop.argtypes = [wintypes.HANDLE]
    user.SwitchDesktop.restype = wintypes.BOOL
    user.CloseDesktop.argtypes = [wintypes.HANDLE]
    user.CloseDesktop.restype = wintypes.BOOL
    desktop = user.OpenInputDesktop(0, False, 0x0100)
    if not desktop:
        return False
    try:
        return bool(user.SwitchDesktop(desktop))
    finally:
        user.CloseDesktop(desktop)


def tray_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("transparent"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#df3038"))
    painter.setBrush(QColor("#df3038"))
    painter.drawRoundedRect(3, 10, 58, 44, 10, 10)
    painter.setPen(QColor("white"))
    font = painter.font()
    font.setPixelSize(34)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(18, 44, "↑")
    painter.end()
    return QIcon(pixmap)


class Controller(QObject):
    def __init__(self, app: QApplication, data_dir: Path):
        super().__init__()
        self.app = app
        self.data_dir = data_dir
        self.settings = Settings()
        self.loaded = False
        self.records: dict[int, Recording] = {}
        self.pending: set[int] = set()
        self.vmix_pending: set[int] = set()
        self.vmix_status_text = "Waiting for vMix"
        self.presented: set[int] = set()
        self.review_window = None
        self.settings_window = None
        self.closing = False
        self.worker = Worker(data_dir)
        self.queue_window = QueueWindow()
        self.queue_window.review_requested.connect(self.show_review)
        self.queue_window.settings_requested.connect(self.show_settings)
        self.queue_window.settings_button.setEnabled(False)
        self.queue_window.scan_requested.connect(lambda: self.worker.submit("scan"))
        self.tray = QSystemTrayIcon(tray_icon(), self)
        self.menu = QMenu()
        self.status_action = self.menu.addAction("Starting…")
        self.status_action.setEnabled(False)
        self.menu.addSeparator()
        self.menu.addAction("Open Upload Queue", self.show_queue)
        self.settings_action = self.menu.addAction("Settings", self.show_settings)
        self.settings_action.setEnabled(False)
        self.pause_action = self.menu.addAction("Pause Monitoring")
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(self.pause)
        self.menu.addAction("View Logs", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(data_dir / "logs" / "uploader.log"))))
        self.menu.addSeparator()
        self.menu.addAction("Exit", self.shutdown)
        self.tray.setContextMenu(self.menu)
        self.tray.setToolTip("vMix YouTube Uploader")
        self.tray.activated.connect(self.activated)
        self.tray.messageClicked.connect(self.show_queue)
        self.worker.records_changed.connect(self.records_changed)
        self.worker.settings_changed.connect(self.settings_changed)
        self.worker.ready.connect(self.record_ready)
        self.worker.recording_stopped.connect(self.vmix_recording_stopped)
        self.worker.vmix_status.connect(self.vmix_status_changed)
        self.worker.vmix_test_result.connect(self.vmix_test_result)
        self.worker.error.connect(self.show_error)
        self.worker.status.connect(self.status_changed)
        self.worker.reviewed.connect(self.review_saved)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.present_next)
        self.timer.start()
        self.tray.show()
        self.worker.start()

    @Slot(str)
    def status_changed(self, status: str) -> None:
        self.status_action.setText(status)
        self.queue_window.summary.setText(f"{status} · {len(self.records)} recordings")

    @Slot(int)
    def record_ready(self, record_id: int) -> None:
        self.presented.discard(record_id)
        self.pending.add(record_id)

    @Slot(int)
    def vmix_recording_stopped(self, record_id: int) -> None:
        self.record_ready(record_id)
        self.vmix_pending.add(record_id)

    @Slot(str)
    def vmix_status_changed(self, message: str) -> None:
        self.vmix_status_text = message
        if self.settings_window:
            self.settings_window.vmix_connection.setText(message)

    @Slot(str)
    def vmix_test_result(self, message: str) -> None:
        if self.settings_window:
            self.settings_window.show_vmix_test_result(message)

    def pause(self, paused: bool) -> None:
        self.pause_action.setText("Resume Monitoring" if paused else "Pause Monitoring")
        self.worker.submit("pause", paused)

    def activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_queue()

    def show_queue(self) -> None:
        self.queue_window.show()
        self.queue_window.raise_()
        self.queue_window.activateWindow()

    @Slot(object)
    def records_changed(self, records: list[Recording]) -> None:
        self.records = {record.id: record for record in records}
        self.queue_window.update_records(records)
        if self.review_window and self.review_window.isVisible():
            current = self.records.get(self.review_window.record.id)
            if current:
                self.review_window.update_record(current)
        if self.vmix_pending:
            self.present_next()

    @Slot(object)
    def settings_changed(self, settings: Settings) -> None:
        first = not self.loaded
        self.loaded = True
        self.settings = settings
        self.settings_action.setEnabled(True)
        self.queue_window.settings_button.setEnabled(True)
        if self.settings_window and self.settings_window.isVisible():
            self.settings_window.accept()
        if first and not settings.vmix_enabled and not settings.directories and settings.notifications:
            self.tray.showMessage("Ready to configure", "Open Settings from the tray menu to choose a recording folder.")

    def show_settings(self) -> None:
        if self.settings_window and self.settings_window.isVisible():
            self.settings_window.raise_()
            self.settings_window.activateWindow()
            return
        self.settings_window = SettingsWindow(self.settings)
        self.settings_window.vmix_connection.setText(self.vmix_status_text)
        self.settings_window.vmix_test_requested.connect(lambda url: self.worker.submit("vmix_test", url))
        self.settings_window.saved.connect(lambda settings: self.worker.submit("settings", settings))
        self.settings_window.show()

    @Slot(str)
    def show_error(self, message: str) -> None:
        self.queue_window.error.setText(message)
        if not message:
            return
        if self.settings_window:
            self.settings_window.save_failed()
        if self.review_window:
            self.review_window.failed(message)
        if self.settings.notifications:
            self.tray.showMessage("Uploader needs attention", message, QSystemTrayIcon.MessageIcon.Warning)

    def present_next(self) -> None:
        if self.closing or self.pause_action.isChecked() or not desktop_available():
            return
        if self.review_window and self.review_window.isVisible():
            return
        for record_id in sorted(self.pending):
            record = self.records.get(record_id)
            vmix_stopped = record_id in self.vmix_pending and record is not None and record.vmix_state == "stopped"
            if record is None or (not vmix_stopped and (record.status != Status.READY or not record.available)):
                continue
            self.pending.discard(record_id)
            self.vmix_pending.discard(record_id)
            watched_directories = {os.path.normcase(os.path.abspath(path)) for path in self.settings.directories}
            if not vmix_stopped and (os.path.normcase(str(Path(record.path).parent)) not in watched_directories
                    or Path(record.path).suffix.lower() not in self.settings.file_extensions):
                continue
            if record.deferred or record_id in self.presented:
                continue
            if self.settings.notifications:
                self.tray.showMessage("Recording ready for review", Path(record.path).name)
            self.show_review(record_id, automatic=True)
            break

    @Slot(int)
    def show_review(self, record_id: int, automatic: bool = False) -> None:
        record = self.records.get(record_id)
        if not record or (automatic and record.vmix_state != "stopped" and (record.status != Status.READY or not record.available)):
            return
        if self.review_window and self.review_window.isVisible():
            if automatic:
                return
            if self.review_window.record.id == record_id:
                self.review_window.raise_()
                self.review_window.activateWindow()
                return
            self.review_window.reject()
        self.presented.add(record_id)
        self.pending.discard(record_id)
        self.vmix_pending.discard(record_id)
        self.review_window = ReviewWindow(record)
        self.review_window.action_requested.connect(lambda payload: self.worker.submit("review", payload))
        self.review_window.show()
        if not automatic:
            self.review_window.activateWindow()

    @Slot(int)
    def review_saved(self, record_id: int) -> None:
        if self.review_window and self.review_window.record.id == record_id:
            self.review_window.accept()

    def shutdown(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.timer.stop()
        self.menu.setEnabled(False)
        self.status_changed("Shutting down…")
        self.worker.stop()
        # Keep the event loop responsive while the worker closes its observer/DB.
        self.shutdown_timer = QTimer(self)
        self.shutdown_timer.setInterval(100)
        self.shutdown_timer.timeout.connect(self.finish_shutdown)
        self.shutdown_timer.start()

    def finish_shutdown(self) -> None:
        if not self.worker.isRunning():
            self.tray.hide()
            self.app.quit()

    def cleanup(self) -> None:
        self.worker.stop()
        self.worker.wait()
        self.tray.hide()


def main() -> int:
    parser = argparse.ArgumentParser(description="vMix YouTube Uploader local recording review")
    parser.add_argument("--data-dir", type=Path, help="Override per-user state directory (for isolated testing)")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("vMix YouTube Uploader")
    app.setOrganizationName("vMix YouTube Uploader")
    app.setQuitOnLastWindowClosed(False)
    data_dir = args.data_dir or Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    data_dir = data_dir.absolute()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(data_dir / "instance.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            QMessageBox.information(None, "vMix YouTube Uploader", "The app is already running, or its data folder cannot be locked. Check the system tray.")
            return 1
        configure_logging(data_dir / "logs")
        if not QSystemTrayIcon.isSystemTrayAvailable():
            QMessageBox.critical(None, "System tray unavailable", "A Windows desktop with a system tray is required.")
            return 1
        controller = Controller(app, data_dir)
        app.aboutToQuit.connect(controller.cleanup)
        logging.getLogger(__name__).info("Application started")
        return app.exec()
    finally:
        if "lock" in locals():
            lock.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
