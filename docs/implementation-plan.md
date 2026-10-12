# Implementation plan

## Current checkpoint

The local tray-to-review workflow is implemented. The user subsequently requested vMix recording-state integration ahead of the original order: it is now the default trigger and replaces folder monitoring/stability delays when enabled. See `README.md` for setup and validation and `docs/architecture.md` for state and recovery decisions. YouTube uploads, webhook dispatch, packaging, and broader operational validation remain planned.

## First step: a durable local recording-review workflow

Build the smallest useful slice that exercises the application's main reliability boundary: distinguishing a completed recording from a file still being written, then preserving its review state.

Start by defining the recording identity, persistent queue states, and allowed transitions. Then implement persistence and file completion logic with tests before wiring them into a minimal tray application.

Deliverables:

1. Python package and development configuration with pytest support.
2. SQLite schema and migrations, recording identity rules, transactional state changes, and startup reconciliation. Represent the full intended queue lifecycle without pretending uploads work yet.
3. Watchdog events plus periodic scans for supported video extensions. Use size and modification-time stability, practical Windows access checks, and final readiness revalidation. Track candidates independently and tolerate missing or renamed files.
4. A minimal PySide6 tray, single-instance guard, watched-directory settings, and a persistent review queue. Closing windows leaves the tray running; Exit shuts down cleanly.
5. Review, Later, and Skip behavior with no focus stealing or overlapping review windows. Upload approval and execution become available when the real uploader exists in milestone 2.

Acceptance criteria:

- Launch into the tray and select a watch directory; persist settings under Windows application data.
- A growing video remains pending; a stable, accessible video appears once for review after the configured interval (60 seconds by default).
- Multiple recordings are retained independently, including events recovered by a directory scan.
- Existing untracked videos enter review at startup. Unchanged skipped records stay skipped after restart; deferred records remain available.
- A renamed or replaced file is reconciled according to explicit identity rules. Changed content cannot inherit stale approval.
- Tests cover completion timing, duplicate event handling, persistence, and startup reconciliation without vMix or Google.
- Manually verify tray lifetime, single-instance behavior, and Windows file-access checks. Document anything not verified.

This milestone proves the detection and persistence path without requiring external credentials. It is an intermediate deliverable; the finished MVP still requires all applicable requirements in the brief.

## Milestone 2: approved YouTube uploads

Add desktop OAuth, secure token storage, channel information, editable metadata, Private defaults, optional playlist selection, and explicit approval. Implement real sequential resumable uploads in a background worker with progress, durable history, bounded transient retries, and safe recovery of interrupted or ambiguous results. Revalidate source identity and readiness before each upload. Test API errors and recovery with mocks; verify an explicitly approved private upload with a real account when available.

## Milestone 3: vMix integration and operational resilience

Add configurable read-only vMix polling, XML parsing, recording/streaming status, transition-triggered scans, and recording-state gates for file completion. Handle disconnection and reconnection without treating loss of contact as a recording-stop event. Complete notifications, lock/sleep/wake handling, configuration, logging, queue actions, and graceful shutdown. Test transitions and failure cases with fixtures; verify against vMix when available.

## Milestone 4: Windows packaging and MVP validation

Add the PyInstaller configuration, PowerShell build script, bundled icon/resource handling, and configurable Windows startup. Document development setup, Google OAuth setup, vMix setup, packaging, and manual end-to-end validation. Verify the executable on Windows without a separate Python installation and audit the product against the full brief.

## Decisions recorded during milestone 1

- Define file identity and fingerprinting that handles replacement and rename without repeatedly hashing multi-gigabyte active recordings.
- Define conservative completion behavior when vMix is enabled but unreachable; filesystem evidence must never be presented as a guarantee that a writer has finished.
- Specify how queue removal suppresses rediscovery without deleting source files.
- Define database ownership across workers, migrations, and shutdown ordering before adding asynchronous writes.

These decisions are documented in `docs/architecture.md`, with automated tests for their observable behavior. The newer vMix workflow supersedes the original directory-association plan by using the primary recording filename supplied by the API. Missing filenames are reported rather than guessed.
