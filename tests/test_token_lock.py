"""Saving a token rewrites the clone config, so it waits for a running sync."""

import threading
import unittest
from pathlib import Path

import fake_orca
from helpers import isolate_home, tmpdir


class TokenLockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        isolate_home(self.tmp.name)
        fake_orca.install(self.tmp.name)
        import orca_git_sync

        self.ogs = orca_git_sync
        record = Path(self.tmp.name) / "helper-got"
        self.record = record
        orca_git_sync.credential_helper = lambda: f"!f() {{ cat >> '{record}'; }}; f"

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_token_waits_for_the_sync_lock(self):
        s = self.ogs.Syncer(
            Path(self.tmp.name) / "tree",
            Path(self.tmp.name) / "git",
            {"repo_url": "https://x.example/a.git"},
            git="git",
        )
        config = s.git_dir / "config"
        with s.lock:
            t = threading.Thread(target=s.save_token, args=("tok",), daemon=True)
            t.start()
            t.join(1.0)
            self.assertTrue(t.is_alive(), "save_token ran while a sync held the lock")
            self.assertFalse(config.exists(), "save_token wrote the config while a sync held the lock")
        t.join(30)
        self.assertFalse(t.is_alive())
        self.assertIn("password=tok", self.record.read_text())

    def test_sync_and_token_save_both_finish(self):
        tree = Path(self.tmp.name) / "tree"
        tree.mkdir()
        s = self.ogs.Syncer(tree, Path(self.tmp.name) / "git", {"repo_url": "https://x.example/a.git"}, git="git")
        threads = [
            threading.Thread(target=s.sync, daemon=True),
            threading.Thread(target=s.save_token, args=("tok",), daemon=True),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertFalse(any(t.is_alive() for t in threads), "deadlock between sync and save_token")


if __name__ == "__main__":
    unittest.main()
