# Project: vMix YouTube Uploader

Build a complete, functional Windows desktop application in Python that automatically detects completed video recordings, presents an upload confirmation dialog, and uploads approved videos to YouTube.

The application should run primarily in the Windows system tray with no persistent main window. It should be reliable, unobtrusive, and suitable for daily use on a computer running vMix production software.

**Build a working MVP, not just a prototype or project skeleton.** Implement the code, create the necessary project files, and provide instructions for running and packaging the application.

## 1. Technology Stack

- Python 3.12+
- PySide6 for the Windows GUI, system tray, dialogs, and signals
- watchdog for filesystem monitoring
- requests for vMix HTTP API communication
- google-api-python-client for YouTube Data API v3
- google-auth-oauthlib and google-auth-httplib2 for OAuth authentication
- SQLite for persistent upload tracking
- PyInstaller for creating a standalone Windows executable
- pytest for automated testing

Use type hints, modular architecture, and clear logging.

Avoid unnecessary frameworks, web servers, or external dependencies.

## 2. Windows System Tray

The application should launch directly into the Windows system tray.

Use QSystemTrayIcon with an appropriate icon and context menu.

The tray menu should include:

- Status (Idle / Recording / Uploading / Error)
- Open Upload Queue
- Upload File Manually
- Settings
- Pause Monitoring / Resume Monitoring
- View Logs
- Exit

Double-clicking the icon should open the upload queue.

The application should support starting automatically when the user logs into Windows, configurable in Settings.

Ensure only one instance of the application can run at a time.

Closing configuration windows should not exit the application.

## 3. vMix Integration

vMix exposes an HTTP API, typically:

http://127.0.0.1:8088/api/

Implement a vMix monitoring service that polls the API approximately every two seconds.

Parse the returned XML to determine:

- Whether vMix is connected
- Whether vMix is recording
- Whether vMix is streaming

Detect recording state transitions:

- Not Recording → Recording
- Recording → Not Recording

When recording stops, initiate a scan of the configured recording directory to find newly completed video files.

Do not assume the vMix API provides the exact recording filename. Associate recordings with files through timestamps, path monitoring, and file activity.

Support scenarios where vMix is disconnected, restarted, or temporarily unreachable.

Display the vMix connection status in the application's settings and status window.

The vMix API URL must be configurable.

This integration should be optional so the application can work without vMix.

Do not remotely start or stop vMix recordings in the initial version.

## 4. Directory Monitoring

Monitor one or more configurable directories.

Supported extensions:

- .mp4
- .mkv
- .mov
- .avi

Use watchdog for filesystem events, supplemented by periodic directory scans to catch missed events.

Detect newly created video files and determine when they are safe to upload.

A file should not be considered ready just because it appears in a directory.

Implement file completion detection using:

1. File size stability over a configurable period, default 60 seconds.
2. Successful exclusive-access checks on Windows where practical.
3. vMix recording state when available.
4. A final verification that the file still exists and remains unchanged immediately before uploading.

Avoid uploading files that are still being recorded, copied, or processed.

At startup, scan existing files and reconcile them with the database. Previously known uploaded or skipped files should not trigger new upload dialogs.

Newly discovered existing files should be queued for review, not automatically uploaded.

Support multiple recordings without losing events.

## 5. Upload Confirmation Window

When a completed recording is detected, display a nonmodal upload confirmation window.

The window should show:

- Video filename
- Full file path
- File size
- Video duration, if readily available
- Date and time recorded
- Editable YouTube title
- Editable YouTube description
- YouTube privacy setting: Private, Unlisted, Public
- Optional playlist selection
- Upload, Later, and Skip buttons

Default privacy must be Private.

The title should initially be generated from the filename, but the user must be able to edit it.

The description should be prepopulated from a configurable template.

Example default description:

"Recorded at the Adventurers' Club of Los Angeles."

Buttons:

**Upload:** Save metadata and initiate the YouTube upload.

**Later:** Keep the recording in the upload queue for future approval.

**Skip:** Mark the recording as skipped. Do not upload it automatically.

Never automatically upload or publish a video without explicit approval in the initial version.

If multiple recordings finish while the confirmation window is open, queue them individually rather than opening numerous overlapping dialogs.

If Windows is locked or the application cannot display the dialog, retain the recording in the queue and provide a notification when the user returns.

## 6. YouTube Integration

Use YouTube Data API v3 with OAuth 2.0 desktop application authentication.

Provide a settings interface allowing users to:

- Import a Google OAuth client credentials JSON file
- Connect their YouTube account
- Disconnect the account
- See the connected channel name
- Select default upload privacy
- Set a default description
- Configure an optional playlist

Store OAuth credentials securely using Windows-compatible storage, preferably Windows Credential Manager via keyring.

Do not hardcode client secrets.

Use resumable uploads suitable for large video files, potentially several gigabytes.

Upload processing must run in a background worker without blocking the GUI.

Provide:

- Upload progress percentage
- Bytes transferred
- Current transfer rate
- Estimated remaining time
- Error messages
- Successful upload confirmation
- YouTube video ID and URL

Implement retry handling with exponential backoff for transient network failures and appropriate handling of YouTube API errors.

Persist upload state in SQLite.

Where practical, preserve resumable upload session state so an interrupted upload can continue after application restart. If exact session recovery is not possible, recover the queue safely and require confirmation before starting a new upload attempt that could create a duplicate.

Do not automatically retry ambiguous upload results where YouTube may already have created the video.

Do not delete original video files after uploading.

