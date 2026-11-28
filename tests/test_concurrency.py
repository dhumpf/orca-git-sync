"""One sync at a time: triggers coalesce, a second process skips, unload waits."""

import subprocess
import sys
import threading
import unittest
from pathlib import Path

import fake_orca
from helpers import Mac, bare_repo, isolate_home, tmpdir
from test_plugin import load_plugin


class ConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        isolate_home(self.tmp.name)
        self.remote = bare_repo(self.tmp.name)
        self.orca, self.ogs = load_plugin(self.tmp.name)
        self.mac = Mac(self.tmp.name, ".", self.remote)
        self.cap = self.ogs.GitSync()
        self.cap.config = fake_orca.config(repo_url=str(self.remote), preset_folder="user-1234")
        self.ogs.data_dir = lambda: Path(self.tmp.name)
        self.cap.storage_dir = Path(self.tmp.name) / "orca_plugins" / "plugin_data" / "orca_git_sync"
        self.cap.storage_dir.mkdir(parents=True, exist_ok=True)
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.real_sync = self.ogs.Syncer.sync
        outer = self

        def slow_sync(syncer):
            outer.calls += 1
            if outer.calls == 1:
                outer.entered.set()
                outer.release.wait(30)
            return outer.real_sync(syncer)

        self.ogs.Syncer.sync = slow_sync

    def tearDown(self):
        self.release.set()
        self.ogs.Syncer.sync = self.real_sync
        self.tmp.cleanup()

    def start_first(self):
        t = threading.Thread(target=self.cap.run_sync, daemon=True)
        t.start()
        self.assertTrue(self.entered.wait(10), "first sync never started")
        return t

    def test_triggers_during_a_sync_coalesce_into_one_rerun(self):
        t = self.start_first()
        for _ in range(5):
            self.assertIsNone(self.cap.run_sync())
        self.release.set()
        t.join(60)
        self.assertFalse(t.is_alive())
        self.assertEqual(self.calls, 2)

    def test_no_rerun_without_a_trigger(self):
        t = self.start_first()
        self.release.set()
        t.join(60)
        self.assertEqual(self.calls, 1)
        self.assertFalse(self.ogs.GitSync.running)

    def test_unload_waits_for_the_running_sync(self):
        t = self.start_first()
        u = threading.Thread(target=self.cap.on_unload, daemon=True)
        u.start()
        u.join(1.0)
        self.assertTrue(u.is_alive(), "unload returned while a sync ran")
        self.release.set()
        u.join(60)
        t.join(60)
        self.assertFalse(u.is_alive())
        self.assertEqual(self.calls, 2, "unload did not get a final pass")
        self.assertIn("up to date", self.cap.last)

    def test_second_process_skips_with_a_clear_status(self):
        code = (
            "import fcntl,sys,time\n"
            "f=open(sys.argv[1],'a+')\n"
            "fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
            "print('held',flush=True)\n"
            "time.sleep(30)\n"
        )
        lock = self.cap.storage_dir / "sync.lock"
        other = subprocess.Popen([sys.executable, "-c", code, str(lock)], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(other.stdout.readline().strip(), "held")
            self.release.set()
            result = self.cap.run_sync()
            self.assertTrue(result.busy)
            self.assertEqual(self.calls, 0, "synced while another process held the lock")
            self.assertEqual(self.cap.last, "Git Sync skipped: another Orca is syncing this clone")
            self.assertNotIn("skipped", self.ogs.GitSync.message(self.ogs.SyncResult(), False))
        finally:
            other.kill()
            other.wait()
        result = self.cap.run_sync()
        self.assertFalse(result.busy, "lock not released with the other process")
        self.assertEqual(self.calls, 1)


if __name__ == "__main__":
    unittest.main()
