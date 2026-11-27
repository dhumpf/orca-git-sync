"""Git settings live in the clone's own config. The plugin reads no environment and no computer name."""

import ast
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import fake_orca
from helpers import HERE, Mac, bare_repo, isolate_home, raw_git, remote_log, tmpdir

SOURCE = HERE.parent / "orca_git_sync.py"

# What a plugin review reads as environment, computer name or user home access.
BANNED_NAMES = {
    "environ",
    "getenv",
    "putenv",
    "environb",
    "gethostname",
    "node",
    "uname",
    "which",
    "expanduser",
    "home",
}
BANNED_MODULES = {"platform", "shutil", "socket", "getpass"}


def banned(source):
    hits = []
    for n in ast.walk(ast.parse(source)):
        if isinstance(n, ast.Attribute) and n.attr in BANNED_NAMES:
            hits.append(f"{n.lineno}: .{n.attr}")
        elif isinstance(n, ast.Import):
            hits += [f"{n.lineno}: import {a.name}" for a in n.names if a.name.split(".")[0] in BANNED_MODULES]
        elif isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] in BANNED_MODULES | {"os"}:
            hits.append(f"{n.lineno}: from {n.module}")
        elif isinstance(n, ast.keyword) and n.arg == "env":
            hits.append(f"{n.lineno}: env=")
    return hits


class SourceTest(unittest.TestCase):
    def test_no_environment_hostname_or_home_reads(self):
        self.assertEqual(banned(SOURCE.read_text()), [])

    def test_the_check_fires_on_each_pattern(self):
        for line in (
            "import os\nos.environ['X']",
            "import os\nos.getenv('X')",
            "import platform\nplatform.node()",
            "import shutil\nshutil.which('git')",
            "from os import environ",
            "import subprocess\nsubprocess.run([], env={})",
            "from pathlib import Path\nPath.home()",
        ):
            self.assertNotEqual(banned(line), [], line)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        isolate_home(self.tmp.name)
        fake_orca.install(self.tmp.name)
        import orca_git_sync

        self.ogs = orca_git_sync
        self.remote = bare_repo(self.tmp.name)
        self.a = Mac(self.tmp.name, "mac-a", self.remote)

    def tearDown(self):
        self.tmp.cleanup()

    def git_config(self, *args):
        return subprocess.run(
            ["git", "config", *args], cwd=self.a.syncer.run_dir, capture_output=True, text=True, check=True
        ).stdout.splitlines()

    def test_settings_are_read_back_by_git_from_the_clone(self):
        self.a.syncer.settings.update(author_name='A "quoted" \\ name', branch="presets")
        self.a.syncer.ensure_repo()
        self.assertEqual(self.git_config("--get", "remote.origin.url"), [str(self.remote)])
        self.assertEqual(self.git_config("--get", "user.name"), ['A "quoted" \\ name'])
        self.assertEqual(self.git_config("--get", "remote.origin.push"), ["HEAD:refs/heads/presets"])
        self.assertEqual(
            self.git_config("--local", "--get-all", "credential.helper"),
            ["", self.ogs.credential_helper(), self.ogs.QUIT_HELPER],
        )
        self.assertEqual(self.git_config("--get", "credential.interactive"), ["false"])
        self.assertEqual(self.git_config("--get", "http.followRedirects"), ["false"])
        self.assertEqual(self.git_config("--get", "core.worktree"), [str(self.a.presets)])

    def test_default_author_has_no_computer_name(self):
        self.a.save("filament", "PLA", x=["1"])
        self.assertEqual(self.a.sync().error, "")
        log = subprocess.run(
            ["git", "--git-dir", str(self.remote), "log", "--format=%an|%cn|%s", "main"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(log, "OrcaSlicer|OrcaSlicer|presets: 1 change(s)")

    def test_rewrite_keeps_what_git_init_wrote_and_replaces_only_its_own_part(self):
        s = self.a.syncer
        s.ensure_repo()
        cfg = s.git_dir / "config"
        head = cfg.read_text().split(self.ogs.MANAGED)[0]
        self.assertIn("repositoryformatversion", head)
        s.settings["repo_url"] = "https://git.example.com/new.git"
        s.ensure_repo()
        text = cfg.read_text()
        self.assertEqual(text.count(self.ogs.MANAGED), 1)
        self.assertTrue(text.startswith(head))
        self.assertEqual(self.git_config("--get", "remote.origin.url"), ["https://git.example.com/new.git"])

    def test_a_0_3_2_clone_upgrades_in_place(self):
        # 0.3.2 made the clone with GIT_DIR and GIT_WORK_TREE and kept settings out of its config.
        s = self.a.syncer
        s.git_dir.mkdir(parents=True)
        raw_git(self.a, "init", "-q")
        self.a.save("process", "Old", x="1")
        raw_git(self.a, "add", "process/Old.json")
        raw_git(self.a, "commit", "-q", "-m", "from 0.3.2")
        before = (s.git_dir / "config").read_text()
        self.a.save("process", "New", x="2")
        r = self.a.sync()
        self.assertEqual(r.error, "")
        self.assertTrue(r.pushed)
        self.assertEqual(len(remote_log(self.remote)), 2)
        self.assertTrue((s.git_dir / "config").read_text().startswith(before))
        self.assertFalse((self.a.presets / ".git").exists(), "nothing is added to the preset folder")

    def test_safe_bare_repository_explicit_still_syncs(self):
        (Path(os.environ["HOME"]) / ".gitconfig").write_text("[safe]\n\tbareRepository = explicit\n")
        self.a.save("filament", "PLA", x=["1"])
        r = self.a.sync()
        self.assertEqual(r.error, "")
        self.assertTrue(r.pushed)

    def test_git_inherits_the_environment_unchanged(self):
        # Documented: the plugin passes no env, so a GIT_* variable the user set reaches git.
        with mock.patch.object(subprocess, "run", wraps=subprocess.run) as run:
            self.a.syncer.ensure_repo()
        self.assertTrue(run.call_args_list)
        for call in run.call_args_list:
            self.assertNotIn("env", call.kwargs)
            self.assertTrue(call.kwargs["start_new_session"])

    def test_token_save_before_any_preset_folder_exists(self):
        record = Path(self.tmp.name) / "helper-got"
        self.ogs.credential_helper = lambda: f"!f() {{ cat >> '{record}'; }}; f"
        s = self.ogs.Syncer(
            Path(self.tmp.name) / "missing",
            Path(self.tmp.name) / "store" / "git-x",
            {"repo_url": "https://git.example.com/a.git"},
            git="git",
        )
        s.save_token("tok")
        self.assertIn("password=tok", record.read_text())


if __name__ == "__main__":
    unittest.main()
