"""Minimal settings, queue, and single-record review windows."""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QPushButton, QScrollArea, QSpinBox, QTableWidget, QTabWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from .config import Settings, WebhookEndpoint, WEBHOOK_EVENTS
from .formats import EXTENSION_CHOICES
from .models import Recording, Status


def size_text(size: int) -> str:
    return f"{size / (1024 ** 2):,.1f} MiB"


def local_time(value: str) -> str:
    return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")


class WebhookEditor(QGroupBox):
    remove_requested = Signal(object)

    def __init__(self, endpoint: WebhookEndpoint | None = None):
        super().__init__("Webhook endpoint")
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.url = QLineEdit(endpoint.url if endpoint else "")
        self.url.setPlaceholderText("https://example.com/webhook")
        self.url.setAccessibleName("Webhook endpoint URL")
        row.addWidget(QLabel("Endpoint"))
        row.addWidget(self.url)
        remove = QPushButton("Remove")
        remove.clicked.connect(lambda: self.remove_requested.emit(self))
        row.addWidget(remove)
        layout.addLayout(row)
        layout.addWidget(QLabel("Call when:"))
        grid = QGridLayout()
        self.events: dict[str, QCheckBox] = {}
        for index, (event, label) in enumerate(WEBHOOK_EVENTS.items()):
            checkbox = QCheckBox(label)
            checkbox.setChecked(bool(endpoint and event in endpoint.events))
            self.events[event] = checkbox
            grid.addWidget(checkbox, index // 2, index % 2)
        layout.addLayout(grid)

    def value(self) -> WebhookEndpoint:
        return WebhookEndpoint(self.url.text().strip(), [
            event for event, checkbox in self.events.items() if checkbox.isChecked()
        ])


class SettingsWindow(QDialog):
    saved = Signal(object)
    vmix_test_requested = Signal(str)

    def __init__(self, settings: Settings):
        super().__init__()
        self.setWindowTitle("Settings - vMix YouTube Uploader")
        self.resize(620, 660)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        files = QWidget()
        files_layout = QVBoxLayout(files)
        self.tabs.addTab(files, "Files")
        files_layout.addWidget(QLabel("Recording folders (files directly in these folders):"))
        files_mode_note = QLabel("Folder and extension settings apply only when the vMix trigger is disabled.")
        files_mode_note.setWordWrap(True)
        files_layout.addWidget(files_mode_note)
        self.directories = QListWidget()
        self.directories.addItems(settings.directories)
        files_layout.addWidget(self.directories)
        buttons = QHBoxLayout()
        add = QPushButton("Add folder…")
        add.clicked.connect(self.add_folder)
        remove = QPushButton("Remove selected folder")
        remove.clicked.connect(lambda: self.directories.takeItem(self.directories.currentRow()))
        buttons.addWidget(add)
        buttons.addWidget(remove)
        files_layout.addLayout(buttons)
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
        files_layout.addWidget(filters)
        triggers = QWidget()
        triggers_layout = QVBoxLayout(triggers)
        self.tabs.addTab(triggers, "Triggers")
        stability_group = QGroupBox("Stability Trigger")
        form = QFormLayout(stability_group)
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
        triggers_layout.addWidget(stability_group)
        vmix = QGroupBox("vMix")
        vmix_layout = QFormLayout(vmix)
        self.vmix_enabled = QCheckBox("Use vMix recording events instead of folder monitoring")
        self.vmix_enabled.setChecked(settings.vmix_enabled)
        vmix_layout.addRow(self.vmix_enabled)
        self.vmix_url = QLineEdit(settings.vmix_url)
        vmix_layout.addRow("API URL", self.vmix_url)
        self.vmix_poll = QSpinBox()
        self.vmix_poll.setRange(1, 60)
        self.vmix_poll.setSuffix(" seconds")
        self.vmix_poll.setValue(settings.vmix_poll_seconds)
        vmix_layout.addRow("Poll interval", self.vmix_poll)
        self.vmix_connection = QLabel("Waiting for connection status…")
        self.vmix_connection.setWordWrap(True)
        vmix_layout.addRow("Status", self.vmix_connection)
        self.vmix_test = QPushButton("Test connection")
        self.vmix_test.clicked.connect(self.test_vmix)
        vmix_layout.addRow(self.vmix_test)
        self.vmix_test_status = QLabel()
        self.vmix_test_status.setWordWrap(True)
        vmix_layout.addRow(self.vmix_test_status)
        vmix_note = QLabel("Recording starts add the primary recording to the queue. Recording stops open its upload window. No watched folder or stability timer is needed; the API must provide a filename accessible on this computer.")
        vmix_note.setWordWrap(True)
        vmix_layout.addRow(vmix_note)
        self.vmix_enabled.toggled.connect(lambda enabled: stability_group.setEnabled(not enabled))
        stability_group.setEnabled(not settings.vmix_enabled)
        triggers_layout.addWidget(vmix)
        triggers_layout.addStretch()

        webhooks = QWidget()
        webhooks_layout = QVBoxLayout(webhooks)
        self.tabs.addTab(webhooks, "Webhooks")
        webhook_note = QLabel("Save endpoints and choose their triggers below. Webhook delivery is not enabled yet.")
        webhook_note.setWordWrap(True)
        webhooks_layout.addWidget(webhook_note)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self.webhook_layout = QVBoxLayout(container)
        self.webhook_layout.addStretch()
        scroll.setWidget(container)
        webhooks_layout.addWidget(scroll)
        self.webhook_editors: list[WebhookEditor] = []
        for endpoint in settings.webhooks:
            self.add_webhook(endpoint)
        self.add_webhook_button = QPushButton("Add")
        self.add_webhook_button.clicked.connect(lambda: self.add_webhook())
        webhooks_layout.addWidget(self.add_webhook_button)

        general = QWidget()
        general_layout = QVBoxLayout(general)
        self.tabs.addTab(general, "General")
        form = QFormLayout()
        self.notifications = QCheckBox("Show tray notifications")
        self.notifications.setChecked(settings.notifications)
        form.addRow(self.notifications)
        self.description = QTextEdit(settings.description)
        self.description.setAcceptRichText(False)
        self.description.setMaximumHeight(95)
        form.addRow("Default description", self.description)
        general_layout.addLayout(form)
        general_layout.addStretch()
        self.validation_error = QLabel()
        self.validation_error.setWordWrap(True)
        self.validation_error.setStyleSheet("color: #ac2424")
        layout.addWidget(self.validation_error)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def add_webhook(self, endpoint: WebhookEndpoint | None = None) -> None:
        editor = WebhookEditor(endpoint)
        editor.remove_requested.connect(self.remove_webhook)
        self.webhook_editors.append(editor)
        self.webhook_layout.insertWidget(self.webhook_layout.count() - 1, editor)

    def remove_webhook(self, editor: WebhookEditor) -> None:
        self.webhook_editors.remove(editor)
        self.webhook_layout.removeWidget(editor)
        editor.deleteLater()

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
            webhooks=[editor.value() for editor in self.webhook_editors],
            vmix_enabled=self.vmix_enabled.isChecked(),
            vmix_url=self.vmix_url.text().strip(),
            vmix_poll_seconds=self.vmix_poll.value(),
        )
        try:
            settings.validate()
        except ValueError as exc:
            self.validation_error.setText(str(exc))
            if str(exc).startswith("Webhook"):
                self.tabs.setCurrentIndex(2)
            return
        self.validation_error.clear()
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        self.saved.emit(settings)

    def save_failed(self) -> None:
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(True)

    def test_vmix(self) -> None:
        self.vmix_test.setEnabled(False)
        self.vmix_test_status.setText("Testing…")
        self.vmix_test_requested.emit(self.vmix_url.text().strip())

    def show_vmix_test_result(self, message: str) -> None:
        self.vmix_test.setEnabled(True)
        self.vmix_test_status.setText(message)


