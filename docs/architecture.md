# Milestone 1 architecture and decisions

## Ownership and storage

`models.py`, `database.py`, `files.py`, and `monitor.py` contain domain logic without Qt dependencies. A single `Worker` QThread owns the SQLite connection, filesystem checks, configuration writes, and watchdog lifecycle. UI commands enter a thread-safe queue; immutable recording snapshots return through Qt signals. Watchdog callbacks only mark discovery dirty. Network operations do not exist yet.

The database uses WAL, FULL synchronous writes, transactions, schema constraints, and `PRAGMA user_version` migrations. Version 1 includes fields for the intended upload lifecycle. Unknown newer schema versions fail visibly rather than rewriting data. Application settings use an fsynced temporary JSON file followed by atomic replacement. Neither source videos nor user credentials are stored in the repository.

Exit stops accepting interaction, signals the worker, and keeps the UI event loop responsive while watchdog shuts down and SQLite closes. There is no forced worker termination. OS calls to inaccessible network storage can still delay shutdown; production validation should use the intended recording disks.

## Identity and file readiness

The Windows key combines volume/device ID, file ID, and file birth timestamp. Size and nanosecond modification timestamp identify the observed content version. A normalized absolute path is a fallback only when the filesystem provides no file ID; rename recognition is then limited. Two independently copied files are separate recordings even if their contents match. No full-video hash is computed.

Readiness requires a continuously observed, nonempty, unchanged size/timestamp for the configured stability interval, then a `CreateFileW` read handle with sharing disabled. A busy file restarts the interval. A bounded SHA-256 sample of the beginning, middle, and end (at most 192 KiB) is saved at completion; the file is statted again after sampling. This fingerprint is additional evidence, not a full-content checksum. Filesystems or writers that preserve all metadata and modify only unsampled regions cannot be distinguished reliably by this scheme.

Reviews include the identity, size, timestamp, and sampled fingerprint that the user saw. Before saving Later/Skip, the worker checks them against the live file and current database record. A stale review cannot be applied silently to a changed recording. The future uploader must run the same preflight and add upload-specific checks; readiness alone never authorizes uploading.

Scans are non-recursive. Known queue entries continue to be checked after removal of their watch folder, but new files there are not discovered. Directory errors are isolated. Missing files lose readiness and stay visible as unavailable; restoration starts a new stability interval. Duplicate filesystem events do not create duplicate database rows. Stable file identities let renames preserve suppression.

## Queue transitions and recovery

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

The current schema tracks the latest observed version of each file identity. Before milestone 2 enables uploads, add immutable upload-attempt history so overwriting a previously uploaded source preserves its earlier YouTube result independently of the current file version. The current workflow cannot create uploads and does not claim upload-session recovery.

The worker resets readiness observation after a long scheduling gap (over five seconds) and on resume from Pause. OS suspend behavior and focus handling still require interactive validation. The controller checks Windows desktop availability before presenting pending reviews, keeps one review window open at a time, and never calls activation methods for automatic reviews. A user-requested review may activate normally. Closing a review without a button dismisses it for the current session; use Later to persist deferral across restarts.

## Future integrations

vMix is absent in milestone 1. When enabled in milestone 3, active recording must gate readiness, and a disconnected/unknown state must not be treated as a recording-stop transition. Default to holding newly detected candidates while an enabled vMix connection is unknown; disabling the integration explicitly returns to filesystem-only detection. Implement and test that policy when the integration arrives.

OAuth, approval-to-upload transitions, resumable sessions, retries, durable upload-attempt history, Google API errors, and actual progress are milestone 2 work. The reserved statuses/columns are not substitutes for those implementations.

Implementation references: [Qt QLockFile](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QLockFile.html) for per-data-directory process locking, and the [watchdog quickstart](https://python-watchdog.readthedocs.io/en/stable/quickstart.html) for observer startup/shutdown.
