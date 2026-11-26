"""No git on the computer reads as a plain message, not an OS error."""

import unittest
from pathlib import Path

import fake_orca
from helpers import isolate_home, tmpdir


class GitMissingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        isolate_home(self.tmp.name)
        fake_orca.install(self.tmp.name)
        import orca_git_sync

        self.ogs = orca_git_sync
        self.tree = Path(self.tmp.name) / "tree"
        self.tree.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def syncer(self, git):
        return self.ogs.Syncer(self.tree, Path(self.tmp.name) / "git", {"repo_url": "https://x.example/a.git"}, git=git)

    def fake_git(self, script):
        path = Path(self.tmp.name) / "fake-git"
        path.write_text("#!/bin/sh\n" + script)
        path.chmod(0o755)
        return str(path)

    def test_missing_binary(self):
        r = self.syncer(str(Path(self.tmp.name) / "no" / "git")).sync()
        self.assertEqual(r.error, self.ogs.GIT_MISSING)
        self.assertNotIn("Errno", r.summary())
        self.assertTrue(r.summary().startswith("Git Sync failed: git was not found."))

    def test_macos_stub_without_command_line_tools(self):
        git = self.fake_git(
            "echo 'xcrun: error: invalid active developer path (/Library/Developer/CommandLineTools)' >&2\nexit 1\n"
        )
        self.assertEqual(self.syncer(git).sync().error, self.ogs.GIT_MISSING)

    def test_token_save_says_the_same(self):
        with self.assertRaises(self.ogs.GitError) as e:
            self.syncer(str(Path(self.tmp.name) / "no" / "git")).save_token("tok")
        self.assertEqual(str(e.exception), self.ogs.GIT_MISSING)

    def test_other_git_errors_keep_their_text(self):
        git = self.fake_git("echo 'fatal: something else' >&2\nexit 128\n")
        r = self.syncer(git).sync()
        self.assertIn("fatal: something else", r.error)
        self.assertNotEqual(r.error, self.ogs.GIT_MISSING)


if __name__ == "__main__":
    unittest.main()
