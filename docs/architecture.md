# Architecture and decisions

## Ownership and storage

`models.py`, `database.py`, `files.py`, `monitor.py`, and `vmix.py` contain domain logic without Qt dependencies. A single `Worker` QThread owns the SQLite connection, vMix HTTP polling, filesystem checks, configuration writes, and watchdog lifecycle. UI commands enter a thread-safe queue; immutable recording snapshots return through Qt signals. Watchdog callbacks only mark discovery dirty. vMix requests use bounded connect/read timeouts and run off the GUI thread.

The database uses WAL, FULL synchronous writes, transactions, schema constraints, and `PRAGMA user_version` migrations. Version 1 includes fields for the intended upload lifecycle; version 2 adds recording source and vMix lifecycle state; version 3 adds upload-attempt history and duplicate-risk tracking. Unknown newer schema versions fail visibly rather than rewriting data. Application settings use an fsynced temporary JSON file followed by atomic replacement. Neither source videos nor user credentials are stored in the repository.

Exit stops accepting interaction, signals the worker, and keeps the UI event loop responsive while watchdog shuts down and SQLite closes. There is no forced worker termination. OS calls to inaccessible network storage can still delay shutdown; production validation should use the intended recording disks.

## Identity and file readiness

The user's newer instruction makes vMix the default trigger, superseding filesystem discovery and stability timers. The filesystem rules below apply only when vMix is explicitly disabled. In vMix mode, the API's primary recording filename supplies the path, a recording-start observation creates a durable provisional queue row, and a confirmed stop schedules upload confirmation. Rows acquire the real file identity once the path can be statted, preserving the provisional row ID where possible and merging with any previously known file identity to avoid duplicate queue entries.

vMix mode retains filename changes as primary recording segments and schedules each observed segment on stop. It does not infer filenames from directory contents. Stop confirmation opens a window even if the file is not yet readable; metadata actions wait for nonempty contents, exclusive access, and a consistent sampled fingerprint. A late filesystem update refreshes the open window without losing edited metadata. Startup idle states and connection failures never generate synthetic recording-stop events. Active rows become interrupted after disconnect/restart, and are reattached only when vMix explicitly names the active file again. Previously confirmed stops can finish their access checks after restart without reopening windows automatically.

The Windows key combines volume/device ID, file ID, and file birth timestamp. Size and nanosecond modification timestamp identify the observed content version. A normalized absolute path is a fallback only when the filesystem provides no file ID; rename recognition is then limited. Two independently copied files are separate recordings even if their contents match. No full-video hash is computed.

Readiness requires a continuously observed, nonempty, unchanged size/timestamp for the configured stability interval, then a `CreateFileW` read handle with sharing disabled. A busy file restarts the interval. A bounded SHA-256 sample of the beginning, middle, and end (at most 192 KiB) is saved at completion; the file is statted again after sampling. This fingerprint is additional evidence, not a full-content checksum. Filesystems or writers that preserve all metadata and modify only unsampled regions cannot be distinguished reliably by this scheme.

Reviews include the identity, size, timestamp, and sampled fingerprint that the user saw. Before saving Later/Skip or approving an upload, the worker checks them against the live file and current database record. A stale review cannot be applied silently to a changed recording. The uploader repeats this preflight, then holds a Windows read handle that denies writes and deletion throughout transfer; readiness alone never authorizes uploading.

Scans are non-recursive. Known queue entries continue to be checked after removal of their watch folder, but new files there are not discovered. Directory errors are isolated. Missing files lose readiness and stay visible as unavailable; restoration starts a new stability interval. Duplicate filesystem events do not create duplicate database rows. Stable file identities let renames preserve suppression.

## Queue transitions and recovery

Readiness and automatic review eligibility are separate. The first successful scan of each watched directory/extension combination establishes its baseline: existing files become ready without a popup. New arrivals and subsequent content changes in already watched scopes become eligible for automatic review after completion. Adding a folder or enabling an extension establishes a new quiet baseline for it; unrelated settings changes preserve existing baselines. Unwatched queue entries remain tracked but cannot trigger automatic review. Restart clears session eligibility. Opening the queue requests a scan without resetting the baseline. Only the Review button opens manual review; row double-click has no review action.

| Trigger | Result |
| --- | --- |
| New file, including existing file found at startup | Waiting for File Completion; no approval |
| Stable, accessible file passes verification | Ready for Review |
| Later | Ready for Review with persisted deferral and edited metadata |
| Skip | Skipped; no further automatic review for unchanged content |
| Remove from queue | Skipped and hidden; retain suppression record and source file |
| Observed content changes | Waiting; clear old approval, suppression, completion, and upload fields |
| Ready file disappears or becomes busy | Waiting; revoke readiness |
| Restart | Ready/Detecting return to Waiting for fresh observation; deferral persists |
| Interrupted Approved/Uploading state | Failed, with approval cleared and a review-required error |
| Unchanged Uploaded/Skipped state | Remains Uploaded/Skipped |

