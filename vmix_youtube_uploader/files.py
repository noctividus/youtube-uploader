"""File readiness evidence; no GUI or database dependencies."""

import ctypes
import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .models import Snapshot


def exclusive_access(path: Path) -> bool:
    """Try a read handle with no sharing on Windows, and always close it.

    This is evidence at a point in time, not a promise a writer cannot reopen.
    """
    if os.name != "nt":
        try:
            with path.open("rb"):
                return True
        except OSError:
            return False
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        return False
    close(handle)
    return True


def sampled_fingerprint(path: Path, size: int) -> str:
    """Read at most 192 KiB, after stability, rather than hashing large videos."""
    digest = hashlib.sha256(str(size).encode("ascii"))
    block = 65536
    with path.open("rb") as stream:
        for offset in sorted({0, max(0, size // 2 - block // 2), max(0, size - block)}):
            stream.seek(offset)
            digest.update(stream.read(block))
    return digest.hexdigest()


@dataclass
class Candidate:
    version: tuple[str, int, int]
    since: float


class CompletionTracker:
    def __init__(self, seconds: int, access: Callable[[Path], bool] = exclusive_access):
        self.seconds = seconds
        self.access = access
        self.candidates: dict[str, Candidate] = {}

    def reset(self) -> None:
        self.candidates.clear()

    def forget(self, identity: str) -> None:
        self.candidates.pop(identity, None)

    def check(self, snapshot: Snapshot, clock: float) -> str | None:
        candidate = self.candidates.get(snapshot.identity)
        if candidate is None or candidate.version != snapshot.version:
            self.candidates[snapshot.identity] = Candidate(snapshot.version, clock)
            return None
        if snapshot.size <= 0 or clock - candidate.since < self.seconds:
            return None
        path = Path(snapshot.path)
        if not self.access(path):
            candidate.since = clock
            return None
        try:
            fingerprint = sampled_fingerprint(path, snapshot.size)
            if Snapshot.read(path).version != snapshot.version:
                self.forget(snapshot.identity)
                return None
            return fingerprint
        except OSError:
            self.forget(snapshot.identity)
            return None


def revalidate(record_path: str, version: tuple[str, int, int], fingerprint: str | None) -> bool:
    """Shared pre-action check for review decisions and upload approval/transfer."""
    path = Path(record_path)
    try:
        snapshot = Snapshot.read(path)
        return (
            snapshot.version == version and exclusive_access(path)
            and sampled_fingerprint(path, snapshot.size) == fingerprint
            and Snapshot.read(path).version == version
        )
    except OSError:
        return False
