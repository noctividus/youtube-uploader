"""Minimal settings, queue, and single-record review windows."""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QPushButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from .config import Settings
from .formats import EXTENSION_CHOICES
from .models import Recording, Status


def size_text(size: int) -> str:
    return f"{size / (1024 ** 2):,.1f} MiB"


def local_time(value: str) -> str:
    return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")


class SettingsWindow(QDialog):
    saved = Signal(object)

    def __init__(self, settings: Settings):
        super().__init__()
        self.setWindowTitle("Settings - vMix YouTube Uploader")
        self.resize(620, 660)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Recording folders (files directly in these folders):"))
        self.directories = QListWidget()
        self.directories.addItems(settings.directories)
        layout.addWidget(self.directories)
        buttons = QHBoxLayout()
        add = QPushButton("Add folder…")
        add.clicked.connect(self.add_folder)
        remove = QPushButton("Remove selected folder")
        remove.clicked.connect(lambda: self.directories.takeItem(self.directories.currentRow()))
        buttons.addWidget(add)
        buttons.addWidget(remove)
        layout.addLayout(buttons)
        filters = QGroupBox("File Extension Filter")
        filters.setToolTip("Choose which new files to detect. Existing queue entries are kept. Uncheck all to stop discovering new files.")
        grid = QGridLayout(filters)
        self.extension_checkboxes: dict[str, QCheckBox] = {}
        for index, extension in enumerate(EXTENSION_CHOICES):
            checkbox = QCheckBox(extension if extension != ".mkv" else ".mkv (additional)")
            checkbox.setChecked(extension in settings.file_extensions)
            if extension == ".mkv":
                checkbox.setToolTip("Retained from the original recording filter; not explicitly listed on YouTube's supported-format page.")
            self.extension_checkboxes[extension] = checkbox
            grid.addWidget(checkbox, index // 4, index % 4)
        layout.addWidget(filters)
        form = QFormLayout()
        self.stability = QSpinBox()
        self.stability.setRange(1, 86400)
        self.stability.setSuffix(" seconds")
        self.stability.setValue(settings.stability_seconds)
        form.addRow("File stability period", self.stability)
        self.periodic = QCheckBox("Scan periodically to recover missed events")
        self.periodic.setChecked(settings.periodic_scan)
        form.addRow(self.periodic)
        self.interval = QSpinBox()
        self.interval.setRange(1, 86400)
        self.interval.setSuffix(" seconds")
        self.interval.setValue(settings.scan_seconds)
        form.addRow("Scan interval", self.interval)
        self.notifications = QCheckBox("Show tray notifications")
        self.notifications.setChecked(settings.notifications)
        form.addRow(self.notifications)
        self.description = QTextEdit(settings.description)
        self.description.setAcceptRichText(False)
        self.description.setMaximumHeight(95)
        form.addRow("Default description", self.description)
        layout.addLayout(form)
        note = QLabel("YouTube uploads and vMix connection settings will arrive in later milestones.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose recording folder")
        if path and not self.directories.findItems(path, Qt.MatchFlag.MatchFixedString):
            self.directories.addItem(str(Path(path)))

    def save(self) -> None:
        settings = Settings(
            directories=[self.directories.item(i).text() for i in range(self.directories.count())],
            stability_seconds=self.stability.value(), scan_seconds=self.interval.value(),
            periodic_scan=self.periodic.isChecked(), notifications=self.notifications.isChecked(),
            description=self.description.toPlainText(),
            file_extensions=[extension for extension, checkbox in self.extension_checkboxes.items()
                             if checkbox.isChecked()],
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        self.saved.emit(settings)

    def save_failed(self) -> None:
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(True)


class QueueWindow(QWidget):
    review_requested = Signal(int)
    remove_requested = Signal(int)
    scan_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Upload Queue - vMix YouTube Uploader")
        self.resize(1000, 440)
        self.records: dict[int, Recording] = {}
        layout = QVBoxLayout(self)
        self.summary = QLabel("Starting monitoring…")
        layout.addWidget(self.summary)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #ac2424")
        layout.addWidget(self.error)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Filename", "Date detected", "Size", "Status", "Progress", "YouTube"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.itemDoubleClicked.connect(lambda _: self.review())
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        self.review_button = QPushButton("Review")
        self.review_button.clicked.connect(self.review)
        actions.addWidget(self.review_button)
        self.remove_button = QPushButton("Remove from queue")
        self.remove_button.clicked.connect(self.remove)
        actions.addWidget(self.remove_button)
        location = QPushButton("Open folder")
        location.clicked.connect(self.open_folder)
        actions.addWidget(location)
        scan = QPushButton("Scan now")
        scan.clicked.connect(self.scan_requested.emit)
        actions.addWidget(scan)
        actions.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        actions.addWidget(close)
        layout.addLayout(actions)
        layout.addWidget(QLabel("Local review milestone • Uploads are not enabled yet • Closing this window keeps monitoring active"))
        self.table.itemSelectionChanged.connect(self.update_actions)
        self.update_actions()

    def selected(self) -> Recording | None:
        row = self.table.currentRow()
        if row < 0 or not self.table.item(row, 0):
            return None
        return self.records.get(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole))

    def update_actions(self) -> None:
        record = self.selected()
        self.review_button.setEnabled(bool(record and record.available and record.status == Status.READY))
        self.remove_button.setEnabled(bool(record and record.status not in {Status.UPLOADING, Status.UPLOADED}))

    def update_records(self, records: list[Recording]) -> None:
        selected = self.selected()
        self.records = {record.id: record for record in records}
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            status = str(record.status)
            if not record.available:
                status += " (file unavailable)"
            elif record.deferred and record.status == Status.READY:
                status += " (Later)"
            columns = [Path(record.path).name, local_time(record.detected_at), size_text(record.size),
                       status, "—", record.youtube_url or "—"]
            for column, text in enumerate(columns):
                item = QTableWidgetItem(text)
                item.setToolTip(record.path if not record.error else f"{record.path}\n{record.error}")
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, record.id)
                self.table.setItem(row, column, item)
            if selected and record.id == selected.id:
                self.table.selectRow(row)
        self.update_actions()

    def review(self) -> None:
        record = self.selected()
        if record and record.available and record.status == Status.READY:
            self.review_requested.emit(record.id)

    def remove(self) -> None:
        record = self.selected()
        if record:
            self.remove_requested.emit(record.id)

    def open_folder(self) -> None:
        record = self.selected()
        if record:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(record.path).parent)))


