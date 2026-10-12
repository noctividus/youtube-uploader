# vMix YouTube Uploader

A Windows system-tray application that listens to vMix recording state, queues completed recordings for review, uploads approved videos to YouTube, and sends authenticated webhook notifications. Windows startup registration and standalone packaging remain future work.

## Run on Windows

Use Python 3.12 or newer. From PowerShell in this repository:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m vmix_youtube_uploader
```

If `python` opens the Microsoft Store or fails, use the full path to your installed Python for the first command. On the development computer used for validation, that was `C:\Users\Rich\AppData\Local\Python\bin\python.exe`.

Once installed, `.\.venv\Scripts\vmix-youtube-uploader.exe` launches without a console. The app appears in the Windows tray, possibly under the hidden-icons arrow. Right-click the icon to open **Settings**, then configure vMix under **Triggers**. Double-click the icon to open the queue. Closing a window leaves monitoring active; use **Exit** in the tray menu to quit.

## Workspace run/stop tasks

In VS Code, open the Command Palette (**Ctrl+Shift+P**), select **Tasks: Run Task**, then choose:

- **Uploader: Run for testing** to start the tray app.
- **Uploader: Stop testing app** to shut it down cleanly, including its watcher and database.
- **Uploader: Restart testing app** to run Stop followed by Run for quick testing iterations. It also starts the app if it is already stopped.

These tasks use the repository's `.venv` and retain separate test settings/history under `%LOCALAPPDATA%\vMix YouTube Uploader\Development\<workspace-id>`. Configure the vMix endpoint on your first test run. The stop task only targets this workspace's testing instance; a normally launched instance is separate. Use the stop task instead of the terminal's trash/terminate button.

The same controls work from PowerShell:

```powershell
.\.venv\Scripts\python.exe scripts\dev_app.py run
# In another terminal:
.\.venv\Scripts\python.exe scripts\dev_app.py stop
```

## vMix recording workflow (default)

1. In vMix, enable its Web Controller/API under **Settings → Web Controller** (the Web settings tab), and note the HTTP address/port.
2. In this app, open **Settings → Triggers → vMix**. Leave **Use vMix recording events instead of folder monitoring** checked. The default API URL is `http://127.0.0.1:8088/api/`, polled every 2 seconds. Use **Test connection**, then Save.
3. Start recording in vMix. On the next successful poll, the primary recording is added to the queue as **Recording (vMix)**, even if the file has not appeared on disk yet.
4. Stop recording in vMix. A confirmed recording-to-idle transition opens that recording's upload window. There is no file-stability delay. If Windows still has the file locked, the window remains open and reports that it is waiting for access; it updates when the file is readable.

vMix mode does not watch folders, scan for unrelated videos, or apply the extension filter. It uses the primary recorder's `filename1` attribute (or `filename` when provided) in the API XML, captures it while recording, and retains it when the idle response no longer includes it. If no absolute filename is supplied, the recording is still queued but reports the missing filename; it never guesses the latest file from a directory. The path must be accessible on this computer. Remote vMix installations need accessible paths such as a shared UNC path; automatic drive/path mapping is not implemented.

