import pytest

from vmix_youtube_uploader.config import Settings, WebhookEndpoint, load_settings, save_settings


def test_webhooks_roundtrip_and_old_settings_default(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    assert load_settings(path).webhooks == []
    settings = Settings(webhooks=[
        WebhookEndpoint("https://example.com/hook", ["recording_started", "error"]),
        WebhookEndpoint("http://localhost:8080/hook", ["upload_finished"]),
    ])
    save_settings(path, settings)
    assert load_settings(path) == settings


@pytest.mark.parametrize("endpoint", [
    WebhookEndpoint("", ["error"]),
    WebhookEndpoint("file:///example", ["error"]),
    WebhookEndpoint("https://example.com:invalid", ["error"]),
    WebhookEndpoint("https://example.com", []),
    WebhookEndpoint("https://example.com", ["unknown"]),
])
def test_webhook_validation(endpoint):
    with pytest.raises(ValueError):
        Settings(webhooks=[endpoint]).validate()


def test_extension_filter_roundtrip_and_older_settings(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"stability_seconds": 15}', encoding="utf-8")
    assert set(load_settings(path).file_extensions) == {".mp4", ".mkv", ".mov", ".avi"}
    for extensions in ([".webm", ".wmv"], []):
        settings = Settings(file_extensions=extensions)
        save_settings(path, settings)
        assert load_settings(path).file_extensions == extensions


@pytest.mark.parametrize("extensions", [".mp4", [None], [".exe"], ["MP4"]])
def test_invalid_extension_filters_are_rejected(extensions):
    with pytest.raises(ValueError, match="extensions"):
        Settings(file_extensions=extensions).validate()


def test_settings_roundtrip_and_atomic_write(tmp_path):
    path = tmp_path / "settings.json"
    assert load_settings(path) == Settings()
    settings = Settings(directories=[str(tmp_path)], stability_seconds=2, notifications=False)
    save_settings(path, settings)
    assert load_settings(path) == settings
    assert not path.with_suffix(".tmp").exists()


@pytest.mark.parametrize("payload", ["{", "[]", '{"stability_seconds": 0}', '{"periodic_scan": "yes"}', '{"directories": ["relative"]}'])
def test_invalid_settings_report_error_without_overwriting(tmp_path, payload):
    path = tmp_path / "settings.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(path)
    assert path.read_text(encoding="utf-8") == payload


def test_failed_replace_keeps_previous_settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    save_settings(path, Settings())
    def fail(*args):
        raise OSError("disk failure")
    monkeypatch.setattr("vmix_youtube_uploader.config.os.replace", fail)
    with pytest.raises(OSError):
        save_settings(path, Settings(stability_seconds=2))
    assert load_settings(path) == Settings()
