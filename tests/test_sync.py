"""Two simulated Macs syncing through a bare repo, with real git and no Orca."""

import json
import subprocess
import unittest

import fake_orca
from helpers import Mac, bare_repo, isolate_home, remote_log, tmpdir


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

        twin = ogs.Syncer(self.a.presets, self.a.syncer.git_dir, self.a.syncer.settings, git="git", host="twin")
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


if __name__ == "__main__":
    unittest.main()
