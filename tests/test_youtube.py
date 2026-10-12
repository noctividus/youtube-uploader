import json
import os
import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import httplib2
import pytest
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest
from googleapiclient.model import JsonModel

from vmix_youtube_uploader.files import sampled_fingerprint
from vmix_youtube_uploader.models import Snapshot, Status
from vmix_youtube_uploader.youtube import UploadFailure, protected_video, read_client_config, safe_error, upload_video
from vmix_youtube_uploader.ui import ReviewWindow, SettingsWindow
from vmix_youtube_uploader.config import Settings


def ready(database, tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"test video contents")
    record = database.observe(Snapshot.read(path), "Description")
    database.ready(record.id, sampled_fingerprint(path, record.size))
    return database.get(record.id)


def version(record):
    return record.identity, record.size, record.mtime_ns, record.fingerprint


def approve(database, record, **kwargs):
    return database.approve_upload(record.id, version(record), "Video", "Description", "private", "channel", **kwargs)


def fake_api():
    api = Mock()
    api.channels.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "channel", "snippet": {"title": "My channel"}}]}
    request = api.videos.return_value.insert.return_value
    request.resumable_uri = "https://upload.example/secret-session"
    request.next_chunk.return_value = (None, {"id": "video123"})
    return api, request


def test_approval_freezes_metadata_and_rejects_duplicate_or_changed_version(database, tmp_path):
    record = ready(database, tmp_path)
    with pytest.raises(ValueError, match="changed"):
        database.approve_upload(record.id, ("wrong", *version(record)[1:]), "Title", "", "private", "channel")
    job = approve(database, record)
    assert database.get(record.id).approved
    with pytest.raises(ValueError, match="not ready"):
        approve(database, record)
    saved = database.connection.execute("SELECT job FROM upload_attempts").fetchone()[0]
    assert json.loads(saved)["channel_id"] == job.channel_id
    assert json.loads(saved)["privacy"] == "private"


def test_startup_recovery_requires_duplicate_acknowledgment(database, tmp_path):
    record = ready(database, tmp_path)
    job = approve(database, record)
    database.upload_event("started", {"attempt_id": job.attempt_id})
    database.recover(uploads=False)
    assert database.get(record.id).status == Status.UPLOADING
    database.recover()
    recovered = database.get(record.id)
    assert recovered.status == Status.FAILED and recovered.upload_ambiguous
    with pytest.raises(ValueError, match="duplicate"):
        approve(database, recovered)
    second = approve(database, recovered, acknowledge_duplicate=True)
    assert second.attempt_id != job.attempt_id
    assert database.connection.execute("SELECT count(*) FROM upload_attempts").fetchone()[0] == 2


def test_new_file_version_cannot_inherit_upload_result(database, tmp_path):
    record = ready(database, tmp_path)
    job = approve(database, record)
    path = tmp_path / "video.mp4"
    path.write_bytes(b"a different recording")
    database.observe(Snapshot.read(path), "")
    database.upload_event("created", {"attempt_id": job.attempt_id, "video_id": "old", "url": "https://www.youtube.com/watch?v=old"})
    assert database.get(record.id).youtube_id is None
    assert database.get(record.id).status == Status.WAITING
    assert database.connection.execute("SELECT video_id FROM upload_attempts").fetchone()[0] == "old"


def test_upload_sends_approved_metadata_and_persists_success_before_playlist(database, tmp_path):
    record = ready(database, tmp_path)
    job = approve(database, record, playlist_id="playlist")
    api, request = fake_api()
    api.playlistItems.return_value.insert.return_value.execute.side_effect = RuntimeError("secret")
    events = []
    outcome = upload_video(api, job, threading.Event(), lambda *event: events.append(event))
    assert events[0][0] == "created"
    assert not events[0][1]["warning"]
    assert outcome["video_id"] == "video123" and outcome["warning"]
    body = api.videos.return_value.insert.call_args.kwargs["body"]
    assert body["status"]["privacyStatus"] == "private"
    assert body["snippet"]["title"] == job.title
    assert request.next_chunk.call_count == 1
    database.upload_event(*events[0])
    database.upload_event("done", outcome)
    assert database.get(record.id).status == Status.UPLOADED
    assert database.get(record.id).bytes_uploaded == record.size


def test_network_retry_uses_same_resumable_request(database, tmp_path):
    job = approve(database, ready(database, tmp_path))
    api, request = fake_api()
    request.next_chunk.side_effect = [OSError("secret URL"),
        (SimpleNamespace(resumable_progress=4), None), (None, {"id": "video123"})]
    stopping = Mock()
    stopping.is_set.return_value = False
    events = []
    upload_video(api, job, stopping, lambda *event: events.append(event))
    assert api.videos.return_value.insert.call_count == 1
    assert request.next_chunk.call_count == 3
    assert [kind for kind, _ in events] == ["retry", "progress", "created"]


