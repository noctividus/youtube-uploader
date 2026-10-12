"""Validated per-user configuration, replaced atomically on save."""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .formats import DEFAULT_EXTENSIONS, EXTENSION_CHOICES

DEFAULT_DESCRIPTION = "Recorded at the Adventurers' Club of Los Angeles."


@dataclass(frozen=True)
class Settings:
    directories: list[str] = field(default_factory=list)
    stability_seconds: int = 60
    scan_seconds: int = 30
    periodic_scan: bool = True
    notifications: bool = True
    description: str = DEFAULT_DESCRIPTION
    file_extensions: list[str] = field(default_factory=lambda: list(DEFAULT_EXTENSIONS))

    def validate(self) -> None:
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
        value = Settings(**json.loads(path.read_text(encoding="utf-8")))
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