The public [vMix HTTP API documentation](https://www.vmix.com/help29/DeveloperAPI.html) describes read-only XML state retrieval but does not guarantee filename attributes in its example. Check Test connection and a real recording with your vMix version. Requests contain no function parameters and never start or stop recording remotely.

The integration tracks the primary recorder, including file segments whose names change between polls. Each observed segment gets its own window after recording stops. Polling can miss entire recordings or segments shorter than the poll interval. Secondary recording and MultiCorder tracking are not implemented.

Connection loss never counts as a stop. An interrupted recording remains unconfirmed unless the app reconnects while that same recording is active and subsequently observes its stop. Starting the app while vMix is already recording attaches to that active recording. Starting while vMix is idle does not pop up old recordings. Confirmed stopped entries survive restarts without automatically reopening their windows.

## Connect YouTube and upload

1. In [Google Cloud Console](https://console.cloud.google.com/), create or select your project and enable **YouTube Data API v3**.
2. Configure the Google Auth Platform consent screen (branding, audience, and data access). For a personal project in Testing, add your Google account as a test user. This app requests `youtube.force-ssl`, which covers uploads, channel information, and optional playlist placement.
3. Create an OAuth client with application type **Desktop app** and download its JSON. Keep the downloaded file outside the repository. A web client or API key will not work. See Google's [desktop OAuth instructions](https://developers.google.com/identity/protocols/oauth2/native-app).
4. Restart the testing app after installing the updated dependencies. Open **Settings → YouTube**, select **Import OAuth client JSON**, then **Connect / Refresh channel**. Complete Google's consent flow in your browser and select the correct channel/Brand account. Sign-in times out after three minutes; closing the app cancels it. Confirm the channel name and ID shown in Settings.
5. Select a ready recording and click **Upload**. Confirm its destination channel, title, description, privacy (Private initially), and optional playlist, then click **Upload** in the confirmation window. One transfer runs at a time; other recordings remain available for later review. Detection continues while uploading.
6. Watch progress in the queue. A successful upload saves its YouTube link; double-click that link to open it. Success means YouTube accepted the video, not that video processing has finished. A playlist failure preserves the successful upload and displays a warning.

Account changes take effect immediately, independently of the Settings Save/Cancel buttons. Client configuration and refresh credentials are stored in **Windows Credential Manager**, isolated by application data directory. They are not written to settings, SQLite, or logs. **Disconnect** removes this app's locally stored account credentials; it does not revoke Google's consent. To revoke consent, remove access in your Google account's connected-app settings. Reconnect to refresh the playlist list after restarting the app.

Google testing-mode grants can expire and require reconnection. Videos uploaded through unverified API projects may be restricted to Private, regardless of the selected visibility; see [YouTube's videos.insert restrictions](https://developers.google.com/youtube/v3/docs/videos/insert). Public use may require Google's OAuth verification and YouTube API compliance audit.

Transient failures retry only within the same [resumable upload session](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol). This version does not persist session URLs across restarts. An interrupted or uncertain upload becomes Failed and requires checking YouTube Studio plus explicitly acknowledging duplicate risk before another attempt. It never silently creates a replacement upload. Source files remain untouched and are held against writes/deletion during transfer on Windows.

## Filesystem workflow (optional fallback)

Disable the vMix trigger to use the previous folder-based workflow. Settings is organized into **Files** (folders and extension filter), **Triggers** (Stability Trigger and vMix), **Webhooks**, **General** (notifications and default description), and **YouTube**.

- Watches files directly inside each selected folder, without recursing into subfolders. **File Extension Filter** in Settings selects which extensions to discover (case insensitive), with MP4, MKV, MOV, and AVI selected by default. Selections persist across restarts. Unchecking all stops new file discovery; existing queue entries remain tracked.
- Scans run at startup, when the queue window opens, and after watched folders are saved. Watchdog events trigger discovery, with periodic scans every 30 seconds by default to recover missed events. While monitoring is paused, scans wait until resume.
- Each nonempty file must have unchanged size and modification time for 60 seconds by default and pass a Windows exclusive-read check. Detection runs in a background worker.
- Files already present at startup or when a folder is first added populate the queue quietly. New files arriving in a watched folder automatically open for review after completion checks. Subsequent content changes in watched files can also trigger review; unchanged files never retrigger just because the queue opens. Automatic windows request no activation; pending reviews wait while the Windows desktop is locked.
- The queue has a **Settings** shortcut at the top right and an **Upload** button that opens upload confirmation for any selected file. The confirmation shows readiness and updates as checks finish without discarding metadata edits. Connect a channel under **YouTube** in Settings to enable transfer of ready files. All column headers sort, including Directory, numeric Size, and Progress. Sorting and refresh preserve the selected file. Double-clicking a row does not open confirmation.
- Review lets you edit title, description, and privacy (initially Private). **Later** saves the metadata and defers attention. **Skip** suppresses future review of that unchanged recording. Upload always requires explicit confirmation.
- Skipped items remain visible as history; restoring them is not yet implemented. The queue has no remove, open-folder, scan, or close buttons; close it with the window's title-bar control.
- Renames within watched folders retain the file's state when Windows exposes a stable file identity. A different file replacing a path becomes a new item.
- Ready files are checked again after restart. Deferred items remain deferred, and unchanged skipped/uploaded items do not become new reviews. Approval is never inferred from a restart.
- **Pause Monitoring** suspends discovery/readiness checks; resuming starts a fresh stability observation. Removing a watched folder stops discovering new files there; records already in the queue continue to be checked.

File stability is practical evidence, not proof that a producer will never reopen a file. Use a suitable stability timeout for your recording/copy workflow in filesystem mode. Neither mode inspects video container integrity. Review actions revalidate identity, size, timestamp, sampled content, and file accessibility.

## Webhooks and header authentication

In **Settings → Webhooks**, click **Add**, enter the endpoint, and select events. Use **Add authentication header** for any headers your receiver requires, for example name `Authorization` with value `Bearer your-token`, or name `X-API-Key` with your key as the value. Values are masked and encrypted in settings and pending deliveries using [Windows user-bound DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata). Save applies changes; Cancel discards them. Use HTTPS for remote destinations. Endpoint URLs remain in local settings, so put authentication in headers rather than URL parameters.

Events are JSON HTTP POST request bodies sent to your endpoint. The examples below use illustrative IDs and timestamps. Every event includes `id` (event UUID, unchanged on retries), `event`, `version` (currently `1`), `occurred_at` (UTC time when the app queued the event), and `data`.

When present, `data.recording` contains the local queue ID, filename (without the directory), last observed size in bytes, title, and source (`vmix` or `filesystem`). At recording start, the filename may be empty and size may be zero. At vMix stop, the file may still be finishing, so the reported size is not guaranteed to be final. Fields shown only for a particular event are absent from other events, rather than set to `null`.

### Recording Started — vMix

Sent when the app observes a recording start, including attachment to an active recording on startup. `observed: true` indicates an observed vMix state; it does not distinguish a new start from startup attachment.

```json
{
  "id": "11111111-1111-4111-8111-111111111111",
  "event": "recording_started",
  "version": 1,
  "occurred_at": "2026-10-12T02:00:00+00:00",
  "data": {
    "observed": true,
    "recording": {"id": 1, "filename": "recording.mp4", "size": 0, "title": "Recording", "source": "vmix"}
  }
}
```

### Recording Started — filesystem fallback

Sent for a newly observed or changed watched file. `evidence` replaces the vMix-only `observed` field. This detects a file, not the producer's actual recording-start time. Existing files discovered during the initial scan do not generate this event.

```json
{
  "id": "22222222-2222-4222-8222-222222222222",
  "event": "recording_started",
  "version": 1,
  "occurred_at": "2026-10-12T02:00:00+00:00",
  "data": {
    "evidence": "new_file_observed",
    "recording": {"id": 2, "filename": "recording.mp4", "size": 4096, "title": "Recording", "source": "filesystem"}
  }
}
```

### Recording Finished — vMix

Sent on a confirmed vMix stop for each tracked recording segment. Connection loss never counts as a stop. This event does not include `observed` or `evidence`.

```json
{
  "id": "33333333-3333-4333-8333-333333333333",
  "event": "recording_finished",
  "version": 1,
  "occurred_at": "2026-10-12T02:10:00+00:00",
  "data": {
    "recording": {"id": 1, "filename": "recording.mp4", "size": 12345, "title": "Recording", "source": "vmix"}
  }
}
```

### Recording Finished — filesystem fallback

Sent when a new/changed watched file passes stability and access checks. Existing files discovered during the initial scan do not generate this event.

```json
{
  "id": "44444444-4444-4444-8444-444444444444",
  "event": "recording_finished",
  "version": 1,
  "occurred_at": "2026-10-12T02:11:00+00:00",
  "data": {
    "evidence": "file_stability",
    "recording": {"id": 2, "filename": "recording.mp4", "size": 12345, "title": "Recording", "source": "filesystem"}
  }
}
```

### Upload Started

Sent when an approved upload attempt starts, before transfer finishes. `attempt_id` identifies the upload attempt; `channel_id` is its destination channel. `privacy` is the requested visibility: `private`, `unlisted`, or `public`.

```json
{
  "id": "55555555-5555-4555-8555-555555555555",
  "event": "upload_started",
  "version": 1,
  "occurred_at": "2026-10-12T02:12:00+00:00",
  "data": {
    "attempt_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "channel_id": "UC_example_channel_id",
    "privacy": "private",
    "recording": {"id": 1, "filename": "recording.mp4", "size": 12345, "title": "Recording", "source": "vmix"}
  }
}
```

### Upload Finished

Sent when YouTube returns the video ID, before optional playlist placement. It confirms upload acceptance, not completion of YouTube's video processing. Playlist failures do not repeat this event or turn it into a failed video upload. The payload does not include channel, privacy, or playlist fields.

```json
{
  "id": "66666666-6666-4666-8666-666666666666",
  "event": "upload_finished",
  "version": 1,
  "occurred_at": "2026-10-12T02:13:00+00:00",
  "data": {
    "recording": {"id": 1, "filename": "recording.mp4", "size": 12345, "title": "Recording", "source": "vmix"},
    "attempt_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "youtube_id": "video-id",
    "youtube_url": "https://www.youtube.com/watch?v=video-id"
  }
}
```

### Error — upload attempt

An upload-attempt failure includes `operation: upload`, `attempt_id`, `recording`, a sanitized `message`, and `ambiguous`. When `ambiguous` is true, the outcome is uncertain and the user must check YouTube Studio before retrying; false means the app did not classify the outcome as uncertain.

```json
{
  "id": "77777777-7777-4777-8777-777777777777",
  "event": "error",
  "version": 1,
  "occurred_at": "2026-10-12T02:13:00+00:00",
  "data": {
    "operation": "upload",
    "attempt_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "message": "YouTube API quota exceeded. Try again after the quota resets.",
    "ambiguous": false,
    "recording": {"id": 1, "filename": "recording.mp4", "size": 12345, "title": "Recording", "source": "vmix"}
  }
}
```

### Error — connection, monitoring, or application operation

Other errors contain only `operation` and `message` inside `data`. They omit `recording`, `attempt_id`, and `ambiguous`. An upload command rejected before an attempt starts also uses this smaller shape, with `operation: upload`.

```json
{
  "id": "88888888-8888-4888-8888-888888888888",
  "event": "error",
  "version": 1,
  "occurred_at": "2026-10-12T02:14:00+00:00",
  "data": {
    "operation": "vmix",
    "message": "vMix connection failed; retrying."
  }
}
```

| Error origin | `data.operation` | `data.message` |
| --- | --- | --- |
| vMix polling failure | `vmix` | `vMix connection failed; retrying.` |
| Watched-folder failure | `filesystem` | `A watched folder is unavailable.` |
| Monitoring worker stopped | `monitor` | `Monitoring stopped; check the app.` |
| YouTube sign-in failure | `youtube_connect` | Sanitized explanation of the connection failure |
| OAuth client import failure | `youtube_import` | Sanitized explanation of the import failure |
| YouTube disconnect failure | `youtube_disconnect` | Sanitized explanation of the disconnect failure |
| Failed worker command | Command name, such as `upload`, `review`, `settings`, `youtube`, `pause`, `remove`, `scan`, `vmix_test`, or `youtube_event` | `Application operation failed; check the app.` |

Error messages are human-readable and may change; route automation using `event`, `operation`, and the presence of attempt fields. Repeated outage polls are suppressed. Webhook delivery failures are shown locally and never trigger recursive Error calls.

### HTTP headers and receiver responses

Every request includes `Content-Type: application/json`, `X-Webhook-Id`, and `X-Webhook-Event`, plus your configured authentication headers. For example:

```http
Content-Type: application/json
X-Webhook-Id: 66666666-6666-4666-8666-666666666666
X-Webhook-Event: upload_finished
Authorization: Bearer your-token
```

Your endpoint can acknowledge a delivery with `204 No Content` or any other 2xx response. No JSON response body is required; response bodies are not read.

`X-Webhook-Id` and `X-Webhook-Event` mirror the JSON fields. A 2xx response is success. Network errors, HTTP 408/429 and 5xx responses get at most three attempts, with 2- and 4-second delays. Other responses fail without retries. Redirects are not followed. Receivers should deduplicate by event ID because timeouts or a crash after delivery can produce duplicate requests.

Deliveries are saved in `webhooks.sqlite3` and resume after restart. They retain the endpoint and encrypted headers configured when the event occurred; subsequent settings edits/removal apply to new events. Finished delivery records are retained for up to 30 days and cleaned up on startup. Terminal failures are not automatically replayed. Latest delivery status appears in the Webhooks tab; logs contain delivery IDs and HTTP status, without authentication values, endpoint URLs or response bodies. Delivery runs independently of recording and upload processing.

## Settings, state, and logs

The default data directory is Qt's per-user `AppLocalDataLocation`, normally:

```text
%LOCALAPPDATA%\vMix YouTube Uploader\vMix YouTube Uploader\
  settings.json
  queue.sqlite3
  logs\uploader.log
```

SQLite sidecars and an instance lock may also appear there. Logs contain JSON records and rotate at 2 MB, keeping five backups. **View Logs** opens the current log. Only one process may use a given data directory.

For isolated experiments, choose a separate state directory:

```powershell
.\.venv\Scripts\python.exe -m vmix_youtube_uploader --data-dir "$env:TEMP\vmix-uploader-demo"
```

An invalid settings file is reported and preserved until you explicitly save replacement settings. A disconnected folder reports an error while other folders continue scanning. Keep periodic scanning enabled to recover missed events or disconnected paths.

## Tests and verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

The suite uses temporary folders/databases and requires no Google account or vMix instance. It covers completion timing, growing and missing files, duplicate events, rename/replacement, durable Later/Skip/removal, stale review rejection, restart recovery, settings validation, real watchdog discovery, UI review behavior, and single-instance locking. On Windows it also tests real exclusive-access behavior and briefly starts/exits the real tray application with isolated data and notifications disabled. The remaining UI tests use Qt's offscreen plugin.

vMix interactive acceptance checks:

1. Test the connection, then start a recording and check its queue entry and filename.
2. Stop recording and check that its upload window opens without the stability delay.
3. Confirm that unrelated files copied into old watched folders do not trigger a review while vMix mode is enabled.
4. Disconnect the vMix endpoint during a recording. Confirm the row reports an unconfirmed stop and no upload window opens solely because of the disconnect.
5. Reconnect while still recording, then stop. Check recovery and the correct filename. Repeat with app restart during recording and with split recording files.

Filesystem interactive acceptance checks (disable vMix first):

1. Start the app, configure a disposable recordings folder, and temporarily set stability to 5 seconds.
2. Copy several sample videos into it. Confirm each stays pending while changing, then becomes ready once stable and accessible.
3. Edit a title and select Later for one file; Skip another. Restart and verify both choices persist, with no overlapping review windows.
4. Rename a skipped file within the folder. Verify it stays skipped. Replace a file with different contents and verify it returns for review.
5. Sort the queue by each header, select a ready file, and click Upload to open its confirmation. Check the Settings shortcut. Close all windows and confirm the tray stays active. Launch a second instance and confirm it is refused.
6. Pause/resume, temporarily disconnect a folder, lock/unlock Windows, and sleep/wake. Check recovery and confirm review windows do not steal focus from vMix or other applications.
7. Exit through the tray and confirm the process terminates. Restore the 60-second stability default after testing.

Automated checks run on Windows with Python 3.14.5, including simulated vMix transitions, database migrations, mocked YouTube transfers, upload recovery, and real Windows source-file locks. Live Google authorization and an actual private test upload still require your account and consent. Verify the selected channel, successful private upload/link, playlist placement, and reconnect behavior using a small disposable video. No live vMix instance was reachable at the default endpoint during development. Focus behavior during production, actual lock/sleep transitions, and long-running operation also need human verification.

## Project references

- [Full project brief](docs/project-brief.md)
- [Implementation milestones](docs/implementation-plan.md)
- [Architecture and recovery decisions](docs/architecture.md)
- [Contributor instructions](AGENTS.md)
