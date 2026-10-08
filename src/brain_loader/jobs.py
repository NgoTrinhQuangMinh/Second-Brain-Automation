"""Durable, serial document jobs; parsing runs in a separate process."""

import json
import sqlite3
import subprocess
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from filelock import FileLock


def now():
    return datetime.now(timezone.utc).isoformat()


class Jobs:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "jobs.sqlite3"
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, operation TEXT, document_id TEXT, payload TEXT,
                status TEXT, stage TEXT, created_at TEXT, updated_at TEXT,
                result TEXT, error TEXT)""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, operation, document_id, payload, job_id=None):
        job_id = job_id or uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 100:
                raise ValueError("Job queue is full; try again later")
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
                (job_id, operation, document_id, json.dumps(payload), "queued", "queued", now(), now(), None, None),
            )
        return self.get(job_id)

    def get(self, job_id):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        value = dict(row)
        value.pop("payload")
        value["job_id"] = value.pop("id")
        value["result"] = json.loads(value["result"]) if value["result"] else None
        return value

    def payload(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        return json.loads(row[0])

    def update(self, job_id, *, status="running", stage="running", result=None, error=None):
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status=?,stage=?,updated_at=?,result=?,error=? WHERE id=?",
                (status, stage, now(), json.dumps(result) if result is not None else None, error, job_id),
            )


class Worker:
    def __init__(self, jobs):
        self.jobs = jobs
        self.stop_event = threading.Event()
        self.lock = FileLock(str(jobs.data_dir / "api-worker.lock"), timeout=0)
        self.thread = threading.Thread(target=self.work, daemon=True)

    def start(self):
        self.lock.acquire()
        with self.jobs.connect() as db:
            # Re-run interrupted jobs with the same file/revision and upload journal.
            db.execute("UPDATE jobs SET status='queued',stage='resuming' WHERE status='running'")
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=15)
        if self.thread.is_alive():
            raise RuntimeError("Ingestion worker did not stop")
        self.lock.release()

    def work(self):
        while not self.stop_event.is_set():
            with self.jobs.connect() as db:
                row = db.execute(
                    "SELECT id FROM jobs WHERE status='queued' ORDER BY created_at,rowid LIMIT 1"
                ).fetchone()
            if row is None:
                self.stop_event.wait(1)
                continue
            job_id = row[0]
            self.jobs.update(job_id)
            try:
                process = subprocess.Popen(
                    [sys.executable, "-m", "brain_loader.document_worker", job_id],
                    stdout=subprocess.DEVNULL,
                )
                while process.poll() is None:
                    if self.stop_event.wait(0.5):
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                        self.jobs.update(job_id, status="queued", stage="resuming")
                        return
                if self.jobs.get(job_id)["status"] == "running":
                    self.jobs.update(
                        job_id, status="failed", stage="failed",
                        error="Worker exited unexpectedly. Resubmit the file to retry; previous records are journaled.",
                    )
            except Exception:
                self.jobs.update(job_id, status="failed", stage="failed", error="Could not run document worker")