## 7. Upload Queue

Implement an Upload Queue window using PySide6.

Display recordings in a table with:

- Filename
- Date detected
- File size
- Status
- Upload progress
- YouTube link, when available

Possible statuses:

- Detecting
- Waiting for File Completion
- Ready for Review
- Approved / Queued
- Uploading
- Uploaded
- Failed
- Skipped

Users should be able to:

- Review and approve a pending upload
- Edit upload metadata before upload
- Retry failed uploads
- Remove a pending item from the queue without deleting the video
- Open the source file location in Windows Explorer
- Open successfully uploaded videos in a browser

Process uploads sequentially by default to avoid saturating the network.

## 8. Persistent Database

Use SQLite to track files and uploads.

At minimum, store:

- Unique record ID
- Full file path
- File size
- File modification timestamp
- File identity/fingerprint sufficient to prevent duplicate processing
- Detection timestamp
- Recording completion timestamp, if known
- Upload approval state
- YouTube title
- YouTube description
- Privacy status
- Upload status
- Bytes uploaded / last known progress
- YouTube video ID
- YouTube URL
- Error details
- Retry count
- Created and updated timestamps

Use appropriate database constraints and transactions.

Do not rely only on filenames to identify recordings.

On application startup, reconcile database state with actual files and safely recover interrupted operations.

## 9. Settings Window

Create a clean, simple settings interface with these sections:

**General**
- Start with Windows
- Start minimized to tray
- Show Windows notifications

**vMix**
- Enable/disable integration
- API URL
- Polling interval
- Connection test button
- Current connection and recording status

**Directories**
- Add/remove watched directories
- File stability timeout
- Enable/disable periodic scanning

**YouTube**
- OAuth credentials
- Connect/disconnect account
- Connected channel information
- Default privacy
- Default description
- Optional playlist

**Uploads**
- Sequential uploads
- Retry behavior
- Upload history

Store settings under the user's Windows application data directory, not alongside the executable.

## 10. Notifications

Use Windows-compatible notifications through PySide6 or an appropriate lightweight library.

Notify the user when:

- A new recording is ready for review
- An upload starts
- An upload succeeds
- An upload fails

Notifications should be configurable.

The application should never steal focus while the user is actively using other software unless explicitly configured to do so.

The approval window may appear as a normal, attention-requesting application window; do not forcibly keep it always on top.

## 11. Error Handling and Reliability

This application may run continuously for days or weeks.

Design for:

- vMix restarting
- Temporary loss of internet connectivity
- YouTube OAuth token expiration
- Files disappearing or being renamed
- Windows sleep and wake
- Application restart during an upload
- Multiple recordings arriving close together
- Large recordings
- Missing or invalid configuration
- YouTube quota and authorization errors

Use structured logging with rotating log files.

Keep network operations, file scanning, and uploads off the GUI thread.

Use Qt signals/slots for thread-safe UI updates.

Handle shutdown gracefully without corrupting the upload database.

## 12. Architecture

Organize the project into clear modules, approximately:

vmix_youtube_uploader/
- main.py
- app/
  - tray.py
  - settings.py
  - database.py
  - models.py
  - config.py
  - logging_setup.py
  - services/
    - vmix_monitor.py
    - directory_watcher.py
    - file_completion.py
    - upload_manager.py
    - youtube_client.py
  - ui/
    - confirmation_dialog.py
    - upload_queue.py
    - settings_window.py
- resources/
- tests/
- requirements.txt
- README.md
- build.ps1

You may adjust the structure if there is a cleaner approach.

Separate business logic from GUI code.

Services should be independently testable.

## 13. Packaging

Create a PyInstaller-based build configuration for a Windows standalone executable.

Include a PowerShell build script.

The packaged application should:

- Launch without a console window
- Run in the system tray
- Store application data in an appropriate user directory
- Locate icons and bundled resources correctly
- Work without a separate Python installation

Do not bundle user-specific Google OAuth client credentials.

## 14. Testing

Write meaningful automated tests covering:

- vMix XML parsing and state transition detection
- Recording file stability detection
- Duplicate file detection
- Database persistence
- Queue recovery after restart
- Upload state transitions
- Google API error handling using mocks

Tests must not require access to a real YouTube account or a running vMix instance.

## 15. Implementation Instructions

Implement this project directly in the current repository.

First inspect the repository and identify any existing code worth preserving.

If the repository is empty, initialize the complete Python project.

Build the actual application and its key workflows, not pseudocode.

Prioritize a functional end-to-end path:

1. Launch system tray application.
2. Open settings and configure a watch directory.
3. Detect a completed video file.
4. Show the upload confirmation dialog.
5. Authenticate to YouTube.
6. Upload the approved video.
7. Track progress and completion.
8. Save upload history.
9. Integrate vMix recording-state detection.

Where external credentials are required, implement the full integration and document the setup process rather than inventing credentials or substituting fake successful uploads.

Use mocks only in tests.

Run the tests and resolve failures where the environment allows.

Document any functionality that cannot be verified without a Windows machine, vMix, or a real Google account.

Provide clear instructions for:

- Installing dependencies
- Running the app in development
- Configuring Google Cloud OAuth
- Enabling the vMix API
- Building the Windows executable
- Testing the full workflow

Make sensible engineering decisions without asking unnecessary questions. Focus on a reliable, maintainable, usable MVP rather than additional speculative features.

Do not add n8n integration, AI transcription, video editing, or other advanced features in this first version. Structure the application so these can be added later.