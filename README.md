# vMix YouTube Uploader

A planned Windows desktop application that lives in the system tray, detects completed recordings, and uploads them to YouTube after explicit approval.

**Status:** repository setup and planning. No application code, dependencies, or build scripts have been added yet.

- [Project brief](docs/project-brief.md): supplied product requirements.
- [Implementation plan](docs/implementation-plan.md): milestones and first-step acceptance criteria.
- [Contributor instructions](AGENTS.md): architecture, safety rules, and development expectations.

Target stack: Python 3.12+, PySide6, watchdog, SQLite, vMix HTTP API, YouTube Data API v3 with desktop OAuth, and PyInstaller.

The first implementation milestone will deliver a local tray-to-review workflow: configure a watched directory, detect stable videos, and retain their review state across restarts. YouTube authentication and uploads follow in the next milestone.

Development, account setup, and packaging instructions will be added as their corresponding functionality is implemented and verified.
