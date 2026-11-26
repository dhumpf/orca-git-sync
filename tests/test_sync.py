"""Two simulated Macs syncing through a bare repo, with real git and no Orca."""

import json
import os
import subprocess
import unittest
from pathlib import Path

import fake_orca
from helpers import Mac, bare_repo, isolate_home, preset, push_files, remote_file, remote_files, remote_log, tmpdir


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        isolate_home(self.tmp.name)
        fake_orca.install(self.tmp.name)
        self.remote = bare_repo(self.tmp.name)
        self.a = Mac(self.tmp.name, "mac-a", self.remote)
        self.b = Mac(self.tmp.name, "mac-b", self.remote, user_id="other-id")

    def tearDown(self):
        self.tmp.cleanup()

    def assertClean(self, result):
        self.assertEqual(result.error, "", result.error)

    def test_first_push_to_empty_remote(self):
        self.a.save("filament", "PLA Fast", nozzle_temperature=["210"])
        r = self.a.sync()
        self.assertClean(r)
        self.assertEqual(r.committed, ["filament/PLA Fast.json"])
        self.assertTrue(r.pushed)
        self.assertEqual(len(remote_log(self.remote)), 1)

    def test_a_to_b_and_back(self):
        self.a.save("filament", "PLA Fast", nozzle_temperature=["210"])
        self.assertClean(self.a.sync())
        r = self.b.sync()
        self.assertClean(r)
        self.assertEqual(r.pulled, ["filament/PLA Fast.json"])
        self.assertEqual(self.b.read("filament", "PLA Fast")["nozzle_temperature"], ["210"])

        self.b.save("filament", "PLA Fast", nozzle_temperature=["215"])
        self.b.save("process", "0.20 Strong", wall_loops="4")
        self.assertClean(self.b.sync())
        r = self.a.sync()
        self.assertClean(r)
        self.assertEqual(r.pulled, ["filament/PLA Fast.json", "process/0.20 Strong.json"])
        self.assertEqual(self.a.read("filament", "PLA Fast")["nozzle_temperature"], ["215"])

    def test_delete_propagates(self):
        self.a.save("machine", "Ender 5", bed_temperature="60")
        self.a.sync()
        self.b.sync()
        (self.b.presets / "machine" / "Ender 5.json").unlink()
        self.assertClean(self.b.sync())
        r = self.a.sync()
        self.assertClean(r)
        self.assertEqual(self.a.files("machine"), [])

    def test_only_preset_json_is_tracked(self):
        self.a.save("filament", "PLA", nozzle_temperature=["205"])
        for extra in ("filament/PLA.info", "filament/PLA.json.bak-20261111", "user.lock", "_subscribed/x/y.json"):
            path = self.a.presets / extra
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
        r = self.a.sync()
        self.assertEqual(r.committed, ["filament/PLA.json"])
        self.b.sync()
        self.assertEqual(self.b.files("filament"), ["PLA.json"])

    def test_gitignore_from_the_remote_cannot_add_other_files(self):
        self.a.save("filament", "PLA", nozzle_temperature=["205"])
        self.assertClean(self.a.sync())
        push_files(self.tmp.name, self.remote, {".gitignore": "!/secret.txt\n!/filament/*\n"})
        (self.b.presets / "secret.txt").write_text("not for the repo")
        (self.b.presets / "filament" / "PLA.info").write_text("cloud ids")
        self.b.save("process", "Fine", layer_height="0.1")
        self.assertClean(self.b.sync())
        self.b.save("process", "Fine", layer_height="0.12")
        self.assertClean(self.b.sync())
        files = remote_files(self.remote)
        self.assertNotIn("secret.txt", files)
        self.assertNotIn("filament/PLA.info", files)
        self.assertIn("process/Fine.json", files)
        self.assertFalse((self.b.presets / ".gitignore").exists())

    def test_tracked_info_is_never_committed_deleted_or_pulled(self):
        self.a.save("filament", "PLA", nozzle_temperature=["205"])
        self.assertClean(self.a.sync())
        push_files(self.tmp.name, self.remote, {"filament/PLA.info": "remote ids\n", "notes.txt": "remote\n"})
        self.assertClean(self.a.sync())
        self.assertFalse((self.a.presets / "filament" / "PLA.info").exists(), "a lone .info was pulled")
        self.assertFalse((self.a.presets / "notes.txt").exists())
        self.b.sync()
        (self.b.presets / "filament" / "PLA.info").write_text("local ids\n")
        (self.b.presets / "notes.txt").write_text("local\n")
        self.b.save("filament", "PLA", nozzle_temperature=["210"])
        self.assertClean(self.b.sync())
        (self.b.presets / "filament" / "PLA.info").unlink()
        (self.b.presets / "notes.txt").unlink()
        self.b.save("filament", "PLA", nozzle_temperature=["215"])
        self.assertClean(self.b.sync())
        self.assertEqual(remote_file(self.remote, "filament/PLA.info"), "remote ids\n")
        self.assertEqual(remote_file(self.remote, "notes.txt"), "remote\n")
        self.assertIn('"215"', remote_file(self.remote, "filament/PLA.json"))

    def test_pull_writes_only_preset_json(self):
        push_files(
            self.tmp.name,
            self.remote,
            {
                "filament/PLA.json": "{}\n",
                "filament/sub/deep.json": "{}\n",
                "filament/run.sh": "x\n",
                "README.md": "x\n",
                ".gitignore": "x\n",
                "other/x.json": "{}\n",
            },
        )
        r = self.a.sync()
        self.assertClean(r)
        self.assertEqual(r.pulled, ["filament/PLA.json"])
        on_disk = sorted(str(p.relative_to(self.a.presets)) for p in self.a.presets.rglob("*") if p.is_file())
        self.assertEqual(on_disk, ["filament/PLA.json"])

    def test_odd_preset_names_sync_and_delete(self):
        names = ["-rf", "a b", "Ünïcode PLA", ":(glob)x", "[x]*?", "*"]
        for n in names:
            self.a.save("filament", n, nozzle_temperature=["200"])
        nested = self.a.presets / "filament" / "sub" / "deep.json"
        nested.parent.mkdir()
        nested.write_text("{}")
        self.assertClean(self.a.sync())
        self.assertEqual(sorted(remote_files(self.remote)), sorted(f"filament/{n}.json" for n in names))
        for n in ("-rf", "*"):
            (self.a.presets / "filament" / f"{n}.json").unlink()
        self.assertClean(self.a.sync())
        self.assertEqual(sorted(remote_files(self.remote)), sorted(f"filament/{n}.json" for n in names[1:5]))
        self.assertClean(self.b.sync())
        self.assertEqual(self.b.files("filament"), sorted(f"{n}.json" for n in names[1:5]))

    def test_many_presets_in_one_sync(self):
        for i in range(300):
            self.a.save("process", f"{i:03d} " + "long name " * 8, layer_height="0.2")
        r = self.a.sync()
        self.assertClean(r)
        self.assertEqual(len(r.committed), 300)
        self.assertEqual(len(remote_files(self.remote)), 300)

    def test_conflict_keeps_both_and_never_force_pushes(self):
        self.a.save("filament", "PETG", nozzle_temperature=["240"])
        self.a.sync()
        self.b.sync()
        self.a.save("filament", "PETG", nozzle_temperature=["245"])
        self.b.save("filament", "PETG", nozzle_temperature=["235"])
        self.assertClean(self.a.sync())
        head_before = remote_log(self.remote)[0].split()[0]

        r = self.b.sync()
        self.assertClean(r)
        self.assertTrue(r.pushed)
        self.assertEqual(len(r.conflicts), 1)
        path, copy = r.conflicts[0]
        self.assertEqual(path, "filament/PETG.json")
        self.assertRegex(copy, r"^filament/PETG \(git conflict \d{4}-\d\d-\d\d \d{4}\)\.json$")

        mine = self.b.read("filament", "PETG")
        self.assertEqual(mine["nozzle_temperature"], ["235"])
        theirs = (self.b.presets / copy).read_text()
        self.assertIn('"245"', theirs)
        self.assertNotIn("<<<<<<<", theirs + (self.b.presets / path).read_text())
        self.assertEqual(json.loads(theirs)["name"], copy.split("/")[1][:-5])
        self.assertEqual(json.loads(theirs)["filament_settings_id"], [copy.split("/")[1][:-5]])

        ancestor = subprocess.run(
            ["git", "--git-dir", str(self.remote), "merge-base", "--is-ancestor", head_before, "main"]
        )
        self.assertEqual(ancestor.returncode, 0, "remote history was rewritten")

        r = self.a.sync()
        self.assertClean(r)
        self.assertEqual(sorted(self.a.files("filament")), sorted(["PETG.json", copy.split("/")[1]]))
        self.assertEqual(self.a.read("filament", "PETG")["nozzle_temperature"], ["235"])

    def test_edit_vs_delete_keeps_the_edit(self):
        self.a.save("process", "Draft", layer_height="0.28")
        self.a.sync()
        self.b.sync()
        (self.a.presets / "process" / "Draft.json").unlink()
        self.a.sync()
        self.b.save("process", "Draft", layer_height="0.3")
        r = self.b.sync()
        self.assertClean(r)
        self.assertEqual(r.conflicts, [("process/Draft.json", None)])
        self.assertEqual(self.b.read("process", "Draft")["layer_height"], "0.3")

    def test_same_name_created_on_both_macs(self):
        self.a.save("filament", "TPU", nozzle_temperature=["220"])
        self.b.save("filament", "TPU", nozzle_temperature=["230"])
        self.assertClean(self.a.sync())
        r = self.b.sync()
        self.assertClean(r)
        self.assertEqual(len(r.conflicts), 1)
        self.assertEqual(len(self.b.files("filament")), 2)

    def test_push_race_retries_without_force(self):
        self.a.save("filament", "A1", nozzle_temperature=["200"])
        self.a.sync()
        self.b.sync()
        self.a.save("filament", "A2", nozzle_temperature=["200"])
        self.b.save("filament", "B1", nozzle_temperature=["200"])
        self.a.syncer.ensure_repo()
        self.a.syncer.commit_local()
        self.a.syncer.run("push")
        r = self.b.sync()
        self.assertClean(r)
        self.assertTrue(r.pushed)
        self.assertEqual(self.b.files("filament"), ["A1.json", "A2.json", "B1.json"])

    def test_unreachable_remote_keeps_local_commit(self):
        self.a.syncer.settings["repo_url"] = self.tmp.name + "/missing.git"
        self.a.save("filament", "PLA", nozzle_temperature=["205"])
        r = self.a.sync()
        self.assertTrue(r.offline)
        self.assertIn("push next time", r.error)
        self.assertTrue(self.a.syncer.has_head())
        self.a.syncer.settings["repo_url"] = str(self.remote)
        r = self.a.sync()
        self.assertClean(r)
        self.assertTrue(r.pushed)
        self.assertEqual(len(remote_log(self.remote)), 1)

    def test_overlapping_syncs_on_one_folder(self):
        import threading

        import orca_git_sync as ogs

        twin = ogs.Syncer(self.a.presets, self.a.syncer.git_dir, self.a.syncer.settings, git="git")
        self.a.save("filament", "PLA", nozzle_temperature=["205"])
        results = []
        threads = [threading.Thread(target=lambda s=s: results.append(s.sync())) for s in (self.a.syncer, twin) * 3]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual([r.error for r in results], [""] * 6)
        self.assertEqual(len(remote_log(self.remote)), 1)

    def test_missing_repo_url(self):
        self.a.syncer.settings["repo_url"] = ""
        self.assertIn("no repo URL", self.a.sync().error)

    def test_branch_setting(self):
        self.a.syncer.settings["branch"] = "presets"
        self.a.save("filament", "PLA", nozzle_temperature=["205"])
        self.assertTrue(self.a.sync().pushed)
        heads = subprocess.run(
            ["git", "--git-dir", str(self.remote), "for-each-ref", "--format=%(refname)"],
            capture_output=True,
            text=True,
        ).stdout.split()
        self.assertEqual(heads, ["refs/heads/presets"])

    def test_nothing_lands_in_the_orca_folder(self):
        self.a.save("filament", "PLA", nozzle_temperature=["205"])
        self.a.sync()
        self.assertFalse((self.a.presets / ".git").exists())
        self.assertFalse((self.a.presets / ".gitignore").exists())

    def raw_commit(self, change):
        """Commit to the remote through plain git, for entries push_files cannot write."""
        work = Path(self.tmp.name) / "raw-clone"
        if not work.exists():
            subprocess.run(["git", "clone", "-q", str(self.remote), str(work)], check=True, capture_output=True)
        git = ["git", "-C", str(work), "-c", "user.name=raw", "-c", "user.email=raw@example.com"]
        subprocess.run(git + ["pull", "-q", "--no-rebase", "origin", "main"], capture_output=True)
        change(work, git)
        subprocess.run(git + ["commit", "-q", "-m", "raw"], check=True)
        subprocess.run(git + ["push", "-q", "origin", "HEAD:main"], check=True, capture_output=True)

    def test_folders_named_like_a_preset_never_break_a_sync(self):
        self.a.save("filament", "PLA", nozzle_temperature=["200"])
        self.assertClean(self.a.sync())
        folder = self.a.presets / "filament" / "old.json"
        folder.mkdir()
        (folder / "x.json").write_text("{}\n")
        self.a.save("process", "New", layer_height="0.2")
        self.assertClean(self.a.sync())
        self.assertEqual(sorted(remote_files(self.remote)), ["filament/PLA.json", "process/New.json"])

        def gitlink(work, git):
            entry = "160000," + "1" * 40 + ",filament/sub.json"
            subprocess.run(git + ["update-index", "--add", "--cacheinfo", entry], check=True)

        self.raw_commit(gitlink)
        for mac in (self.a, self.b):
            self.assertClean(mac.sync())
            sub = mac.presets / "filament" / "sub.json"
            self.assertFalse(sub.is_file())
            self.assertEqual(list(sub.iterdir()) if sub.is_dir() else [], [])
        self.b.save("process", "Other", layer_height="0.3")
        self.assertClean(self.b.sync())
        self.assertClean(self.a.sync())
        self.assertEqual(self.a.read("process", "Other")["layer_height"], "0.3")

    def test_symlinks_are_never_followed_or_staged(self):
        victim = Path(self.tmp.name) / "victim.txt"
        victim.write_text("precious\n")
        self.a.save("filament", "PLA", nozzle_temperature=["200"])
        self.assertClean(self.a.sync())

        def link(work, git):
            os.symlink(str(victim), work / "filament" / "Evil.json")
            subprocess.run(git + ["add", "-A"], check=True)

        self.raw_commit(link)
        for mac in (self.a, self.b):
            self.assertClean(mac.sync())
            pulled = mac.presets / "filament" / "Evil.json"
            self.assertFalse(pulled.is_symlink(), "a link from the repo was checked out as a link")
            pulled.write_text(preset("filament", "Evil", nozzle_temperature=["1"]))
        self.assertEqual(victim.read_text(), "precious\n")

        os.symlink(str(victim), self.a.presets / "process" / "Local.json")
        r = self.a.sync()
        self.assertClean(r)
        self.assertEqual(r.skipped, ["process/Local.json"])
        self.assertIn("skipped 1", r.summary())
        self.assertNotIn("process/Local.json", remote_files(self.remote))
        self.assertEqual(victim.read_text(), "precious\n")

    def test_folder_named_like_a_preset_in_the_repo_is_not_written(self):
        names = ["PLA [0.4] fast", "Ünïcødé spool", "a b", "*?["]
        push_files(
            self.tmp.name,
            self.remote,
            {
                "filament/archive.json/notes.txt": "x\n",
                "process/old.json/deep.json": "{}\n",
                **{f"filament/{n}.json": preset("filament", n, nozzle_temperature=["200"]) for n in names},
            },
        )
        r = self.a.sync()
        self.assertClean(r)
        self.assertEqual(r.pulled, sorted(f"filament/{n}.json" for n in names))
        on_disk = sorted(str(p.relative_to(self.a.presets)) for p in self.a.presets.rglob("*") if not p.is_dir())
        self.assertEqual(on_disk, sorted(f"filament/{n}.json" for n in names))
        self.assertFalse((self.a.presets / "filament" / "archive.json").exists())

    def test_nested_path_is_never_a_preset(self):
        import orca_git_sync as ogs

        self.assertTrue(ogs.is_preset("filament/x.json"))
        for path in ("filament/sub/x.json", "filament/a.json/b.json", "other/x.json", "x.json", "filament/x.info"):
            self.assertFalse(ogs.is_preset(path), path)


if __name__ == "__main__":
    unittest.main()
