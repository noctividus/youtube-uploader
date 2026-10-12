import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {"time": datetime.now(timezone.utc).isoformat(), "level": record.levelname,
                 "logger": record.name, "message": record.getMessage()}
        if record.exc_info:
            value["exception"] = self.formatException(record.exc_info)
        return json.dumps(value)


def configure_logging(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / "uploader.log", maxBytes=2_000_000,
                                  backupCount=5, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
