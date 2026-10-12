"""Workspace-only launcher with a cooperative stop request; never kills processes."""

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QLockFile, QTimer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "stop"))
    args = parser.parse_args()
    workspace = Path(__file__).resolve().parent.parent
    workspace_id = hashlib.sha256(os.path.normcase(str(workspace)).encode()).hexdigest()[:12]
    data_dir = Path(os.environ["LOCALAPPDATA"]) / "vMix YouTube Uploader" / "Development" / workspace_id
    data_dir.mkdir(parents=True, exist_ok=True)
    request = data_dir / "stop-request"
    lock = QLockFile(str(data_dir / "development.lock"))
    lock.setStaleLockTime(0)

    if args.action == "stop":
        if lock.tryLock(0):
            lock.unlock()
            print("The testing app is already stopped.", flush=True)
            return 0
        request.write_text("stop", encoding="utf-8")
        print("Waiting for the testing app to close its watcher and database…", flush=True)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if lock.tryLock(0):
                lock.unlock()
                print("Testing app stopped.", flush=True)
                return 0
            time.sleep(0.1)
        print("Shutdown is taking longer than expected. Check the app or its logs; no process was forcibly killed.", flush=True)
        return 1

    if not lock.tryLock(0):
        print("This workspace's testing app is already running (or the test-data folder cannot be locked).", flush=True)
        return 1
    try:
        request.unlink(missing_ok=True)
        sys.path.insert(0, str(workspace))
        import vmix_youtube_uploader.main as entry

        class DevelopmentController(entry.Controller):
            def __init__(self, app, directory):
                super().__init__(app, directory)
                self.stop_request_timer = QTimer(self)
                self.stop_request_timer.setInterval(250)
                self.stop_request_timer.timeout.connect(self.check_stop_request)
                self.stop_request_timer.start()

            def check_stop_request(self) -> None:
                if request.exists():
                    request.unlink(missing_ok=True)
                    self.stop_request_timer.stop()
                    self.shutdown()

        entry.Controller = DevelopmentController
        sys.argv = [sys.argv[0], "--data-dir", str(data_dir)]
        print(f"Test settings, queue, and logs: {data_dir}", flush=True)
        print("Open the app from its tray icon. Use 'Uploader: Stop testing app' to exit cleanly.", flush=True)
        return entry.main()
    finally:
        lock.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
