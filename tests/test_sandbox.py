"""Sync under the OrcaSlicer 2.5 denied-path rule, which no grant or retry can lift."""

import json
import os
import subprocess
import sys
import textwrap
import unittest

from helpers import HERE, tmpdir

# PluginAuditManager::is_denied_path in OrcaSlicer v2.5.2: a denied base name, or a component
# below data_dir() that is a denied keyword or ends in .conf or .ini, fails every fs event.
SANDBOX_CHILD = textwrap.dedent(
    """
    import json, os, subprocess, sys
    sys.path[:0] = [{here!r}, {root!r}]
    from pathlib import PurePath
    from helpers import Mac, bare_repo, isolate_home
    import fake_orca
    tmp = sys.argv[1]
    isolate_home(tmp)
    fake_orca.install(tmp)
    import orca_git_sync as ogs

    DATA_DIR = os.path.realpath(tmp)
    NAMES = ("orcaslicer.conf", "gcodeviewer.conf", "orcaslicer.ini", "gcodeviewer.ini",
             "orca_refresh_token.sec", ".install_state.json")
    KEYWORDS = {{"secret", "secrets", "cert", "certs", "certificate", "certificates", "conf", "config"}}
    FS = {{"open": 1, "os.rename": 2, "os.mkdir": 1, "os.remove": 1, "os.rmdir": 1, "os.listdir": 1,
           "os.scandir": 1, "os.chmod": 1, "os.utime": 1, "os.truncate": 1, "os.link": 2, "os.symlink": 2}}
    judging, denied = [False], []

    def is_denied(target):
        path = os.path.realpath(target)
        if PurePath(path).name.lower().startswith(NAMES):
            return True
        if not (path + os.sep).startswith(DATA_DIR + os.sep):
            return False
        for part in PurePath(os.path.relpath(path, DATA_DIR)).parts:
            part = part.lower()
            if part in KEYWORDS or part.endswith((".conf", ".ini")):
                return True
        return False

    def hook(event, args):
        if not judging[0] or event not in FS:
            return
        for target in args[: FS[event]]:
            if isinstance(target, bytes):
                target = os.fsdecode(target)
            if isinstance(target, str) and is_denied(target):
                denied.append(target)
                raise PermissionError(
                    f'Plugin attempted audited operation "{{event}}" without permission: denied path: {{target}}'
                )

    sys.addaudithook(hook)

    def judged(fn, *a):
        judging[0] = True
        try:
            return fn(*a)
        finally:
            judging[0] = False

    def git(mac, *args):
        if not mac.syncer.run_dir.is_dir():
            return []
        return subprocess.run(["git", "config", *args], cwd=mac.syncer.run_dir, capture_output=True,
                              text=True).stdout.splitlines()

    out = {{}}
    remote = bare_repo(tmp)

    # Control: the hook refuses a file named config inside the data dir.
    probe = os.path.join(tmp, "probe", "config")
    os.makedirs(os.path.dirname(probe))
    try:
        judged(open, probe, "w")
        out["control"] = "allowed"
    except PermissionError as e:
        out["control"] = str(e)
    denied.clear()

    a, b = Mac(tmp, "a", remote), Mac(tmp, "b", remote)
    a.save("filament", "P", x=["1"])
    out["new_clone"] = judged(a.sync).summary()
    out["second_mac"] = judged(b.sync).summary()
    a.save("filament", "P", x=["2"]); b.save("filament", "P", x=["3"])
    judged(a.sync)
    out["conflict"] = judged(b.sync).summary()

    a.syncer.settings.update(author_name="Renamed", repo_url=str(remote))
    a.save("process", "Q", x="1")
    out["settings_change"] = judged(a.sync).summary()
    out["author"] = git(a, "--get", "user.name")
    out["origin_urls"] = git(a, "--get-all", "remote.origin.url")
    out["origin_fetch"] = git(a, "--get-all", "remote.origin.fetch")
    out["origin_push"] = git(a, "--get-all", "remote.origin.push")
    out["denied"] = list(denied)
    print(json.dumps(out))
    """
)

# The tail 0.4.0 appended to the clone config, as a clone has it after its last good 0.4.0 sync.
V040_TAIL = """# orca-git-sync: settings below are rewritten on every sync
[core]
\tbare = "false"
\tworktree = "{tree}"
\thooksPath = "{hooks}"
[remote "origin"]
\turl = "{url}"
\tfetch = "+refs/heads/main:refs/ogs/remote"
\tpush = "HEAD:refs/heads/main"
[credential]
\thelper = ""
\thelper = "osxkeychain"
\thelper = "!f() {{ echo quit=1; }}; f"
\tinteractive = "false"
[user]
\tname = "Old Author"
\temail = "old@example.com"
"""

