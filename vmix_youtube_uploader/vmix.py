"""Read-only vMix HTTP state polling and primary-recorder lifecycle."""

from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET

import requests

from .config import Settings, validate_vmix_url
from .database import Database
from .files import exclusive_access, sampled_fingerprint
from .models import Snapshot, Status


@dataclass(frozen=True)
class VmixState:
    recording: bool
    streaming: bool | None
    filename: str = ""


def parse_state(xml: bytes | str) -> VmixState:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError("vMix returned invalid XML.") from exc
    if root.tag != "vmix":
        raise ValueError("The endpoint did not return vMix state.")
    recording = root.find("recording")
    if recording is None or (recording.text or "").strip().lower() not in {"true", "false"}:
        raise ValueError("vMix recording state is missing or invalid.")
    streaming = (root.findtext("streaming") or "").strip().lower()
    filename = recording.get("filename1", recording.get("filename", "")).strip()
    return VmixState((recording.text or "").strip().lower() == "true",
                     streaming == "true" if streaming in {"true", "false"} else None, filename)


class VmixClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False

    def fetch(self, url: str) -> VmixState:
        validate_vmix_url(url)
        with self.session.get(url, timeout=(1, 2), allow_redirects=False) as response:
            if response.status_code != 200:
                raise ValueError(f"vMix returned HTTP {response.status_code}.")
            if len(response.content) > 4 * 1024 * 1024:
                raise ValueError("vMix state response is too large.")
            return parse_state(response.content)

    def close(self) -> None:
        self.session.close()


class VmixMonitor:
    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings
        self.previous: VmixState | None = None
        self.active: list[int] = []
        self.current_id: int | None = None
        self.current_path = ""
        self.finalizing: set[int] = {record.id for record in database.all()
                                     if record.source == "vmix" and record.vmix_state == "stopped"
                                     and record.status in {Status.WAITING, Status.DETECTING}}
        self.message = "Waiting for vMix"

    def disconnect(self) -> None:
        # An unknown state is never a recording-stop event.
        for record_id in self.active:
            self.database.set_vmix_state(record_id, "interrupted", "vMix connection interrupted; stop was not confirmed.")
        self.active.clear()
        self.current_id = None
        self.current_path = ""
        self.previous = None

    def update(self, state: VmixState) -> list[int]:
        stopped: list[int] = []
        if state.recording:
            path = state.filename
            if path and not Path(path).is_absolute():
                path = ""
            if self.current_id is None:
                self.current_id = self.database.begin_vmix(path)
                self.active.append(self.current_id)
            elif path and self.current_path and Path(path) != Path(self.current_path):
                # Keep split recordings; review each observed segment on stop.
                self.current_id = self.database.begin_vmix(path)
                self.active.append(self.current_id)
            if path:
                old_id = self.current_id
                self.current_id = self.database.observe_vmix(old_id, path, self.settings.description)
                self.active = list(dict.fromkeys(self.current_id if i == old_id else i for i in self.active))
                self.current_path = path
                self.message = "Recording"
            else:
                self.message = "Recording: vMix has not provided an absolute filename"
        elif self.previous is not None and self.previous.recording:
            # Retain the path captured while recording; it may vanish from XML on stop.
            if self.current_id is not None and not self.current_path and state.filename and Path(state.filename).is_absolute():
                old_id = self.current_id
                self.current_id = self.database.observe_vmix(old_id, state.filename, self.settings.description)
                self.active = list(dict.fromkeys(self.current_id if i == old_id else i for i in self.active))
            for record_id in self.active:
                self.database.set_vmix_state(record_id, "stopped")
                self.finalizing.add(record_id)
                stopped.append(record_id)
            self.active.clear()
            self.current_id = None
            self.current_path = ""
            self.message = "Idle"
        else:
            self.message = "Idle"
        self.previous = state
        return stopped

    def check_completed(self) -> None:
        # No stability timer. The API confirmed stop; wait only for an accessible,
        # nonempty file and verify it did not change during fingerprinting.
        for record_id in list(self.finalizing):
            record = self.database.get(record_id)
            if not record.path:
                self.database.set_vmix_state(record_id, "stopped", "vMix did not supply a recording filename.")
                self.finalizing.remove(record_id)
                continue
            if self.previous and self.previous.recording and self.current_path and Path(record.path) == Path(self.current_path):
                continue
            try:
                snapshot = Snapshot.read(Path(record.path))
                if snapshot.size <= 0 or not exclusive_access(Path(record.path)):
                    continue
                fingerprint = sampled_fingerprint(Path(record.path), snapshot.size)
                if Snapshot.read(Path(record.path)).version != snapshot.version:
                    continue
                bound_id = self.database.observe_vmix(record_id, record.path, self.settings.description)
                self.database.set_vmix_state(bound_id, "stopped")
                bound = self.database.get(bound_id)
                if (bound.identity, bound.size, bound.mtime_ns) != snapshot.version:
                    if bound_id != record_id:
                        self.finalizing.remove(record_id)
                        self.finalizing.add(bound_id)
                    continue
                self.database.ready(bound_id, fingerprint)
                self.finalizing.remove(record_id)
            except OSError:
                continue
