"""git gets only the environment it needs, never the rest of the user's shell."""

import os
import unittest
from unittest import mock

import fake_orca
from helpers import tmpdir

SHELL = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/Users/someone",
    "LANG": "en_CA.UTF-8",
    "SYSTEMROOT": r"C:\Windows",
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    "AWS_SECRET_ACCESS_KEY": "do-not-leak",
    "GITHUB_TOKEN": "do-not-leak-either",
    "GIT_DIR": "/somewhere/else/.git",
}


class EnvTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        fake_orca.install(self.tmp.name)
        import orca_git_sync

        self.syncer = orca_git_sync.Syncer(self.tmp.name + "/tree", self.tmp.name + "/git", {"repo_url": "x"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_allowlisted_variables_reach_git(self):
        with mock.patch.dict(os.environ, SHELL, clear=True):
            env = self.syncer.env()
        passed = {k for k in env if not k.startswith("GIT_")}
        self.assertEqual(passed, {"PATH", "HOME", "LANG", "SYSTEMROOT", "DBUS_SESSION_BUS_ADDRESS"})
        self.assertNotIn("do-not-leak", " ".join(env.values()))

    def test_a_stray_git_variable_from_the_shell_is_replaced(self):
        with mock.patch.dict(os.environ, SHELL, clear=True):
            env = self.syncer.env()
        self.assertEqual(env["GIT_DIR"], self.tmp.name + "/git")

    def test_missing_variables_are_left_out_not_emptied(self):
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True):
            env = self.syncer.env()
        self.assertNotIn("HOME", env)
        self.assertEqual(env["PATH"], "/usr/bin")


if __name__ == "__main__":
    unittest.main()
