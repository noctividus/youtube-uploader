"""Google desktop OAuth and resumable uploads. Never log tokens or session URLs."""

import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httplib2
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

from .files import revalidate, sampled_fingerprint
from .models import Snapshot

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]


class UploadFailure(Exception):
    def __init__(self, message: str, ambiguous: bool = False):
        super().__init__(message)
        self.ambiguous = ambiguous


def safe_error(exc: Exception) -> str:
    if isinstance(exc, UploadFailure):
        return str(exc)
    if isinstance(exc, RefreshError):
        return "Google authorization expired or was revoked. Reconnect the YouTube channel."
    if isinstance(exc, HttpError):
        reason = ""
        try:
            reason = json.loads(exc.content)["error"]["errors"][0]["reason"]
        except (ValueError, KeyError, IndexError, TypeError):
            pass
        messages = {
            "quotaExceeded": "YouTube API quota exceeded. Try again after the quota resets.",
            "dailyLimitExceeded": "The Google project's daily API limit was reached.",
            "uploadLimitExceeded": "This channel has reached its YouTube upload limit.",
            "youtubeSignupRequired": "The selected Google account does not have a YouTube channel.",
            "insufficientPermissions": "Reconnect the channel and grant the requested YouTube permissions.",
            "forbidden": "YouTube refused this operation. Check the connected channel and its permissions.",
            "invalidTitle": "YouTube rejected the title. Edit the title and try again.",
        }
        return messages.get(reason, f"YouTube API request failed (HTTP {exc.resp.status}).")
    # Do not stringify library errors: they can contain tokens, auth codes, or
    # resumable URLs. The type is useful without exposing those values.
    return f"YouTube operation failed ({type(exc).__name__}). Check connectivity and account setup."


class CredentialStore:
    def __init__(self, data_dir: Path):
        if os.name != "nt":
            raise UploadFailure("YouTube credential storage requires Windows Credential Manager.")
        from keyring.backends.Windows import WinVaultKeyring
        self.backend = WinVaultKeyring()  # No fallback to plaintext keyrings.
        suffix = hashlib.sha256(os.path.normcase(str(data_dir.absolute())).encode()).hexdigest()[:20]
        self.service = f"vMix YouTube Uploader:{suffix}"

    def get(self, name: str) -> dict | None:
        value = self.backend.get_password(self.service, name)
        return json.loads(value) if value else None

    def put(self, name: str, value: dict) -> None:
        self.backend.set_password(self.service, name, json.dumps(value))

    def delete(self, name: str) -> None:
        if self.backend.get_password(self.service, name) is not None:
            self.backend.delete_password(self.service, name)


