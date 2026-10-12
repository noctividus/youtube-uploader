"""Validated per-user configuration, replaced atomically on save."""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .formats import DEFAULT_EXTENSIONS, EXTENSION_CHOICES

DEFAULT_DESCRIPTION = "Recorded at the Adventurers' Club of Los Angeles."
WEBHOOK_EVENTS = {
    "recording_started": "Recording Started",
    "recording_finished": "Recording Finished",
    "upload_started": "Upload Started",
    "upload_finished": "Upload Finished",
    "error": "Error",
}


def validate_vmix_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                 and not parsed.query and not parsed.fragment
                 and not any(char.isspace() for char in url))
        parsed.port
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise ValueError("vMix API URL must be HTTP/HTTPS with no query parameters or fragment.")


@dataclass(frozen=True)
class WebhookEndpoint:
    url: str
    events: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not isinstance(self.url, str) or any(char.isspace() for char in self.url):
            raise ValueError("Enter a valid HTTP or HTTPS endpoint URL.")
        try:
            parsed = urlsplit(self.url)
            valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
            parsed.port  # Validate malformed and out-of-range port numbers.
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Enter a valid HTTP or HTTPS endpoint URL.")
        if not isinstance(self.events, list) or not self.events or not all(
            isinstance(event, str) and event in WEBHOOK_EVENTS for event in self.events
        ):
            raise ValueError("Select at least one webhook trigger.")


@dataclass(frozen=True)
class Settings:
    directories: list[str] = field(default_factory=list)
    stability_seconds: int = 60
    scan_seconds: int = 30
    periodic_scan: bool = True
    notifications: bool = True
    description: str = DEFAULT_DESCRIPTION
    file_extensions: list[str] = field(default_factory=lambda: list(DEFAULT_EXTENSIONS))
    webhooks: list[WebhookEndpoint] = field(default_factory=list)
    vmix_enabled: bool = True
    vmix_url: str = "http://127.0.0.1:8088/api/"
    vmix_poll_seconds: int = 2

    def validate(self) -> None:
        if type(self.vmix_enabled) is not bool:
            raise ValueError("vMix enabled must be a boolean.")
        validate_vmix_url(self.vmix_url)
        if type(self.vmix_poll_seconds) is not int or not 1 <= self.vmix_poll_seconds <= 60:
            raise ValueError("vMix polling interval must be between 1 and 60 seconds.")
        if not isinstance(self.webhooks, list):
            raise ValueError("Webhooks must be a list of endpoints.")
        for index, endpoint in enumerate(self.webhooks, start=1):
            if not isinstance(endpoint, WebhookEndpoint):
                raise ValueError(f"Webhook {index}: invalid endpoint settings.")
            try:
                endpoint.validate()
            except ValueError as exc:
                raise ValueError(f"Webhook {index}: {exc}") from exc
        if not isinstance(self.file_extensions, list) or not all(
            isinstance(extension, str) and extension in EXTENSION_CHOICES
            for extension in self.file_extensions
        ):
            raise ValueError("File extensions must be a list of available lowercase extensions.")
        if not isinstance(self.directories, list) or not all(
            isinstance(p, str) and p.strip() and Path(p).is_absolute()
            for p in self.directories
        ):
            raise ValueError("Watched directories must be absolute paths.")
        if type(self.stability_seconds) is not int or not 1 <= self.stability_seconds <= 86400:
            raise ValueError("Stability period must be between 1 and 86400 seconds.")
        if type(self.scan_seconds) is not int or not 1 <= self.scan_seconds <= 86400:
            raise ValueError("Scan interval must be between 1 and 86400 seconds.")
        if type(self.periodic_scan) is not bool or type(self.notifications) is not bool:
            raise ValueError("Notification and scanning settings must be booleans.")
        if not isinstance(self.description, str):
            raise ValueError("Default description must be text.")


def load_settings(path: Path) -> Settings:
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Settings must be an object.")
        if "webhooks" in raw:
            if not isinstance(raw["webhooks"], list):
                raise ValueError("Webhooks must be a list of endpoints.")
            raw["webhooks"] = [WebhookEndpoint(**endpoint) for endpoint in raw["webhooks"]]
        value = Settings(**raw)
        value.validate()
        return value
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid settings in {path}: {exc}") from exc


def save_settings(path: Path, settings: Settings) -> None:
    settings.validate()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(asdict(settings), stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
