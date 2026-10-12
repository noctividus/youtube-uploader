"""Serial network worker; all database changes return to the owning Qt worker."""

import queue
import threading
from pathlib import Path

from google.oauth2.credentials import Credentials

from .youtube import (CredentialStore, UploadFailure, authorize, channel_info,
                      list_playlists, read_client_config, safe_error, upload_video, youtube_api)


class YouTubeService(threading.Thread):
    def __init__(self, data_dir: Path, emit):
        super().__init__(name="youtube")
        self.data_dir, self.emit = data_dir, emit
        self.commands = queue.Queue()
        self.stopping = threading.Event()
        self.state = {"connected": False, "imported": False, "busy": False, "message": "Not connected"}
        self.store = None
        self.account = None

    def submit(self, command, payload=None):
        self.commands.put((command, payload))

    def publish(self, **changes):
        self.state.update(changes)
        self.emit("account", self.state.copy())

    def run(self):
        try:
            self.store = CredentialStore(self.data_dir)
            self.account = self.store.get("account")
            self.publish(imported=bool(self.store.get("client")), connected=bool(self.account),
                         message="Connected" if self.account else "Import an OAuth client and connect your channel.",
                         **(self.account.get("profile", {}) if self.account else {}))
        except Exception as exc:
            self.publish(message=safe_error(exc))
        while not self.stopping.is_set():
            try:
                command, payload = self.commands.get(timeout=0.2)
            except queue.Empty:
                continue
            api = None
            self.publish(busy=True, message="Uploading…" if command == "upload" else "Connecting…")
            try:
                if self.store is None:
                    raise UploadFailure("Windows Credential Manager is unavailable.")
                if command == "import":
                    config = read_client_config(Path(payload))
                    self.store.put("client", config)
                    self.store.delete("account")
                    self.account = None
                    self.publish(imported=True, connected=False, channel_id="", channel_name="", playlists=[],
                                 message="Desktop OAuth client imported. Click Connect.")
                elif command == "disconnect":
                    self.store.delete("account")
                    self.account = None
                    self.publish(connected=False, channel_id="", channel_name="", playlists=[], message="Disconnected")
                elif command == "connect":
                    config = self.store.get("client")
                    if not config:
                        raise UploadFailure("Import a Desktop OAuth client JSON first.")
                    credentials = authorize(config, self.stopping)
                    api = youtube_api(credentials)
                    channel = channel_info(api)
                    message = "Connected"
                    try:
                        playlists = list_playlists(api)
                    except Exception:
                        playlists = []
                        message = "Connected. Playlists could not be loaded; reconnect to refresh them."
                    profile = {"channel_id": channel["id"], "channel_name": channel["name"], "playlists": playlists}
                    # Store only the refresh credential; short-lived access/ID tokens stay in memory.
                    account = {"credentials": {key: getattr(credentials, key) for key in
                               ("refresh_token", "client_id", "client_secret", "token_uri", "scopes")},
                               "profile": {"channel_id": channel["id"], "channel_name": channel["name"]}}
                    self.store.put("account", account)
                    self.account = account
                    self.publish(connected=True, message=message, **profile)
                elif command == "upload":
                    if not self.account:
                        raise UploadFailure("Connect a YouTube channel in Settings first.")
                    api = youtube_api(Credentials.from_authorized_user_info(self.account["credentials"]))
                    outcome = upload_video(api, payload, self.stopping, self.emit)
                    self.emit("done", outcome)
                    self.publish(message=outcome.get("warning") or "Upload finished. Connected.")
            except Exception as exc:
                message = safe_error(exc)
                if command == "upload":
                    self.emit("failed", {"attempt_id": payload.attempt_id, "recording_id": payload.recording_id,
                                         "error": message, "ambiguous": getattr(exc, "ambiguous", False)})
                else:
                    self.emit("operation_error", {"operation": "youtube_" + command, "message": message})
                self.publish(message=message)
            finally:
                if api:
                    try:
                        api.close()
                    except Exception:
                        pass
                self.publish(busy=False)