def read_client_config(path: Path) -> dict:
    try:
        if path.stat().st_size > 65536:
            raise ValueError()
        installed = json.loads(path.read_text(encoding="utf-8-sig"))["installed"]
        if not all(isinstance(installed.get(key), str) and installed[key] for key in ("client_id", "client_secret")):
            raise ValueError()
        if not installed["client_id"].endswith(".apps.googleusercontent.com"):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise UploadFailure("Choose a Google OAuth client JSON downloaded for a Desktop app.") from None
    # Only Google's OAuth endpoints are used, regardless of extra JSON fields.
    return {"installed": {
        "client_id": installed["client_id"], "client_secret": installed["client_secret"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }}


def authorize(config: dict, stopping: threading.Event) -> Credentials:
    flow = InstalledAppFlow.from_client_config(config, SCOPES, autogenerate_code_verifier=True)
    received: dict[str, str] = {}
    expected_state = ""

    class Callback(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(2)
            super().setup()

        def log_message(self, *_args) -> None:
            pass

        def do_GET(self) -> None:
            query = parse_qs(urlsplit(self.path).query)
            state = query.get("state", [""])[0]
            if not secrets.compare_digest(state, expected_state):
                self.send_error(400, "Invalid OAuth state")
                return
            received["code"] = query.get("code", [""])[0]
            received["error"] = query.get("error", [""])[0]
            body = b"Authorization response received. You may close this tab and return to the uploader."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with HTTPServer(("127.0.0.1", 0), Callback) as server:
        server.timeout = 0.5
        flow.redirect_uri = f"http://127.0.0.1:{server.server_port}/"
        url, expected_state = flow.authorization_url(access_type="offline", prompt="consent select_account")
        if not webbrowser.open(url):
            raise UploadFailure("Could not open the browser for Google sign-in.")
        deadline = time.monotonic() + 180
        while not received and not stopping.is_set() and time.monotonic() < deadline:
            server.handle_request()
        if stopping.is_set() or not received.get("code") or received.get("error"):
            raise UploadFailure("YouTube connection was cancelled or timed out. Click Connect to try again.")
        flow.fetch_token(code=received["code"], timeout=20)
        if not flow.credentials.refresh_token:
            raise UploadFailure("Google did not provide offline access. Reconnect and grant consent.")
        return flow.credentials


def youtube_api(credentials: Credentials):
    return build("youtube", "v3", http=AuthorizedHttp(credentials, http=httplib2.Http(timeout=20)),
                 cache_discovery=False, static_discovery=True)


def channel_info(api) -> dict:
    items = api.channels().list(part="snippet", mine=True).execute(num_retries=0).get("items", [])
    if len(items) != 1:
        raise UploadFailure("Select a Google/Brand account with one YouTube channel, then reconnect.")
    return {"id": items[0]["id"], "name": items[0]["snippet"]["title"]}


def list_playlists(api) -> list[dict]:
    items, page = [], None
    while True:
        response = api.playlists().list(part="snippet", mine=True, maxResults=50, pageToken=page).execute(num_retries=0)
        items.extend({"id": item["id"], "name": item["snippet"]["title"]} for item in response.get("items", []))
        page = response.get("nextPageToken")
        if not page:
            return items


@dataclass(frozen=True)
class UploadJob:
    attempt_id: str
    recording_id: int
    path: str
    identity: str
    size: int
    mtime_ns: int
    fingerprint: str
    title: str
    description: str
    privacy: str
    channel_id: str
    playlist_id: str = ""


@contextmanager
def protected_video(job: UploadJob):
    path = Path(job.path)
    version = (job.identity, job.size, job.mtime_ns)
    if not revalidate(job.path, version, job.fingerprint):
        raise UploadFailure("The source file changed or is busy. Review it again before uploading.")
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise UploadFailure("Cannot lock the source file for reading. Wait until recording has finished.")
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except Exception:
            kernel.CloseHandle(handle)
            raise
        try:
            stream = os.fdopen(descriptor, "rb")
        except Exception:
            os.close(descriptor)
            raise
    else:
        stream = path.open("rb")
    with stream:
        if Snapshot.read(path).version != version or sampled_fingerprint(path, job.size) != job.fingerprint:
            raise UploadFailure("The source file changed before upload.")
        yield stream


def upload_video(api, job: UploadJob, stopping: threading.Event, emit) -> dict:
    """Retry only within the same confirmed resumable session; never start a new one implicitly."""
    if channel_info(api)["id"] != job.channel_id:
        raise UploadFailure("The connected channel changed. Review this upload again.")
    if stopping.is_set():
        raise UploadFailure("Upload stopped before sending video data.")
    with protected_video(job) as stream:
        media = MediaIoBaseUpload(stream, mimetype="application/octet-stream", chunksize=8 * 1024 * 1024, resumable=True)
        request = api.videos().insert(part="snippet,status", body={
            "snippet": {"title": job.title, "description": job.description, "categoryId": "22"},
            "status": {"privacyStatus": job.privacy},
        }, media_body=media, notifySubscribers=False)
        retries, sent, started, attempted = 0, 0, time.monotonic(), False
        while True:
            if stopping.is_set():
                raise UploadFailure("Upload interrupted. Check YouTube Studio before starting another attempt.", attempted)
            try:
                if Snapshot.read(Path(job.path)).version != (job.identity, job.size, job.mtime_ns):
                    raise UploadFailure("Source changed during upload. Check YouTube Studio before retrying.", attempted)
                attempted = True
                progress, result = request.next_chunk(num_retries=0)
            except UploadFailure:
                raise
            except Exception as exc:
                transient = (isinstance(exc, (OSError, httplib2.HttpLib2Error))
                             or isinstance(exc, HttpError) and exc.resp.status in {429, 500, 502, 503, 504})
                if transient and request.resumable_uri and retries < 5:
                    retries += 1
                    emit("retry", {"attempt_id": job.attempt_id, "count": retries})
                    stopping.wait(min(2 ** retries, 30))
                    continue
                ambiguous = not isinstance(exc, HttpError) or exc.resp.status >= 500 or bool(request.resumable_uri)
                raise UploadFailure(safe_error(exc), ambiguous) from None
            if progress:
                sent = progress.resumable_progress
                elapsed = max(time.monotonic() - started, 0.001)
                rate = sent / elapsed
                emit("progress", {"attempt_id": job.attempt_id, "recording_id": job.recording_id,
                                  "bytes": sent, "rate": rate, "eta": (job.size - sent) / rate if rate else None})
            if result is not None:
                video_id = result.get("id")
                if not isinstance(video_id, str) or not video_id:
                    raise UploadFailure("YouTube returned no video ID. Check YouTube Studio before retrying.", True)
                outcome = {"attempt_id": job.attempt_id, "recording_id": job.recording_id,
                           "video_id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}", "warning": ""}
                emit("created", outcome.copy())  # Persist success before the optional playlist operation.
                if job.playlist_id and not stopping.is_set():
                    try:
                        api.playlistItems().insert(part="snippet", body={"snippet": {
                            "playlistId": job.playlist_id,
                            "resourceId": {"kind": "youtube#video", "videoId": video_id},
                        }}).execute(num_retries=0)
                    except Exception:
                        outcome["warning"] = "Video uploaded, but playlist placement was not confirmed. Check the playlist manually."
                elif job.playlist_id:
                    outcome["warning"] = "Video uploaded; playlist placement was skipped during shutdown."
                return outcome