class ReviewWindow(QDialog):
    action_requested = Signal(object)

    def __init__(self, record: Recording):
        super().__init__()
        self.record = record
        self.setWindowTitle("Review recording")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.resize(600, 430)
        form = QFormLayout(self)
        filename = QLabel(Path(record.path).name)
        filename.setTextFormat(Qt.TextFormat.PlainText)
        form.addRow("Video", filename)
        path = QLineEdit(record.path)
        path.setReadOnly(True)
        form.addRow("Full path", path)
        form.addRow("File size", QLabel(size_text(record.size)))
        recorded = datetime.fromtimestamp(record.mtime_ns / 1_000_000_000).astimezone()
        form.addRow("Last modified", QLabel(recorded.strftime("%Y-%m-%d %H:%M:%S")))
        self.title = QLineEdit(record.title)
        form.addRow("Title", self.title)
        self.description = QTextEdit(record.description)
        self.description.setAcceptRichText(False)
        form.addRow("Description", self.description)
        self.privacy = QComboBox()
        self.privacy.addItems(["Private", "Unlisted", "Public"])
        self.privacy.setCurrentText(record.privacy.title())
        form.addRow("Privacy", self.privacy)
        self.notice = QLabel("Uploads will be available in the next milestone. Later saves this review for then.")
        self.notice.setWordWrap(True)
        form.addRow(self.notice)
        buttons = QHBoxLayout()
        upload = QPushButton("Upload (coming next)")
        upload.setEnabled(False)
        buttons.addWidget(upload)
        self.later = QPushButton("Later")
        self.later.clicked.connect(lambda: self.submit("later"))
        buttons.addWidget(self.later)
        self.skip = QPushButton("Skip")
        self.skip.clicked.connect(lambda: self.submit("skip"))
        buttons.addWidget(self.skip)
        form.addRow(buttons)

    def submit(self, action: str) -> None:
        if not self.title.text().strip():
            self.notice.setText("Enter a title before saving.")
            return
        self.later.setEnabled(False)
        self.skip.setEnabled(False)
        record = self.record
        self.action_requested.emit((record.id, (record.identity, record.size, record.mtime_ns, record.fingerprint),
                                    action, self.title.text(), self.description.toPlainText(),
                                    self.privacy.currentText().lower()))

    def failed(self, message: str) -> None:
        self.notice.setText(message)
        self.later.setEnabled(True)
        self.skip.setEnabled(True)
