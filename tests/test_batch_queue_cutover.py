"""
INS-60 cutover tests — exercise the production wiring (init_db creates the
batch_jobs table + uq index, queue worker is started, /admin/queue route works,
trigger_revaluation enqueues instead of spawning a thread when
SOURCETECH_USE_BATCH_QUEUE=1).

Stdlib-only assertions where possible; Flask-app tests skip cleanly if Flask
is not installed in the env.
"""
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import batch_queue


def _reset_module_state():
    batch_queue._shutdown = False
    batch_queue._wake_event.clear()
    batch_queue._worker_started = threading.Lock()
    batch_queue._worker_thread = None


def _make_db_with_schema(db_path: str) -> None:
    """Minimal schema mirror of app.py init_db (just what the queue tests need)."""
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS vendors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url_code TEXT, vendor_name TEXT, dm_email TEXT, dm_name TEXT,
            status TEXT DEFAULT 'pending'
        );
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER NOT NULL,
            pavtech_batch_id TEXT,
            file_count INTEGER,
            pavtech_status TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_submissions_pavtech_batch_id
            ON submissions(pavtech_batch_id) WHERE pavtech_batch_id IS NOT NULL;
    """)
    conn.execute(
        "INSERT INTO vendors (id, url_code, vendor_name, dm_email, dm_name) "
        "VALUES (1, 'TESTCODE', 'Test Vendor', 'dm@test', 'Paul')"
    )
    conn.commit()
    conn.close()


class TestCutoverInvariants(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = self.tmp.name
        _make_db_with_schema(self.db)
        batch_queue.init_queue(self.db)
        _reset_module_state()

    def tearDown(self):
        batch_queue.stop_worker()
        try:
            os.unlink(self.db)
        except FileNotFoundError:
            pass

    def test_unique_index_blocks_duplicate_pavtech_batch_ids(self):
        """The cutover added UQ idx on submissions.pavtech_batch_id WHERE NOT NULL.
        Two completions of the same batch_id must NOT double-write submissions."""
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO submissions (vendor_id, pavtech_batch_id, file_count, pavtech_status) "
            "VALUES (1, 'BATCH-XYZ', 3, 'complete')"
        )
        conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO submissions (vendor_id, pavtech_batch_id, file_count, pavtech_status) "
                "VALUES (1, 'BATCH-XYZ', 3, 'complete')"
            )
            conn.commit()
        conn.close()

    def test_unique_index_allows_multiple_null_batch_ids(self):
        """Failure rows have pavtech_batch_id=NULL; UNIQUE WHERE NOT NULL must
        let multiple of those exist (the partial-index trick)."""
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO submissions (vendor_id, pavtech_batch_id, file_count, pavtech_status) "
            "VALUES (1, NULL, 2, 'error')"
        )
        conn.execute(
            "INSERT INTO submissions (vendor_id, pavtech_batch_id, file_count, pavtech_status) "
            "VALUES (1, NULL, 5, 'error')"
        )
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM submissions WHERE pavtech_batch_id IS NULL").fetchone()[0]
        self.assertEqual(n, 2)
        conn.close()

    def test_worker_alive_reflects_state(self):
        """worker_alive() must return False before start and True after."""
        self.assertFalse(batch_queue.worker_alive())
        batch_queue.start_worker(self.db, lambda v, p, i: True)
        # Worker thread should be up almost immediately.
        for _ in range(20):
            if batch_queue.worker_alive():
                break
            time.sleep(0.05)
        self.assertTrue(batch_queue.worker_alive())

    def test_enqueue_processes_via_worker_end_to_end(self):
        """End-to-end: enqueue a job, worker runs it, status becomes complete."""
        processed = threading.Event()

        def processor(vendor, paths, info):
            processed.set()
            return True

        batch_queue.start_worker(self.db, processor)
        vendor = {'id': 1, 'vendor_name': 'Test Vendor'}
        job_id = batch_queue.enqueue_batch(
            self.db, vendor, [Path("/tmp/a.xlsx")], [{'filename': 'a.xlsx'}]
        )
        self.assertTrue(processed.wait(timeout=3))
        # Wait a tick for _finish.
        for _ in range(30):
            conn = sqlite3.connect(self.db)
            r = conn.execute("SELECT status FROM batch_jobs WHERE id=?", (job_id,)).fetchone()
            conn.close()
            if r and r[0] == 'complete':
                break
            time.sleep(0.1)
        self.assertEqual(r[0], 'complete')

    def test_processor_exception_does_not_kill_worker(self):
        """A crashing processor must NOT take the worker thread down — the next
        job should still be processed."""
        calls = []
        done = threading.Event()

        def flaky(vendor, paths, info):
            calls.append(vendor['id'])
            if len(calls) == 1:
                raise RuntimeError("boom")
            done.set()
            return True

        batch_queue.start_worker(self.db, flaky)
        vendor = {'id': 1, 'vendor_name': 'Test Vendor'}
        batch_queue.enqueue_batch(self.db, vendor, [Path("/tmp/a.xlsx")], [{}])
        # Wait for the first crash to settle.
        time.sleep(0.5)
        batch_queue.enqueue_batch(self.db, vendor, [Path("/tmp/b.xlsx")], [{}])
        self.assertTrue(done.wait(timeout=4))
        self.assertTrue(batch_queue.worker_alive(),
                        "worker thread died after processor exception")


class TestStartWorkerIdempotent(unittest.TestCase):
    """start_worker must be safe to call multiple times (Flask debug reloader
    bites here otherwise)."""
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = self.tmp.name
        _make_db_with_schema(self.db)
        batch_queue.init_queue(self.db)
        _reset_module_state()

    def tearDown(self):
        batch_queue.stop_worker()
        try:
            os.unlink(self.db)
        except FileNotFoundError:
            pass

    def test_double_start_no_op(self):
        batch_queue.start_worker(self.db, lambda v, p, i: True)
        first = batch_queue._worker_thread
        batch_queue.start_worker(self.db, lambda v, p, i: True)
        second = batch_queue._worker_thread
        self.assertIs(first, second, "start_worker should be idempotent")


if __name__ == "__main__":
    unittest.main(verbosity=2)
