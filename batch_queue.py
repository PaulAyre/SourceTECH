"""
SourceTECH persisted batch queue (INS-60).

Design doc:
  /Users/paulayre/concierge/sourcetech/batch-recovery-design-2026-05-18.md

WIRED in app.py on branch paul/ins-60-batch-recovery-cutover:
  - init_queue + start_worker called at startup.
  - trigger_revaluation() uses enqueue_batch instead of fire-and-forget
    threading.Thread (with a fallback to the thread path if
    SOURCETECH_USE_BATCH_QUEUE=0 or enqueue raises).
  - /admin/queue route exposes worker_alive + list_pending + stuck_jobs.

Public API:
    init_queue(db_path)            -- ensure batch_jobs table exists
    enqueue_batch(vendor, paths, files_info) -> job_id
    start_worker(processor_fn, db_path) -- launch the single background worker
    list_pending(db_path) -> list  -- for /admin/queue diagnostics
    stuck_jobs(db_path, max_age_min=15) -> list  -- recovery candidates

The worker rehydrates `in_progress` jobs older than `max_age_min` on boot,
which is the recovery feature.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS batch_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id INTEGER NOT NULL,
    file_paths_json TEXT NOT NULL,
    files_info_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    started_at DATETIME,
    finished_at DATETIME,
    attempts INTEGER DEFAULT 0,
    last_error TEXT,
    FOREIGN KEY (vendor_id) REFERENCES vendors(id)
);
CREATE INDEX IF NOT EXISTS idx_batch_jobs_status ON batch_jobs(status);
"""

MAX_ATTEMPTS = 3
STUCK_AFTER_MIN = 15

_wake_event = threading.Event()
_worker_started = threading.Lock()
_worker_thread: Optional[threading.Thread] = None
_shutdown = False


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------
def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # WAL gives much better single-writer/multi-reader behaviour than the default.
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_queue(db_path: str) -> None:
    """Idempotent. Creates the batch_jobs table if missing."""
    conn = _connect(db_path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------
def enqueue_batch(
    db_path: str,
    vendor: Dict,
    file_paths: List[Path],
    files_info: List[Dict],
) -> int:
    """Persist a new pending job. Returns job_id."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO batch_jobs (vendor_id, file_paths_json, files_info_json, status) "
            "VALUES (?, ?, ?, 'pending')",
            (
                vendor['id'],
                json.dumps([str(p) for p in file_paths]),
                json.dumps(files_info, default=str),
            ),
        )
        conn.commit()
        job_id = cur.lastrowid
        logger.info("Enqueued batch job %s for vendor=%s files=%s",
                    job_id, vendor.get('vendor_name'), len(file_paths))
        _wake_event.set()
        return job_id
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------
def list_pending(db_path: str) -> List[Dict]:
    """Diagnostic helper for /admin/queue."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM batch_jobs WHERE status IN ('pending', 'in_progress') "
            "ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def stuck_jobs(db_path: str, max_age_min: int = STUCK_AFTER_MIN) -> List[Dict]:
    """in_progress jobs whose started_at is older than max_age_min — recovery candidates."""
    cutoff = (datetime.now() - timedelta(minutes=max_age_min)).isoformat()
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM batch_jobs WHERE status='in_progress' AND started_at < ? "
            "ORDER BY created_at",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _claim_next(db_path: str) -> Optional[Dict]:
    """
    Atomically claim the next job: either a pending one or a stuck in_progress.
    Uses an UPDATE ... WHERE status=? guard to avoid two workers grabbing the
    same row even if there are two workers (which currently there are not).
    """
    cutoff = (datetime.now() - timedelta(minutes=STUCK_AFTER_MIN)).isoformat()
    conn = _connect(db_path)
    try:
        conn.isolation_level = None  # autocommit; we manage txns manually
        conn.execute("BEGIN IMMEDIATE")
        # Prefer pending; fall back to stuck in_progress for recovery.
        row = conn.execute(
            "SELECT * FROM batch_jobs WHERE status='pending' "
            "OR (status='in_progress' AND started_at < ?) "
            "ORDER BY created_at LIMIT 1",
            (cutoff,),
        ).fetchone()
        if not row:
            conn.execute("ROLLBACK")
            return None
        # Mark in_progress and bump attempts.
        conn.execute(
            "UPDATE batch_jobs "
            "SET status='in_progress', started_at=?, attempts=attempts+1 "
            "WHERE id=?",
            (datetime.now().isoformat(), row['id']),
        )
        conn.execute("COMMIT")
        return dict(row)
    finally:
        conn.close()


def _finish(db_path: str, job_id: int, ok: bool, error: Optional[str] = None) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE batch_jobs SET status=?, finished_at=?, last_error=? WHERE id=?",
            ('complete' if ok else 'error',
             datetime.now().isoformat(),
             error,
             job_id),
        )
        conn.commit()
    finally:
        conn.close()