class SortableItem(QTableWidgetItem):
    def __init__(self, text: str, sort_value: str | int | float):
        super().__init__(text)
        self.sort_value = sort_value

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, SortableItem):
            return self.sort_value < other.sort_value
        return super().__lt__(other)


class QueueWindow(QWidget):
    review_requested = Signal(int)
    settings_requested = Signal()
    scan_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Upload Queue - vMix YouTube Uploader")
        self.resize(1000, 440)
        self.records: dict[int, Recording] = {}
        layout = QVBoxLayout(self)
        self.summary = QLabel("Starting monitoring…")
        header = QHBoxLayout()
        header.addWidget(self.summary)
        header.addStretch()
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.settings_requested.emit)
        header.addWidget(self.settings_button)
        layout.addLayout(header)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #ac2424")
        layout.addWidget(self.error)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Filename", "Directory", "Date detected", "Size", "Status", "Progress", "YouTube"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSortingEnabled(True)
        self.table.sortItems(2, Qt.SortOrder.DescendingOrder)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        self.review_button = QPushButton("Upload")
        self.review_button.setToolTip("Open upload confirmation for the selected file.")
        self.review_button.clicked.connect(self.review)
        actions.addWidget(self.review_button)
        actions.addStretch()
        layout.addLayout(actions)
        layout.addWidget(QLabel("Local review milestone • Uploads are not enabled yet • Closing this window keeps monitoring active"))
        self.table.itemSelectionChanged.connect(self.update_actions)
        self.table.currentCellChanged.connect(lambda *_: self.update_actions())
        self.update_actions()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.scan_requested.emit()

    def selected(self) -> Recording | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        row = rows[0].row()
        if not self.table.item(row, 0):
            return None
        return self.records.get(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole))

    def update_actions(self) -> None:
        record = self.selected()
        self.review_button.setEnabled(record is not None)

    def update_records(self, records: list[Recording]) -> None:
        selected = self.selected()
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.clearContents()
        self.records = {record.id: record for record in records}
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            status = str(record.status)
            if record.vmix_state == "recording":
                status = "Recording (vMix)"
            elif record.vmix_state == "interrupted":
                status = "Recording interrupted / stop unconfirmed"
            if not record.available:
                status += " (file unavailable)"
            elif record.deferred and record.status == Status.READY:
                status += " (Later)"
            directory = Path(record.path).parent
            progress = (100.0 if record.status == Status.UPLOADED else
                        100 * record.bytes_uploaded / record.size if record.size and record.bytes_uploaded else -1.0)
            columns = [
                (Path(record.path).name if record.path else record.title, (Path(record.path).name if record.path else record.title).casefold()),
                ((directory.name or str(directory)) if record.path else "—", (directory.name or str(directory)).casefold()),
                (local_time(record.detected_at), datetime.fromisoformat(record.detected_at).timestamp()),
                (size_text(record.size), record.size),
                (status, status.casefold()),
                (f"{progress:.0f}%" if progress >= 0 else "—", progress),
                (record.youtube_url or "—", record.youtube_url or ""),
            ]
            for column, (text, sort_value) in enumerate(columns):
                item = SortableItem(text, sort_value)
                item.setToolTip(record.path if not record.error else f"{record.path}\n{record.error}")
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, record.id)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        if selected:
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == selected.id:
                    self.table.selectRow(row)
                    break
        self.table.blockSignals(False)
        self.update_actions()

    def review(self) -> None:
        record = self.selected()
        if record:
            self.review_requested.emit(record.id)

