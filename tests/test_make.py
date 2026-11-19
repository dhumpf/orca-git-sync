"""make version-check takes TAG as data, never as recipe text."""

import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def version():
    with open(os.path.join(ROOT, "orca_git_sync.py"), encoding="utf-8") as f:
        return re.search(r'^VERSION = "(.*)"', f.read(), re.M).group(1)


def make(*args):
    return subprocess.run(["make", "-s", "-C", ROOT, *args], capture_output=True, text=True)


class VersionCheckTest(unittest.TestCase):
    def test_matching_tag_passes(self):
        r = make("version-check", f"TAG=v{version()}")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_mismatching_tag_fails(self):
        r = make("version-check", "TAG=v0.0.0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("does not match", r.stdout + r.stderr)

    def test_malformed_tags_fail(self):
        for tag in (version(), f"v{version()}-rc1", f"v{version()} ", "v1.2", "v1.2.3.4", ""):
            with self.subTest(tag=tag):
                self.assertNotEqual(make("version-check", f"TAG={tag}").returncode, 0)

    def test_tag_is_not_run_by_the_shell(self):
        for tag in (
            f"v`echo INJECTED >&2`{version()}",
            f"v$$(echo INJECTED >&2){version()}",
            f'v{version()}" ; echo INJECTED >&2 ; "',
        ):
            with self.subTest(tag=tag):
                r = make("version-check", f"TAG={tag}")
                self.assertNotEqual(r.returncode, 0)
                self.assertNotIn("INJECTED", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