def test_google_transport_recovers_lost_final_response_without_new_insert(database, tmp_path):
    job = approve(database, ready(database, tmp_path))
    api, _ = fake_api()
    http = Mock()
    http.request.side_effect = [
        (httplib2.Response({"status": "200", "location": "https://upload.example/session"}), b""),
        OSError("Final response lost"),
        (httplib2.Response({"status": "200"}), b'{"id":"already-created"}'),
    ]
    def insert(**kwargs):
        return HttpRequest(http, JsonModel().response, "https://www.googleapis.com/upload/youtube/v3/videos",
                           method="POST", body=json.dumps(kwargs["body"]), resumable=kwargs["media_body"])
    api.videos.return_value.insert.side_effect = insert
    stopping = Mock()
    stopping.is_set.return_value = False
    outcome = upload_video(api, job, stopping, lambda *_: None)
    assert outcome["video_id"] == "already-created"
    assert http.request.call_count == 3
    recovery = http.request.call_args
    assert recovery.args[:2] == ("https://upload.example/session", "PUT")
    assert recovery.kwargs["headers"]["Content-Range"] == f"bytes */{job.size}"
    assert api.videos.return_value.insert.call_count == 1


@pytest.mark.parametrize("failure,session,ambiguous", [
    (OSError("SECRET"), None, True),
    (HttpError(httplib2.Response({"status": "403"}), b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}'), None, False),
])
def test_failure_does_not_implicitly_start_another_upload(database, tmp_path, failure, session, ambiguous):
    job = approve(database, ready(database, tmp_path))
    api, request = fake_api()
    request.resumable_uri = session
    request.next_chunk.side_effect = failure
    with pytest.raises(UploadFailure) as error:
        upload_video(api, job, threading.Event(), lambda *_: None)
    assert error.value.ambiguous == ambiguous
    assert "SECRET" not in str(error.value)
    assert request.next_chunk.call_count == 1


def test_wrong_channel_or_changed_source_never_calls_insert(database, tmp_path):
    job = approve(database, ready(database, tmp_path))
    api, _ = fake_api()
    with pytest.raises(UploadFailure, match="channel changed"):
        upload_video(api, replace(job, channel_id="other"), threading.Event(), lambda *_: None)
    (tmp_path / "video.mp4").write_bytes(b"changed")
    with pytest.raises(UploadFailure, match="changed"):
        upload_video(api, job, threading.Event(), lambda *_: None)
    api.videos.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing locks")
def test_source_cannot_be_overwritten_or_deleted_while_uploading(database, tmp_path):
    job = approve(database, ready(database, tmp_path))
    path = tmp_path / "video.mp4"
    with protected_video(job) as stream:
        assert stream.read() == b"test video contents"
        with pytest.raises(OSError):
            path.write_bytes(b"overwrite")
        with pytest.raises(OSError):
            path.unlink()
    assert path.read_bytes() == b"test video contents"


def test_oauth_import_rejects_web_client_and_ignores_foreign_endpoints(tmp_path):
    path = tmp_path / "client.json"
    path.write_text('{"web": {}}')
    with pytest.raises(UploadFailure, match="Desktop"):
        read_client_config(path)
    path.write_text(json.dumps({"installed": {"client_id": "test.apps.googleusercontent.com",
        "client_secret": "secret", "token_uri": "https://untrusted.invalid"}}))
    assert read_client_config(path)["installed"]["token_uri"] == "https://oauth2.googleapis.com/token"
    assert "secret" not in safe_error(RuntimeError("secret"))


def test_review_requires_connection_and_explicit_retry_acknowledgment(app, database, tmp_path):
    record = ready(database, tmp_path)
    window = ReviewWindow(record)
    payloads = []
    window.upload_requested.connect(payloads.append)
    try:
        assert not window.upload.isEnabled()
        window.set_youtube_state({"connected": True, "channel_id": "channel", "channel_name": "My channel"})
        assert window.upload.isEnabled()
        window.update_record(replace(record, status=Status.FAILED, upload_ambiguous=True))
        assert not window.upload.isEnabled()
        window.duplicate.setChecked(True)
        assert window.upload.isEnabled()
        window.upload.click()
        assert payloads[0][5] == "channel" and payloads[0][-1]
        assert not window.upload.isEnabled()
    finally:
        window.deleteLater()


def test_settings_channel_operations_are_separate_from_save(app):
    window = SettingsWindow(Settings())
    calls = []
    window.youtube_requested.connect(calls.append)
    try:
        assert window.tabs.tabText(4) == "YouTube"
        assert not window.youtube_connect.isEnabled()
        window.set_youtube_state({"imported": True})
        window.youtube_connect.click()
        assert calls == [("connect", None)]
        window.set_youtube_state({"imported": True, "connected": True, "busy": True})
        assert not window.youtube_disconnect.isEnabled()
    finally:
        window.deleteLater()


def test_connection_and_disconnect_use_secure_store_without_plaintext_files(tmp_path, monkeypatch):
    import time
    from vmix_youtube_uploader import youtube_service as module
    values = {"client": {"installed": {"client_id": "test.apps.googleusercontent.com"}}}
    store = Mock()
    store.get.side_effect = lambda key: values.get(key)
    store.put.side_effect = lambda key, value: values.update({key: value})
    store.delete.side_effect = lambda key: values.pop(key, None)
    monkeypatch.setattr(module, "CredentialStore", lambda _: store)
    credentials = SimpleNamespace(refresh_token="SECRET-REFRESH", client_id="id", client_secret="SECRET-CLIENT",
                                  token_uri="https://oauth2.googleapis.com/token", scopes=["scope"], token="ACCESS-SECRET")
    monkeypatch.setattr(module, "authorize", lambda *_: credentials)
    api, _ = fake_api()
    api.playlists.return_value.list.return_value.execute.return_value = {"items": []}
    monkeypatch.setattr(module, "youtube_api", lambda _: api)
    events = []
    service = module.YouTubeService(tmp_path, lambda *event: events.append(event))
    service.start()
    try:
        service.submit("connect")
        deadline = time.monotonic() + 3
        while "account" not in values and time.monotonic() < deadline:
            time.sleep(0.01)
        assert values["account"]["credentials"]["refresh_token"] == "SECRET-REFRESH"
        assert "token" not in values["account"]["credentials"]
        assert "SECRET" not in repr(events)
        assert not list(tmp_path.iterdir())
        service.submit("disconnect")
        deadline = time.monotonic() + 3
        while "account" in values and time.monotonic() < deadline:
            time.sleep(0.01)
        assert "account" not in values and "client" in values
    finally:
        service.stopping.set()
        service.join(3)
        assert not service.is_alive()


def test_oauth_callback_uses_state_and_exchanges_code_without_logging(tmp_path, monkeypatch, caplog):
    import urllib.request
    from vmix_youtube_uploader import youtube as module
    flow = Mock()
    flow.authorization_url.return_value = ("https://accounts.google.com/auth", "expected-state")
    flow.credentials = SimpleNamespace(refresh_token="SECRET-REFRESH")
    monkeypatch.setattr(module.InstalledAppFlow, "from_client_config", lambda *_args, **_kwargs: flow)
    browser_threads = []
    failures = []
    def browser(_url):
        def callback():
            try:
                with pytest.raises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(flow.redirect_uri + "?state=wrong&code=SECRET-CODE", timeout=3)
                assert error.value.code == 400
                with urllib.request.urlopen(flow.redirect_uri + "?state=expected-state&code=SECRET-CODE", timeout=3) as response:
                    assert response.status == 200
            except Exception as exc:
                failures.append(exc)
        thread = threading.Thread(target=callback)
        browser_threads.append(thread)
        thread.start()
        return True
    monkeypatch.setattr(module.webbrowser, "open", browser)
    assert module.authorize({}, threading.Event()) is flow.credentials
    for thread in browser_threads:
        thread.join(4)
    assert not failures
    flow.fetch_token.assert_called_once_with(code="SECRET-CODE", timeout=20)
    assert "SECRET" not in caplog.text


def test_worker_uploads_only_after_approval_and_persists_result(app, tmp_path, monkeypatch):
    import time
    from vmix_youtube_uploader import youtube_service as module
    from vmix_youtube_uploader.config import save_settings
    from vmix_youtube_uploader.database import Database
    from vmix_youtube_uploader.worker import Worker
    account = {"profile": {"channel_id": "channel", "channel_name": "My channel"},
               "credentials": {"refresh_token": "fake", "client_id": "fake", "client_secret": "fake"}}
    store = Mock()
    store.get.side_effect = lambda key: account if key == "account" else {"installed": {}}
    monkeypatch.setattr(module, "CredentialStore", lambda _: store)
    api, request = fake_api()
    monkeypatch.setattr(module, "youtube_api", lambda _: api)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"A test recording")
    data_dir = tmp_path / "data"
    save_settings(data_dir / "settings.json", Settings(directories=[str(tmp_path)], vmix_enabled=False,
                                                        stability_seconds=1, notifications=False))
    records, accounts, outcomes, errors = [], [], [], []
    worker = Worker(data_dir)
    worker.records_changed.connect(lambda values: records.extend(values))
    worker.youtube_state.connect(accounts.append)
    worker.upload_outcome.connect(outcomes.append)
    worker.error.connect(errors.append)
    def wait_for(condition):
        deadline = time.monotonic() + 6
        while not condition() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert condition(), errors
    worker.start()
    try:
        wait_for(lambda: records and records[-1].status == Status.READY and accounts)
        api.videos.assert_not_called()
        record = records[-1]
        worker.submit("upload", (record.id, version(record), "Approved title", "Description", "private", "channel", "", False))
        wait_for(lambda: outcomes and records[-1].status == Status.UPLOADED)
        assert outcomes[-1]["video_id"] == "video123"
        assert request.next_chunk.call_count == 1
    finally:
        worker.stop()
        assert worker.wait(6000)
        app.processEvents()
        worker.deleteLater()
    persisted = Database(data_dir / "queue.sqlite3")
    try:
        assert persisted.all()[0].youtube_id == "video123"
        assert persisted.all()[0].title == "Approved title"
        persisted.recover()
        assert persisted.all()[0].status == Status.UPLOADED
    finally:
        persisted.close()