def _worker_loop(db_path: str, processor_fn: Callable[[Dict, List[Path], List[Dict]], bool]) -> None:
    """
    Single worker thread. Claims jobs one at a time, invokes processor_fn.
    processor_fn signature mirrors _process_batch_with_pavtech in app.py:
        processor_fn(vendor_dict, file_paths, files_info) -> bool (success)
    """
    logger.info("Batch queue worker started")
    while not _shutdown:
        try:
            job = _claim_next(db_path)
            if job is None:
                # Sleep until enqueue_batch wakes us, or 60s tick to re-check
                # for stuck jobs left behind by a previous process.
                _wake_event.wait(timeout=60)
                _wake_event.clear()
                continue

            logger.info("Processing batch_jobs.id=%s attempt=%s", job['id'], job['attempts'])

            file_paths = [Path(p) for p in json.loads(job['file_paths_json'])]
            files_info = json.loads(job['files_info_json'])
            # processor_fn needs the vendor dict — fetch it from the main DB.
            vendor = _fetch_vendor(db_path, job['vendor_id'])
            if vendor is None:
                _finish(db_path, job['id'], ok=False, error="vendor row missing")
                continue

            try:
                ok = processor_fn(vendor, file_paths, files_info)
                _finish(db_path, job['id'], ok=bool(ok))
            except Exception as exc:  # noqa: BLE001
                logger.exception("Batch processor crashed for job=%s", job['id'])
                if job['attempts'] >= MAX_ATTEMPTS:
                    _finish(db_path, job['id'], ok=False,
                            error=f"max attempts reached: {exc}")
                else:
                    # Leave as in_progress with bumped attempts so it can be
                    # re-claimed after STUCK_AFTER_MIN. (Could also revert
                    # to pending — chose this to avoid hot retry loops.)
                    logger.info("Job %s will be retried after %s minutes",
                                job['id'], STUCK_AFTER_MIN)
        except Exception:  # noqa: BLE001
            logger.exception("Worker loop error (will continue)")
            time.sleep(5)


def _fetch_vendor(db_path: str, vendor_id: int) -> Optional[Dict]:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def start_worker(db_path: str, processor_fn: Callable) -> None:
    """Idempotent — starts the worker thread if not already running."""
    global _worker_thread
    if not _worker_started.acquire(blocking=False):
        return  # already started
    init_queue(db_path)
    _worker_thread = threading.Thread(
        target=_worker_loop,
        args=(db_path, processor_fn),
        daemon=True,
        name="sourcetech-batch-queue",
    )
    _worker_thread.start()


def worker_alive() -> bool:
    """Returns True if the worker thread is currently running.
    Used by /admin/queue diagnostics to alert if the worker has died.
    """
    return _worker_thread is not None and _worker_thread.is_alive()


def stop_worker() -> None:
    """Mainly for tests."""
    global _shutdown
    _shutdown = True
    _wake_event.set()
    if _worker_thread is not None:
        _worker_thread.join(timeout=5)