UPGRADE_CHILD = textwrap.dedent(
    """
    import json, os, subprocess, sys
    sys.path[:0] = [{here!r}, {root!r}]
    from helpers import Mac, bare_repo, isolate_home, raw_git
    import fake_orca
    tmp = sys.argv[1]
    isolate_home(tmp)
    fake_orca.install(tmp)
    import orca_git_sync as ogs
    from test_sandbox import V040_TAIL

    remote = bare_repo(tmp)
    a = Mac(tmp, "a", remote)
    s = a.syncer
    # A 0.4.0 clone, written before the hook goes in: init by GIT_DIR, the plugin block appended.
    s.git_dir.mkdir(parents=True)
    raw_git(a, "init", "-q")
    a.save("process", "Old", x="1")
    raw_git(a, "add", "process/Old.json")
    raw_git(a, "commit", "-q", "-m", "from 0.4.0")
    cfg = s.git_dir / "config"
    cfg.write_text(cfg.read_text() + V040_TAIL.format(tree=a.presets, hooks=s.hooks_dir, url=remote))
    s.run_dir.mkdir(parents=True)
    (s.run_dir / ".git").write_text(f"gitdir: {{s.git_dir}}\\n")

    def hook(event, args):
        if event in ("open", "os.rename") and any(
            isinstance(t, str) and os.path.basename(t) == "config" for t in args[:2]
        ):
            raise PermissionError(f'Plugin attempted audited operation "{{event}}" without permission: denied path')

    sys.addaudithook(hook)
    a.save("process", "New", x="2")
    r = s.sync()

    def git(*args):
        return subprocess.run(["git", "config", *args], cwd=s.run_dir, capture_output=True, text=True).stdout.splitlines()

    log = subprocess.run(["git", "--git-dir", str(remote), "log", "--format=%H", "main"], capture_output=True, text=True)
    print(json.dumps({{
        "summary": r.summary(),
        "origin_urls": git("--get-all", "remote.origin.url"),
        "origin_fetch": git("--get-all", "remote.origin.fetch"),
        "helpers": git("--get-all", "credential.helper"),
        "author": git("--get", "user.name"),
        "remote_commits": len(log.stdout.split()),
    }}))
    """
)


def run_child(code, tmp):
    proc = subprocess.run([sys.executable, "-c", code, tmp], capture_output=True, text=True, env=os.environ)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr)
    return json.loads(proc.stdout.strip().splitlines()[-1])


class SandboxTest(unittest.TestCase):
    """Orca 2.5 refuses every Python open or rename of a path component named `config`."""

    def test_sync_never_touches_a_denied_path(self):
        with tmpdir() as tmp:
            out = run_child(SANDBOX_CHILD.format(here=str(HERE), root=str(HERE.parent)), tmp)
        self.assertIn("denied path", out["control"], "control: the hook did not refuse a config file")
        for step in ("new_clone", "second_mac", "conflict", "settings_change"):
            self.assertNotIn("failed", out[step], f"{step}: {out[step]}")
        self.assertIn("conflict", out["conflict"])
        self.assertEqual(out["denied"], [])
        self.assertEqual(out["author"], ["Renamed"])
        self.assertEqual(len(out["origin_urls"]), 1)
        self.assertEqual(len(out["origin_fetch"]), 1)
        self.assertEqual(len(out["origin_push"]), 1)

    def test_a_0_4_0_clone_syncs_and_keeps_one_remote(self):
        with tmpdir() as tmp:
            out = run_child(UPGRADE_CHILD.format(here=str(HERE), root=str(HERE.parent)), tmp)
        self.assertEqual(out["summary"], "Git Sync: committed 1, pushed")
        self.assertEqual(out["remote_commits"], 2)
        self.assertEqual(out["origin_fetch"], ["+refs/heads/main:refs/ogs/remote"])
        self.assertEqual(len(out["origin_urls"]), 1)
        self.assertEqual(out["author"], ["OrcaSlicer"])
        self.assertEqual(out["helpers"][-1], "!f() { echo quit=1; }; f")


if __name__ == "__main__":
    unittest.main()
