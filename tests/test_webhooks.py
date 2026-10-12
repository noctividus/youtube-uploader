import json
import sqlite3
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

import pytest
import requests
from PySide6.QtWidgets import QLineEdit

from vmix_youtube_uploader.config import Settings, WebhookEndpoint, load_settings, save_settings
from vmix_youtube_uploader.ui import SettingsWindow
from vmix_youtube_uploader.webhooks import Webhooks
from vmix_youtube_uploader.worker import Worker


def test_headers_encrypted_on_disk_and_masked_in_ui(tmp_path, app):
    endpoint = WebhookEndpoint("https://example.com/hook", ["upload_finished"],
                               {"Authorization": "Bearer TEST-SECRET", "X-API-Key": "SECOND-SECRET"})
    settings = Settings(webhooks=[endpoint])
    path = tmp_path / "settings.json"
    save_settings(path, settings)
    assert "TEST-SECRET" not in path.read_text()
    assert "SECOND-SECRET" not in path.read_text()
    assert load_settings(path) == settings
    assert "SECRET" not in repr(endpoint)
    window = SettingsWindow(settings)
    try:
        editor = window.webhook_editors[0]
        assert editor.header_rows[0][2].echoMode() == QLineEdit.EchoMode.Password
        assert editor.value() == endpoint
        editor.add_header("authorization", "duplicate")
        window.save()
        assert "unique" in window.validation_error.text()
    finally:
        window.deleteLater()


@pytest.mark.parametrize("headers", [
    {"Authorization": "Bearer x\r\nOther: injected"}, {"Bad Name": "x"},
    {"Host": "foreign.example"}, {"Content-Length": "1"},
    {"X-Webhook-Id": "override"}, {"Authorization": "x", "authorization": "y"},
    {"X-Key": ""},
])
def test_invalid_headers_rejected(headers):
    with pytest.raises(ValueError):
        WebhookEndpoint("https://example.com", ["error"], headers).validate()


def test_real_http_delivery_with_headers_and_event_filtering(tmp_path):
    received = []
    done = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            received.append((dict(self.headers), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(204)
            self.end_headers()
            done.set()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    reports = []
    path = tmp_path / "hooks.sqlite3"
    sender = Webhooks(path, reports.append)
    endpoint = WebhookEndpoint(f"http://127.0.0.1:{server.server_port}/hook", ["upload_finished"],
                               {"Authorization": "Bearer TEST-SECRET", "X-API-Key": "key"})
    sender.enqueue([endpoint], "recording_started", {})
    event_id = sender.enqueue([endpoint], "upload_finished", {"youtube_id": "video"})
    assert b"TEST-SECRET" not in path.read_bytes()
    # A new dispatcher represents restarting before the first attempt.
    sender = Webhooks(path, reports.append)
    sender.start()
    try:
        assert done.wait(4)
    finally:
        sender.stop()
        sender.join(4)
        server.shutdown()
        thread.join(4)
        server.server_close()
    assert not sender.is_alive()
    assert len(received) == 1
    headers, payload = received[0]
    assert headers["Authorization"] == "Bearer TEST-SECRET"
    assert headers["X-API-Key"] == "key"
    assert headers["X-Webhook-Id"] == payload["id"] == event_id
    assert payload["event"] == "upload_finished"
    assert payload["data"] == {"youtube_id": "video"}
    assert reports and "delivered" in reports[-1]
    assert "TEST-SECRET" not in repr(reports)


@pytest.mark.parametrize("failure,retries", [(302, False), (401, False), (429, True), (503, True), (None, True)])
def test_delivery_retry_limits_and_sanitized_errors(tmp_path, failure, retries):
    reports = []
    sender = Webhooks(tmp_path / "hooks.sqlite3", reports.append)
    event_id = sender.enqueue([WebhookEndpoint("https://example.com/SECRET-URL", ["error"])], "error", {})
    session = Mock()
    if failure is None:
        session.post.side_effect = requests.ConnectionError("SECRET-URL SECRET-TOKEN")
    else:
        session.post.return_value.__enter__ = Mock(return_value=Mock(status_code=failure))
        session.post.return_value.__exit__ = Mock(return_value=False)
    with closing(sqlite3.connect(sender.path)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM deliveries").fetchone()
        sender.deliver(db, session, row)
        row = db.execute("SELECT * FROM deliveries").fetchone()
        assert row["status"] == ("pending" if retries else "failed")
        if retries:
            sender.deliver(db, session, row)
            sender.deliver(db, session, db.execute("SELECT * FROM deliveries").fetchone())
            assert db.execute("SELECT status FROM deliveries").fetchone()[0] == "failed"
            assert session.post.call_count == 3
    for call in session.post.call_args_list:
        assert call.kwargs["allow_redirects"] is False
        assert call.kwargs["headers"]["X-Webhook-Id"] == event_id
    assert "SECRET" not in repr(reports)


def test_upload_event_routing_emits_one_success_before_playlist_warning(tmp_path, database):
    from vmix_youtube_uploader.models import Snapshot
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    record = database.observe(Snapshot.read(path), "")
    worker = Worker(tmp_path)
    worker.settings = Settings(webhooks=[WebhookEndpoint("https://example.com", ["upload_finished", "error"])])
    worker.webhooks = Mock()
    outcome = {"attempt_id": "attempt", "recording_id": record.id,
               "video_id": "video", "url": "https://www.youtube.com/watch?v=video", "warning": ""}
    worker._youtube_event(database, "created", outcome)
    worker._youtube_event(database, "done", dict(outcome, warning="Playlist unavailable"))
    assert worker.webhooks.enqueue.call_count == 1
    assert worker.webhooks.enqueue.call_args.args[1] == "upload_finished"
    worker._youtube_event(database, "failed", {"attempt_id": "second", "recording_id": record.id,
                                               "error": "Quota exceeded", "ambiguous": False})
    assert worker.webhooks.enqueue.call_args.args[1] == "error"
    worker.webhooks.enqueue.side_effect = RuntimeError("Disk error")
    worker._webhook("error", operation="test")  # Does not recurse on delivery failure.
    assert worker.webhooks.enqueue.call_count == 3


def test_vmix_events_do_not_repeat_each_poll_or_on_disconnect(app, tmp_path, monkeypatch):
    from vmix_youtube_uploader.vmix import VmixState
    import vmix_youtube_uploader.worker as module
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    states = iter([VmixState(True, False, str(path)), VmixState(True, False, str(path)),
                   requests.ConnectionError(), requests.ConnectionError(),
                   VmixState(True, False, str(path)), VmixState(False, False)])
    def fetch(_):
        value = next(states, VmixState(False, False))
        if isinstance(value, Exception):
            raise value
        return value
    client = Mock()
    client.fetch.side_effect = fetch
    monkeypatch.setattr(module, "VmixClient", lambda: client)
    delivery = Mock()
    monkeypatch.setattr(module, "Webhooks", lambda *_: delivery)
    save_settings(tmp_path / "settings.json", Settings(vmix_poll_seconds=1, notifications=False,
        webhooks=[WebhookEndpoint("http://localhost/hook", ["recording_started", "recording_finished", "error"])]))
    worker = Worker(tmp_path)
    worker.start()
    try:
        deadline = time.monotonic() + 9
        while client.fetch.call_count < 6 and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert client.fetch.call_count >= 6
    finally:
        worker.stop()
        assert worker.wait(5000)
        app.processEvents()
        worker.deleteLater()
    events = [call.args[1] for call in delivery.enqueue.call_args_list]
    assert events == ["recording_started", "error", "recording_finished"]
