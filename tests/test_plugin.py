"""Host glue against a fake `orca` module, plus the permission contract."""

import json
import os
import subprocess
import sys
import textwrap
import time
import types
import unittest
from pathlib import Path

import fake_orca
from helpers import HERE, Mac, bare_repo, isolate_home, tmpdir


def load_plugin(tmp, bundle=None):
    orca = fake_orca.install(Path(tmp) / "orca_plugins" / "plugin_data" / "orca_git_sync", bundle)
    import orca_git_sync

    return orca, orca_git_sync


class FakePreset:
    def __init__(self, file, user=True):
        self.file, self.user = file, user

    def is_user(self):
        return self.user


class FakeCollection:
    def __init__(self, presets):
        self.presets = presets

    def size(self):
        return len(self.presets)

    def preset(self, i):
        return self.presets[i]


class PluginTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        isolate_home(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_registration_declares_every_git_call(self):
        orca, ogs = load_plugin(self.tmp.name)
        ogs.GitSyncPackage().register_capabilities()
        self.assertEqual(orca.capabilities, [ogs.GitSync])
        perms = orca.permissions[0]
        self.assertTrue(perms["threading"])
        self.assertEqual(len(perms["process"]), len(ogs.GIT_ARGS))
        self.assertIn(str([ogs.find_git(), "push", "-q", "origin"]), perms["process"])
        self.assertFalse(any("--force" in c or "-f'" in c or "+refs" in c for c in perms["process"]))

    def test_preset_folder_from_bundle(self):
        root = Path(self.tmp.name)
        f = str(root / "user" / "abc" / "filament" / "PLA.json")
        bundle = types.SimpleNamespace(
            filaments=FakeCollection([FakePreset("/sys/x.json", user=False), FakePreset(f)]),
            prints=FakeCollection([]),
            printers=FakeCollection([]),
        )
        _, ogs = load_plugin(self.tmp.name, bundle)
        self.assertEqual(ogs.preset_folder(root), root / "user" / "abc")
        self.assertEqual(ogs.preset_folder(root, "xyz"), root / "user" / "xyz")

    def test_preset_folder_without_bundle(self):
        root = Path(self.tmp.name)
        _, ogs = load_plugin(self.tmp.name)
        (root / "user" / "default").mkdir(parents=True)
        self.assertEqual(ogs.preset_folder(root), root / "user" / "default")
        (root / "user" / "d198").mkdir(parents=True)
        self.assertEqual(ogs.preset_folder(root), root / "user" / "d198")
        (root / "user" / "e2f0").mkdir(parents=True)
        with self.assertRaises(ogs.GitError):
            ogs.preset_folder(root)
        self.assertEqual(ogs.preset_folder(root, "e2f0"), root / "user" / "e2f0")

    def capability(self, remote):
        orca, ogs = load_plugin(self.tmp.name)
        mac = Mac(self.tmp.name, ".", remote)
        cap = ogs.GitSync()
        cap.config = fake_orca.config(repo_url=str(remote), preset_folder="user-1234")
        ogs.data_dir = lambda: Path(self.tmp.name)
        ogs.DEBOUNCE_S = 0.05
        return orca, ogs, mac, cap

    def wait_for(self, check, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if check():
                return True
            time.sleep(0.05)
        return False

    def test_preset_saved_event_commits_and_pushes(self):
        remote = bare_repo(self.tmp.name)
        orca, ogs, mac, cap = self.capability(remote)
        mac.save("filament", "PLA Fast", nozzle_temperature=["210"])
        cap.on_lifecycle_event(orca.LifecycleEvent.PresetSelected, None)
        time.sleep(0.3)
        self.assertEqual(cap.last, "Not synced yet.")
        cap.on_lifecycle_event(orca.LifecycleEvent.PresetSaved, None)
        self.assertTrue(self.wait_for(lambda: "pushed" in cap.last), cap.last)

    def test_on_load_pulls_and_notifies(self):
        remote = bare_repo(self.tmp.name)
        orca, ogs, mac, cap = self.capability(remote)
        other = Mac(self.tmp.name, "other", remote)
        other.save("process", "0.20 Strong", wall_loops="4")
        other.sync()
        cap.on_load()
        self.assertTrue(self.wait_for(lambda: cap.pending), "startup result not queued")
        self.assertEqual(orca.notifications, [], "notified during startup")
        cap.on_lifecycle_event(orca.LifecycleEvent.PresetSelected, None)
        self.assertTrue(self.wait_for(lambda: orca.notifications), "no notification")
        text = orca.notifications[0][1]
        self.assertIn("pulled 1", text)
        self.assertIn("Restart Orca", text)
        self.assertTrue((mac.presets / "process" / "0.20 Strong.json").exists())

    def test_on_unload_syncs_synchronously(self):
        remote = bare_repo(self.tmp.name)
        orca, ogs, mac, cap = self.capability(remote)
        mac.save("machine", "Ender 5", bed_temperature="60")
        cap.on_unload()
        self.assertIn("pushed", cap.last)

    def test_conflict_warns_user(self):
        remote = bare_repo(self.tmp.name)
        orca, ogs, mac, cap = self.capability(remote)
        other = Mac(self.tmp.name, "other", remote)
        other.save("filament", "PETG", nozzle_temperature=["240"])
        other.sync()
        cap.on_unload()
        other.save("filament", "PETG", nozzle_temperature=["245"])
        other.sync()
        mac.save("filament", "PETG", nozzle_temperature=["235"])
        cap.on_unload()
        self.assertTrue(self.wait_for(lambda: orca.notifications))
        level, text = orca.notifications[-1]
        self.assertEqual(level, orca.host.ui.NotificationLevel.WarningNotificationLevel)
        self.assertIn("PETG (git conflict", text)

    def test_window_sync_and_status(self):
        remote = bare_repo(self.tmp.name)
        orca, ogs, mac, cap = self.capability(remote)
        self.assertTrue(cap.execute().ok)
        window = orca.windows[0]
        cap.on_message({"command": "status"})
        self.assertEqual(window.posts[-1], {"status": "Not synced yet."})
        mac.save("filament", "PLA", nozzle_temperature=["205"])
        cap.on_message({"command": "sync"})
        self.assertTrue(self.wait_for(lambda: "pushed" in str(window.posts[-1])))

    def test_token_goes_to_credential_helper_only(self):
        fake_git = Path(self.tmp.name) / "fake-git"
        record = Path(self.tmp.name) / "credential-stdin"
        fake_git.write_text(f"#!/bin/sh\necho \"$@\" > '{record}.args'\ncat > '{record}'\n")
        fake_git.chmod(0o755)
        orca, ogs = load_plugin(self.tmp.name)
        cfg = {"repo_url": "https://git.example.com:3000/team/presets.git", "username": "daniel"}
        syncer = ogs.Syncer(self.tmp.name, Path(self.tmp.name) / "g", cfg, git=str(fake_git))
        syncer.save_token("s3cret-token\n")
        self.assertEqual(record.with_suffix(".args").read_text().strip(), "credential approve")
        self.assertEqual(
            record.read_text(),
            "protocol=https\nhost=git.example.com:3000\nusername=daniel\npassword=s3cret-token\n\n",
        )
        self.assertNotIn("s3cret", json.dumps(cfg))
        with self.assertRaises(ogs.GitError):
            syncer.save_token("a\nb")
        syncer.settings["repo_url"] = "http://plain.example.com/x.git"
        with self.assertRaises(ogs.GitError):
            syncer.save_token("tok")

    def test_credential_helper_per_platform(self):
        _, ogs = load_plugin(self.tmp.name)

        def none(p):
            return False

        def libsecret(p):
            return p.endswith("git-credential-libsecret")

        self.assertEqual(ogs.credential_helper("darwin", none), "osxkeychain")
        self.assertEqual(ogs.credential_helper("win32", none), "manager")
        self.assertEqual(ogs.credential_helper("linux", libsecret), "libsecret")
        self.assertTrue(ogs.credential_helper("linux", none).startswith("cache"))

    def test_find_git_per_platform(self):
        _, ogs = load_plugin(self.tmp.name)

        def only(want):
            return lambda p: p == want

        mac = "/opt/homebrew/bin/git"
        self.assertEqual(ogs.find_git("darwin", only(mac), lambda n: None), mac)
        win = r"C:\Program Files\Git\cmd\git.exe"
        self.assertEqual(ogs.find_git("win32", only(win), lambda n: None), win)
        self.assertEqual(ogs.find_git("linux", only("/usr/bin/git"), lambda n: None), "/usr/bin/git")
        self.assertEqual(ogs.find_git("linux", lambda p: False, lambda n: "/x/bin/git"), "/x/bin/git")
        self.assertEqual(ogs.find_git("win32", lambda p: False, lambda n: None), "git")

    def test_messages_have_no_semicolons_or_em_dashes(self):
        _, ogs = load_plugin(self.tmp.name)
        r = ogs.SyncResult(
            committed=["a"], pulled=["b"], conflicts=[("x/P.json", "x/P (git conflict).json")], pushed=True
        )
        for text in (ogs.GitSync.message(r, True), ogs.CONFIG_UI, ogs.WINDOW_UI):
            prose = text.replace("&gt;", ">")
            self.assertNotIn(chr(0x2014), prose)
        self.assertNotIn(";", ogs.GitSync.message(r, True))


AUDIT_CHILD = textwrap.dedent(
    """
    import json, subprocess, sys
    sys.path[:0] = [{here!r}, {root!r}]
    from helpers import Mac, bare_repo, isolate_home
    import fake_orca
    tmp = sys.argv[1]
    isolate_home(tmp)
    fake_orca.install(tmp)
    import orca_git_sync as ogs
    seen, fs = [], []
    watching = [False]
    FS_EVENTS = frozenset(["open", "os.rename", "os.mkdir", "os.remove", "os.rmdir", "shutil.rmtree", "os.chmod"])
    def hook(event, args):
        if event == "subprocess.Popen":
            seen.append(str(args[1]))
        elif watching[0] and event in FS_EVENTS:
            fs.append(args[0] if isinstance(args[0], str) else repr(args[0]))
    sys.addaudithook(hook)
    remote = bare_repo(tmp)
    a, b = Mac(tmp, "a", remote), Mac(tmp, "b", remote)
    for m in (a, b):
        m.syncer.git = ogs.find_git()
    a.save("filament", "P", x=["1"])
    watching[0] = True
    a.sync(); b.sync()
    watching[0] = False
    a.save("filament", "P", x=["2"]); b.save("filament", "P", x=["3"])
    watching[0] = True
    a.sync(); r = b.sync()
    watching[0] = False
    assert r.conflicts and not r.error, r
    git_seen = [s for s in seen if ogs.find_git() in s]
    sync_fs = list(fs)
    watching[0] = True
    subprocess.run([ogs.find_git(), "--version"], capture_output=True)
    watching[0] = False
    control_fs = fs[len(sync_fs):]
    import sysconfig
    stdlib = __import__("os").path.realpath(sysconfig.get_paths()["stdlib"])
    subprocess.run([ogs.find_git(), "--git-dir", str(remote), "log"], capture_output=True)
    print(json.dumps({{"declared": ogs.declared_commands(ogs.find_git()), "seen": git_seen,
                      "control": str([ogs.find_git(), "--git-dir", str(remote), "log"]), "all": seen,
                      "fs": [p for p in sync_fs if not __import__("os").path.realpath(p).startswith(stdlib)], "control_fs": control_fs, "root": str(__import__("pathlib").Path(tmp).resolve())}}))
    """
)


class AuditContractTest(unittest.TestCase):
    """Every git call must match a declared process grant verbatim, like the host hook does."""

    def test_every_popen_target_is_declared(self):
        with tmpdir() as tmp:
            code = AUDIT_CHILD.format(here=str(HERE), root=str(HERE.parent))
            proc = subprocess.run([sys.executable, "-c", code, tmp], capture_output=True, text=True, env=os.environ)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = json.loads(proc.stdout.strip().splitlines()[-1])
        declared = set(out["declared"])
        self.assertGreater(len(out["seen"]), 10, "audit hook saw too few git calls")
        undeclared = [s for s in out["seen"] if s not in declared]
        self.assertEqual(undeclared, [])
        self.assertIn(out["control"], out["all"], "negative control did not reach the hook")
        self.assertNotIn(out["control"], declared)

    def test_file_access_stays_in_the_data_dir(self):
        with tmpdir() as tmp:
            code = AUDIT_CHILD.format(here=str(HERE), root=str(HERE.parent))
            proc = subprocess.run([sys.executable, "-c", code, tmp], capture_output=True, text=True, env=os.environ)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertGreater(len(out["fs"]), 5, "audit hook saw too few file events")
        roots = (out["root"], out["root"].replace("/private/", "/", 1))
        outside = [p for p in out["fs"] if not p.startswith(roots)]
        self.assertEqual(outside, [], "the host denies open() on a bare fd and paths outside data_dir")
        self.assertTrue(
            any(not p.startswith("/") for p in out["control_fs"]), "control: capture_output opens a bare fd"
        )


if __name__ == "__main__":
    unittest.main()
