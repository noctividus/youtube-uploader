"""Durable JSON webhook outbox, delivered independently of recording/upload work."""
import json
import logging
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import requests

from .config import WebhookEndpoint
from .secrets import protect, unprotect

log = logging.getLogger(__name__)


class Webhooks(threading.Thread):
    def __init__(self, path: Path, report):
        super().__init__(name="webhooks")
        self.path, self.report = path, report
        self.stopping = threading.Event()
        self.wake = threading.Event()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS deliveries (
                id INTEGER PRIMARY KEY, url TEXT NOT NULL, headers TEXT NOT NULL,
                payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                due REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
                result TEXT NOT NULL DEFAULT '')""")
            db.execute("DELETE FROM deliveries WHERE status<>'pending' AND due<?", (time.time() - 30 * 86400,))

    def enqueue(self, endpoints: list[WebhookEndpoint], event: str, data: dict) -> str:
        event_id = str(uuid.uuid4())
        payload = json.dumps({"id": event_id, "event": event, "version": 1,
                              "occurred_at": datetime.now(timezone.utc).isoformat(), "data": data})
        with closing(sqlite3.connect(self.path)) as db, db:
            for endpoint in endpoints:
                if event in endpoint.events:
                    endpoint.validate()
                    encrypted = protect(json.dumps(endpoint.headers)) if endpoint.headers else ""
                    db.execute("INSERT INTO deliveries(url,headers,payload) VALUES (?,?,?)",
                               (endpoint.url, encrypted, payload))
        self.wake.set()
        return event_id

    def stop(self):
        self.stopping.set()
        self.wake.set()

    def run(self):
        with requests.Session() as session, closing(sqlite3.connect(self.path)) as db:
            session.trust_env = False  # Never pick up ambient .netrc credentials.
            db.row_factory = sqlite3.Row
            while not self.stopping.is_set():
                self.wake.clear()
                try:
                    row = db.execute("SELECT * FROM deliveries WHERE status='pending' AND due<=? ORDER BY id LIMIT 1",
                                     (time.time(),)).fetchone()
                    if row is None:
                        self.wake.wait(0.5)
                        continue
                    self.deliver(db, session, row)
                except sqlite3.Error:
                    self.report("Webhook outbox is unavailable; delivery will retry.")
                    self.stopping.wait(5)

    def deliver(self, db, session, row):
        attempts = row["attempts"] + 1
        retry = False
        status = "failed"
        try:
            payload = json.loads(row["payload"])
            headers = json.loads(unprotect(row["headers"])) if row["headers"] else {}
            headers.update({"Content-Type": "application/json", "X-Webhook-Id": payload["id"],
                            "X-Webhook-Event": payload["event"]})
            with session.post(row["url"], data=row["payload"].encode("utf-8"), headers=headers,
                              timeout=(3, 5), allow_redirects=False, stream=True) as response:
                code = response.status_code
                status = "delivered" if 200 <= code < 300 else "failed"
                retry = code in {408, 429} or code >= 500
                result = f"HTTP {code}"
        except requests.RequestException:
            retry, result = True, "Network request failed"
        except Exception:
            result = "Cannot read webhook configuration or encrypted headers"
        if retry and attempts < 3:
            status = "pending"
        with db:
            db.execute("UPDATE deliveries SET attempts=?,status=?,result=?,due=?,headers=? WHERE id=?",
                       (attempts, status, result, time.time() + 2 ** attempts,
                        row["headers"] if status == "pending" else "", row["id"]))
        # No URLs, header values, response bodies, or exception strings in logs/UI.
        message = f"Webhook delivery {row['id']}: {status} ({result}, attempt {attempts}/3)"
        log.info(message)
        self.report(message)