class ReviewWindow(QDialog):
    action_requested = Signal(object)

    def __init__(self, record: Recording):
        super().__init__()
        self.record = record
        self.setWindowTitle("Upload recording")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.resize(600, 430)
        form = QFormLayout(self)
        filename = QLabel(Path(record.path).name if record.path else record.title)
        filename.setTextFormat(Qt.TextFormat.PlainText)
        form.addRow("Video", filename)
        path = QLineEdit(record.path)
        path.setReadOnly(True)
        form.addRow("Full path", path)
        form.addRow("File size", QLabel(size_text(record.size)))
        recorded = datetime.fromtimestamp(record.mtime_ns / 1_000_000_000).astimezone()
        form.addRow("Last modified", QLabel(recorded.strftime("%Y-%m-%d %H:%M:%S") if record.mtime_ns else "—"))
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
        self.update_record(record)

    def update_record(self, record: Recording) -> None:
        # Keep user edits intact while background readiness checks finish.
        previous = self.record
        if ((previous.identity, previous.size, previous.mtime_ns) != (record.identity, record.size, record.mtime_ns)
                and not (record.source == "vmix" and previous.status in {Status.DETECTING, Status.WAITING})):
            self.notice.setText("The file changed. Close this window and select Upload again after it finishes.")
            self.later.setEnabled(False)
            self.skip.setEnabled(False)
            return
        self.record = record
        ready = record.available and record.status == Status.READY
        self.later.setEnabled(ready)
        self.skip.setEnabled(ready)
        if record.vmix_state == "recording":
            message = "vMix is recording. Waiting for its recording-stopped event."
        elif record.vmix_state == "interrupted":
            message = "vMix recording completion was not confirmed. Reconnect vMix before continuing."
        elif record.source == "vmix" and record.vmix_state == "stopped" and not ready:
            message = record.error or "vMix stopped recording. Waiting for the recording file to become accessible."
        elif not record.available:
            message = "The source file is unavailable. Waiting for it to become accessible."
        elif record.status in {Status.WAITING, Status.DETECTING}:
            message = "Checking file completion. You can edit the details while waiting."
        elif not ready:
            message = f"Current status: {record.status}."
        else:
            message = "Later saves these details for a future upload."
        self.notice.setText(message + " YouTube uploads are not implemented yet.")

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
