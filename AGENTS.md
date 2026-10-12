# vMix YouTube Uploader — contributor instructions

## Scope and source of truth

Build a reliable Windows system-tray application for reviewing and uploading completed recordings to YouTube. The full requirements are in `docs/project-brief.md`; the staged implementation plan is in `docs/implementation-plan.md`.

The user has authorized milestone 1: the local tray-to-review workflow. Implement and validate that slice; OAuth, actual uploads, vMix integration, and packaging remain subsequent milestones. See `docs/architecture.md` for the implemented detection and recovery rules.

## Stack and architecture

- Python 3.12+, PySide6, watchdog, requests, SQLite, the Google YouTube API and OAuth libraries, Windows-compatible credential storage (prefer keyring), pytest, and PyInstaller.
- Keep domain models, persistence, completion detection, and upload state transitions independent of GUI code and independently testable.
- Keep network calls, directory scans, and uploads off the Qt GUI thread. Use Qt signals/slots for UI updates.
- Use type hints, clear module boundaries, and rotating structured logs. Avoid unnecessary frameworks and services.
- Store runtime settings, database, and logs in the user's Windows application data directory, never alongside the executable.

## Required safety and reliability behavior

- Never upload without explicit approval. Default privacy is Private.
- Never delete or modify source recordings as part of uploading or removing queue entries.
- File creation alone does not mean completion. Require configurable stability (default 60 seconds), Windows access checks where practical, and available vMix recording-state evidence. Revalidate the file immediately before upload.
- Identify files using more than filenames. Reconcile persisted state on startup without resurfacing unchanged uploaded or skipped recordings.
- Queue newly discovered existing recordings for review; never auto-upload them.
- Persist metadata, approval, progress, and upload outcomes transactionally. An altered file must not inherit approval for an earlier version.
- Process uploads sequentially by default. Do not automatically retry ambiguous outcomes that could produce duplicate videos. Require confirmation when safe session recovery is unavailable.
- Do not hardcode, log, commit, or package OAuth credentials, tokens, or resumable session secrets. Use secure credential storage.
- vMix integration is optional, read-only, and resilient to disconnection. Do not remotely start or stop recording.
- Avoid focus stealing and overlapping approval windows. Preserve pending work across lock, sleep, restart, and shutdown.

## Working practices

- Inspect existing files and Git status before edits; preserve unrelated user work.
- Implement milestones as usable slices, not mock production integrations or pseudocode. Mocks belong in tests only.
- Add meaningful tests for state transitions, file completion, file identity, persistence, recovery, and API error handling as those capabilities are implemented.
- Tests must run without a live vMix instance or YouTube account. Use temporary files/databases and controlled clocks where appropriate.
- Run the checks relevant to the change. Report what passed, what was not run, and what needs manual Windows/vMix/Google verification.
- Keep setup and packaging instructions accurate; do not document commands as working until the relevant files exist and checks have been performed.
- Do not add transcription, editing, n8n, or speculative integrations to the MVP.
- Do not publish, push, or create external resources unless requested.

## Current implementation checkpoint

Milestone 1 uses `pyproject.toml` for dependencies and package configuration. Run `.\.venv\Scripts\python.exe -m pytest -q` for the offline suite. Do not enable upload actions until the real uploader and its approval/recovery tests exist.
