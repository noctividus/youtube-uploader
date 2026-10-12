# vMix YouTube Uploader

A Windows system-tray application for detecting completed recordings and retaining a review queue. **Milestone 1 is implemented.** YouTube authentication/uploads, vMix integration, Windows startup registration, and standalone packaging are future milestones.

## Run on Windows

Use Python 3.12 or newer. From PowerShell in this repository:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m vmix_youtube_uploader
```

If `python` opens the Microsoft Store or fails, use the full path to your installed Python for the first command. On the development computer used for validation, that was `C:\Users\Rich\AppData\Local\Python\bin\python.exe`.

Once installed, `.\.venv\Scripts\vmix-youtube-uploader.exe` launches without a console. The app appears in the Windows tray, possibly under the hidden-icons arrow. Right-click the icon to open **Settings**, add recording folders, and save. Double-click the icon to open the queue. Closing a window leaves monitoring active; use **Exit** in the tray menu to quit.

## Workspace run/stop tasks

In VS Code, open the Command Palette (**Ctrl+Shift+P**), select **Tasks: Run Task**, then choose:

- **Uploader: Run for testing** to start the tray app.
- **Uploader: Stop testing app** to shut it down cleanly, including its watcher and database.

These tasks use the repository's `.venv` and retain separate test settings/history under `%LOCALAPPDATA%\vMix YouTube Uploader\Development\<workspace-id>`. Configure a watch folder on your first test run. The stop task only targets this workspace's testing instance; a normally launched instance is separate. Use the stop task instead of the terminal's trash/terminate button.

The same controls work from PowerShell:

```powershell
.\.venv\Scripts\python.exe scripts\dev_app.py run
# In another terminal:
.\.venv\Scripts\python.exe scripts\dev_app.py stop
```

## Current workflow

- Watches files directly inside each selected folder, without recursing into subfolders. **File Extension Filter** in Settings selects which extensions to discover (case insensitive), with MP4, MKV, MOV, and AVI selected by default. Selections persist across restarts. Unchecking all stops new file discovery; existing queue entries remain tracked.
- Watchdog events trigger discovery, with periodic scans every 30 seconds by default to recover missed events. Existing files are also scanned at startup.
- Each nonempty file must have unchanged size and modification time for 60 seconds by default and pass a Windows exclusive-read check. Detection runs in a background worker.
- Completed files appear in a persistent review queue and one nonmodal review window at a time. Automatic windows request no activation; pending reviews wait while the Windows desktop is locked.
- Review lets you edit title, description, and privacy (initially Private). **Later** saves the metadata and defers attention. **Skip** suppresses future review of that unchanged recording. Upload is explicitly disabled in this milestone.
- **Remove from queue** hides an item and suppresses rediscovery of its unchanged contents. It never deletes the source file. Changed contents return for review. Skipped items remain visible as history; restoring them is not yet implemented.
- Renames within watched folders retain the file's state when Windows exposes a stable file identity. A different file replacing a path becomes a new item.
- Ready files are checked again after restart. Deferred items remain deferred, and unchanged skipped/uploaded items do not become new reviews. Approval is never inferred from a restart.
- **Pause Monitoring** suspends discovery/readiness checks; resuming starts a fresh stability observation. Removing a watched folder stops discovering new files there; records already in the queue continue to be checked.

File stability is practical evidence, not proof that a producer will never reopen a file. Use a suitable stability timeout for your recording/copy workflow. This milestone does not inspect video container integrity or consult vMix recording state. Review actions revalidate identity, size, timestamp, sampled content, and file accessibility.

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

Interactive acceptance checks:

1. Start the app, configure a disposable recordings folder, and temporarily set stability to 5 seconds.
2. Copy several sample videos into it. Confirm each stays pending while changing, then becomes ready once stable and accessible.
3. Edit a title and select Later for one file; Skip another. Restart and verify both choices persist, with no overlapping review windows.
4. Rename a skipped file within the folder. Verify it stays skipped. Replace a file with different contents and verify it returns for review.
5. Remove a pending item and confirm the video still exists. Close all windows and confirm the tray stays active. Launch a second instance and confirm it is refused.
6. Pause/resume, temporarily disconnect a folder, lock/unlock Windows, and sleep/wake. Check recovery and confirm review windows do not steal focus from vMix or other applications.
7. Exit through the tray and confirm the process terminates. Restore the 60-second stability default after testing.

Automated checks were run on Windows with Python 3.14.5. Human verification of focus behavior during production, actual lock/sleep transitions, and long-running operation is still needed. No real vMix or YouTube workflow has been verified or implemented yet.

## Project references

- [Full project brief](docs/project-brief.md)
- [Implementation milestones](docs/implementation-plan.md)
- [Architecture and recovery decisions](docs/architecture.md)
- [Contributor instructions](AGENTS.md)