The queue tracks the latest observed version of each file identity. Every approved upload also creates a separate attempt with frozen source identity, path, fingerprint, metadata, channel ID and playlist ID. Progress/outcomes update that attempt; file replacement never deletes earlier results. Only the current attempt may update its queue row. Startup converts pending/in-flight operations to Failed without restarting them; an in-flight attempt additionally requires explicit duplicate-risk acknowledgment. Routine monitoring pause/resume does not recover or disrupt live uploads.

The worker resets readiness observation after a long scheduling gap (over five seconds) and on resume from Pause. OS suspend behavior and focus handling still require interactive validation. The controller checks Windows desktop availability before presenting pending reviews, keeps one review window open at a time, and never calls activation methods for automatic reviews. A user-requested review may activate normally. Closing a review without a button dismisses it for the current session; use Later to persist deferral across restarts.

## Future integrations

The vMix integration uses read-only HTTP XML polling (default two seconds); vMix control functions and automatic path mapping are not implemented. Primary recordings without an absolute filename remain explicit unresolved queue entries. Secondary recorder/MultiCorder support remains future work.

## Webhook delivery

The monitoring worker snapshots selected endpoints into a separate durable SQLite outbox when an event occurs. A dedicated HTTP thread delivers JSON without blocking monitoring or uploads. Endpoint configuration is retained per event, headers use current-user Windows DPAPI encryption, and retries preserve the event ID. The outbox is independent of queue database migrations. Events and the recording database changes are not one atomic transaction; a process crash between them can omit an event. Once queued, delivery is at least once with a three-attempt limit, not exactly once. Receivers must deduplicate IDs. Shutdown stops after the current bounded request and leaves pending events for restart.

HTTP redirects, response-body consumption, ambient netrc authentication and environment proxies are disabled. Errors report only delivery ID, HTTP code and attempt count. Event emission is explicit at lifecycle transitions; delivery failures never generate new Error events. Header editing follows Settings Save/Cancel semantics. See README for payloads, event meanings and retention rules.

## YouTube connection and transfer

`youtube_service.py` runs OAuth and uploads on a dedicated serial Python thread, separate from vMix polling. It returns events through the worker's command queue; only the Qt worker writes SQLite. Account changes are blocked during a transfer. The UI never handles credentials. Desktop OAuth uses a random loopback port, state verification and PKCE with a three-minute sign-in timeout. The explicit Windows keyring backend stores the imported client and refresh credentials, without a plaintext fallback. Google API errors are sanitized before reaching UI/logs.

`youtube.py` checks the currently authorized channel against the approved channel ID before insertion. Uploads use 8 MiB resumable chunks, bounded retries within one known session, and progress/speed/ETA events. Google transport failures are retried against the same session, including a status query after a lost response. The app does not persist resumable URLs or automatically initiate a second insert. A restart or ambiguous failure requires the user to check YouTube Studio before explicitly retrying.

The video ID and URL are persisted as soon as creation succeeds, before optional playlist placement. Playlist failure is a warning on the successful upload. Exit cancels OAuth or stops transfer between bounded network operations, joins the network thread, drains final result events, then closes SQLite. Upload history is retained in `upload_attempts`; the queue shows the current version's result. A separate history browser is not implemented.

Implementation references: [Qt QLockFile](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QLockFile.html) for per-data-directory process locking, and the [watchdog quickstart](https://python-watchdog.readthedocs.io/en/stable/quickstart.html) for observer startup/shutdown.

## Local Windows releases

`scripts/build.ps1` drives `scripts/build_release.py`: dependency/version checks, offline tests, icon exports, a PyInstaller folder bundle, a frozen self-check, then Inno Setup and a portable ZIP. Build dependencies are pinned in `requirements-build.lock`. The spec explicitly includes the YouTube discovery document, Windows credential backend and icon. It collects package resources, never user runtime state. License texts and dependency metadata accompany each bundle.

The installer uses a stable AppId and a per-user application directory. Startup and desktop shortcuts are optional. Frozen production processes expose `vMixYouTubeUploader.Running` as a Windows mutex so Setup/Uninstall require closing the app rather than terminating uploads. The existing per-data-directory lock remains the application's instance guard. Runtime settings and credentials are kept outside the install directory and retained on uninstall.

Versioned artifacts and checksums live in ignored `releases/<version>/`. GitHub Releases hosts locally built files; no CI workflow is configured. The explicit publish task checks a clean, unchanged commit, builds locally, verifies checksums, pushes the current branch, uploads a draft and verifies asset names/sizes before publishing a version tag at that commit. Existing tags/releases are not overwritten; failures may leave a draft for manual recovery. `--self-test <isolated-directory>` on the frozen executable verifies imports, discovery data, tray/worker lifetime, databases and Windows secret storage without contacting Google or a webhook receiver.
