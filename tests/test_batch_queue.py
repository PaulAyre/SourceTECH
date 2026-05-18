"""
Scaffold tests for batch_queue (INS-60).

Exercises the queue lifecycle in isolation — does NOT touch PavTECH or
the real Flask app. processor_fn is a stub.

Stdlib-only: uses tempfile + sqlite3.
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


def _bootstrap_db(db_path: str) -> None:
    """Create minimal vendors table so _fetch_vendor works."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vendors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url_code TEXT, vendor_name TEXT, dm_email TEXT, dm_name TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)
    conn.execute(
        "INSERT INTO vendors (id, url_code, vendor_name, dm_email, dm_name) "
        "VALUES (1, 'ABCD1234', 'Test Vendor', 'dm@test', 'Paul')"
    )
    conn.commit()
    conn.close()


class TestQueueLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = self.tmp.name
        _bootstrap_db(self.db)
        batch_queue.init_queue(self.db)
        # Reset module-level state so tests are independent.
        batch_queue._shutdown = False
        batch_queue._wake_event.clear()
        # Replace the single-start lock so each test can start a fresh worker.
        batch_queue._worker_started = threading.Lock()
        batch_queue._worker_thread = None

    def tearDown(self):
        batch_queue.stop_worker()
        os.unlink(self.db)

    def test_init_queue_creates_table(self):
        conn = sqlite3.connect(self.db)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='batch_jobs'"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row, "batch_jobs table missing")

    def test_enqueue_creates_pending_row(self):
        vendor = {'id': 1, 'vendor_name': 'Test Vendor'}
        job_id = batch_queue.enqueue_batch(
            self.db, vendor, [Path("/tmp/a.xlsx")], [{'filename': 'a.xlsx'}]
        )
        self.assertIsNotNone(job_id)
        conn = sqlite3.connect(self.db)
        row = conn.execute(
            "SELECT status, attempts FROM batch_jobs WHERE id=?", (job_id,)
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], 'pending')
        self.assertEqual(row[1], 0)

    def test_worker_processes_pending_job(self):
        processed = []
        done = threading.Event()

        def stub_processor(vendor, paths, info):
            processed.append((vendor['vendor_name'], len(paths), info))
            done.set()
            return True

        batch_queue.start_worker(self.db, stub_processor)
        # Enqueue AFTER the worker is up so we exercise the wake_event path.
        vendor = {'id': 1, 'vendor_name': 'Test Vendor'}
        batch_queue.enqueue_batch(
            self.db, vendor, [Path("/tmp/a.xlsx"), Path("/tmp/b.xlsx")],
            [{'filename': 'a.xlsx'}, {'filename': 'b.xlsx'}],
        )
        # Worker should claim + run within a second or two.
        self.assertTrue(done.wait(timeout=3),
                        "worker did not process job within 3s")
        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0][0], 'Test Vendor')
        self.assertEqual(processed[0][1], 2)
        # Wait a tick for _finish to commit.
        for _ in range(20):
            conn = sqlite3.connect(self.db)
            status = conn.execute("SELECT status FROM batch_jobs LIMIT 1").fetchone()[0]
            conn.close()
            if status == 'complete':
                break
            time.sleep(0.1)
        self.assertEqual(status, 'complete')

    def test_recovery_picks_up_stuck_in_progress_job(self):
        """The recovery feature — boot must reclaim stuck jobs from prior process."""
        # Seed a stuck in_progress job (started 30 minutes ago).
        stuck_started = (datetime.now() - timedelta(minutes=30)).isoformat()
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO batch_jobs (vendor_id, file_paths_json, files_info_json, "
            "status, started_at, attempts) VALUES (?, ?, ?, 'in_progress', ?, 1)",
            (1, json.dumps(["/tmp/stuck.xlsx"]),
             json.dumps([{'filename': 'stuck.xlsx'}]), stuck_started),
        )
        conn.commit()
        conn.close()

        recovered = threading.Event()

        def stub_processor(vendor, paths, info):
            recovered.set()
            return True

        batch_queue.start_worker(self.db, stub_processor)
        self.assertTrue(recovered.wait(timeout=3),
                        "worker did not recover stuck in_progress job within 3s")

    def test_stuck_jobs_query(self):
        # Insert one fresh in_progress and one stuck in_progress; only the
        # stuck one should appear.
        fresh = datetime.now().isoformat()
        stuck = (datetime.now() - timedelta(minutes=30)).isoformat()
        conn = sqlite3.connect(self.db)
        conn.execute(
            "INSERT INTO batch_jobs (vendor_id, file_paths_json, files_info_json, "
            "status, started_at) VALUES (1, '[]', '[]', 'in_progress', ?)",
            (fresh,),
        )
        conn.execute(
            "INSERT INTO batch_jobs (vendor_id, file_paths_json, files_info_json, "
            "status, started_at) VALUES (1, '[]', '[]', 'in_progress', ?)",
            (stuck,),
        )
        conn.commit()
        conn.close()
        stucks = batch_queue.stuck_jobs(self.db, max_age_min=15)
        self.assertEqual(len(stucks), 1,
                         f"expected exactly one stuck job, got {len(stucks)}: {stucks!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
