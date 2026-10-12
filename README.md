# vMix YouTube Uploader

A Windows system-tray application that listens to vMix recording state and retains an upload review queue. The local queue workflow and vMix integration are implemented. YouTube authentication/uploads, webhook delivery, Windows startup registration, and standalone packaging remain future work.

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

## Filesystem workflow (optional fallback)

Disable the vMix trigger to use the previous folder-based workflow. Settings is organized into **Files** (folders and extension filter), **Triggers** (Stability Trigger and vMix), **Webhooks**, and **General** (notifications and default description). In Webhooks, use **Add** to enter an HTTP/HTTPS endpoint and select Recording Started, Recording Finished, Upload Started, Upload Finished, and/or Error. Endpoints can be edited or removed; Save persists them, and Cancel discards edits. Webhook configuration is available now; delivery is not yet implemented, so no outbound webhook calls are made.

- Watches files directly inside each selected folder, without recursing into subfolders. **File Extension Filter** in Settings selects which extensions to discover (case insensitive), with MP4, MKV, MOV, and AVI selected by default. Selections persist across restarts. Unchecking all stops new file discovery; existing queue entries remain tracked.
- Scans run at startup, when the queue window opens, and after watched folders are saved. Watchdog events trigger discovery, with periodic scans every 30 seconds by default to recover missed events. While monitoring is paused, scans wait until resume.
- Each nonempty file must have unchanged size and modification time for 60 seconds by default and pass a Windows exclusive-read check. Detection runs in a background worker.
- Files already present at startup or when a folder is first added populate the queue quietly. New files arriving in a watched folder automatically open for review after completion checks. Subsequent content changes in watched files can also trigger review; unchanged files never retrigger just because the queue opens. Automatic windows request no activation; pending reviews wait while the Windows desktop is locked.
- The queue has a **Settings** shortcut at the top right and an **Upload** button that opens upload confirmation for any selected file. The confirmation shows readiness and updates as checks finish without discarding metadata edits. Actual YouTube transfer is not enabled yet. All column headers sort, including Directory, numeric Size, and Progress. Sorting and refresh preserve the selected file. Double-clicking a row does not open confirmation.
- Review lets you edit title, description, and privacy (initially Private). **Later** saves the metadata and defers attention. **Skip** suppresses future review of that unchanged recording. Upload is explicitly disabled in this milestone.
- Skipped items remain visible as history; restoring them is not yet implemented. The queue has no remove, open-folder, scan, or close buttons; close it with the window's title-bar control.
- Renames within watched folders retain the file's state when Windows exposes a stable file identity. A different file replacing a path becomes a new item.
- Ready files are checked again after restart. Deferred items remain deferred, and unchanged skipped/uploaded items do not become new reviews. Approval is never inferred from a restart.
- **Pause Monitoring** suspends discovery/readiness checks; resuming starts a fresh stability observation. Removing a watched folder stops discovering new files there; records already in the queue continue to be checked.

File stability is practical evidence, not proof that a producer will never reopen a file. Use a suitable stability timeout for your recording/copy workflow in filesystem mode. Neither mode inspects video container integrity. Review actions revalidate identity, size, timestamp, sampled content, and file accessibility.

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

Automated checks were run on Windows with Python 3.14.5, including simulated vMix XML, start/stop transitions, disconnection, delayed files, split recordings, settings, and migration of existing queue databases. No live vMix instance was reachable at the default endpoint during development, so the real vMix workflow still needs the interactive checks above. Focus behavior during production, actual lock/sleep transitions, and long-running operation also need human verification. YouTube uploads remain unimplemented.

## Project references

- [Full project brief](docs/project-brief.md)
- [Implementation milestones](docs/implementation-plan.md)
- [Architecture and recovery decisions](docs/architecture.md)
- [Contributor instructions](AGENTS.md)
