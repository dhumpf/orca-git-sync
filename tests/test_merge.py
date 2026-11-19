"""Merges with files outside the preset dirs, open merges and message size."""

import subprocess
import unittest

import fake_orca
from helpers import (
    Mac,
    bare_repo,
    isolate_home,
    old_clone,
    preset,
    push_files,
    raw_git,
    remote_file,
    remote_files,
    remote_log,
    tmpdir,
)


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        isolate_home(self.tmp.name)
        fake_orca.install(self.tmp.name)
        self.remote = bare_repo(self.tmp.name)
        self.a = Mac(self.tmp.name, "mac-a", self.remote)
        push_files(
            self.tmp.name,
            self.remote,
            {
                "notes.txt": "base\n",
                "filament/PLA.info": "base ids\n",
                "filament/PLA.json": preset("filament", "PLA", nozzle_temperature=["200"]),
                "process/Fine.json": preset("process", "Fine", layer_height="0.1"),
                "filament/PETG.json": preset("filament", "PETG", nozzle_temperature=["240"]),
            },
        )
        old_clone(self.a, self.remote)

    def tearDown(self):
        self.tmp.cleanup()

    def assertClean(self, result):
        self.assertEqual(result.error, "", result.error)

    def local_commit(self, files, delete=()):
        """An unpushed 0.3.1 commit, which synced every tracked file."""
        for rel, text in files.items():
            (self.a.presets / rel).write_text(text)
        for rel in delete:
            raw_git(self.a, "rm", "-q", rel)
        raw_git(self.a, "add", "-A", ".")
        raw_git(self.a, "commit", "-q", "-m", "0.3.1 sync")

    def assertSettled(self):
        """A second sync is a clean no-op."""
        log = remote_log(self.remote)
        r = self.a.sync()
        self.assertClean(r)
        self.assertEqual((r.committed, r.pulled, r.conflicts), ([], [], []))
        self.assertEqual(remote_log(self.remote), log)
        self.assertFalse((self.a.syncer.git_dir / "MERGE_HEAD").exists())

    def test_non_preset_conflicts_from_an_old_clone_take_the_remote(self):
        self.local_commit(
            {
                "notes.txt": "local\n",
                "filament/PLA.info": "local ids\n",
                "filament/PLA.json": preset("filament", "PLA", nozzle_temperature=["210"]),
            }
        )
        push_files(
            self.tmp.name,
            self.remote,
            {"notes.txt": "remote\n", "process/Fine.json": preset("process", "Fine", layer_height="0.12")},
            delete=["filament/PLA.info"],
        )
        r = self.a.sync()
        self.assertClean(r)
        self.assertTrue(r.pushed)
        self.assertEqual(r.conflicts, [])
        self.assertEqual(remote_file(self.remote, "notes.txt"), "remote\n")
        self.assertNotIn("filament/PLA.info", remote_files(self.remote))
        self.assertIn('"210"', remote_file(self.remote, "filament/PLA.json"))
        self.assertEqual(self.a.read("process", "Fine")["layer_height"], "0.12")
        self.assertFalse((self.a.presets / "notes.txt").exists())
        self.assertFalse((self.a.presets / "filament" / "PLA.info").exists())
        self.assertSettled()

        push_files(self.tmp.name, self.remote, {"notes.txt": "remote again\n"})
        self.assertClean(self.a.sync())
        self.assertFalse((self.a.presets / "notes.txt").exists())
        self.assertSettled()

    def test_non_preset_changed_on_both_sides_takes_the_remote(self):
        self.local_commit({"notes.txt": "local\n"})
        push_files(self.tmp.name, self.remote, {"notes.txt": "remote\n"})
        r = self.a.sync()
        self.assertClean(r)
        self.assertEqual(remote_file(self.remote, "notes.txt"), "remote\n")
        self.assertFalse((self.a.presets / "notes.txt").exists())
        self.assertSettled()

    def test_non_preset_deleted_locally_and_changed_remotely(self):
        self.local_commit({}, delete=["notes.txt"])
        push_files(self.tmp.name, self.remote, {"notes.txt": "remote\n"})
        r = self.a.sync()
        self.assertClean(r)
        self.assertEqual(remote_file(self.remote, "notes.txt"), "remote\n")
        self.assertFalse((self.a.presets / "notes.txt").exists())
        self.assertSettled()

    def test_preset_and_non_preset_conflict_in_one_merge(self):
        self.local_commit(
            {"notes.txt": "local\n", "filament/PETG.json": preset("filament", "PETG", nozzle_temperature=["235"])}
        )
        push_files(
            self.tmp.name,
            self.remote,
            {"filament/PETG.json": preset("filament", "PETG", nozzle_temperature=["245"])},
            delete=["notes.txt"],
        )
        r = self.a.sync()
        self.assertClean(r)
        self.assertEqual(len(r.conflicts), 1)
        path, copy = r.conflicts[0]
        self.assertEqual(path, "filament/PETG.json")
        self.assertRegex(copy, r"^filament/PETG \(git conflict \d{4}-\d\d-\d\d \d{4}\)\.json$")
        self.assertEqual(self.a.read("filament", "PETG")["nozzle_temperature"], ["235"])
        self.assertIn('"245"', (self.a.presets / copy).read_text())
        self.assertIn(copy, remote_files(self.remote))
        self.assertNotIn("notes.txt", remote_files(self.remote))
        self.assertFalse((self.a.presets / "notes.txt").exists())
        self.assertSettled()

    def open_merge(self):
        """The state a crash, or a 0.3.2 run that stopped at the merge commit, leaves behind."""
        self.local_commit(
            {
                "filament/PLA.info": "local ids\n",
                "filament/PETG.json": preset("filament", "PETG", nozzle_temperature=["235"]),
            }
        )
        push_files(
            self.tmp.name,
            self.remote,
            {"filament/PETG.json": preset("filament", "PETG", nozzle_temperature=["245"])},
            delete=["filament/PLA.info"],
        )
        s = self.a.syncer
        s.ensure_repo()
        s.fetch()
        s.run("merge", check=False)
        self.assertTrue((s.git_dir / "MERGE_HEAD").exists())

    def test_open_merge_is_finished_on_the_next_sync(self):
        self.open_merge()
        self.a.save("process", "Saved meanwhile", layer_height="0.3")
        r = self.a.sync()
        self.assertClean(r)
        self.assertTrue(r.pushed)
        self.assertIn("process/Saved meanwhile.json", remote_files(self.remote))
        self.assertEqual([p for p, _ in r.conflicts], ["filament/PETG.json"])
        self.assertNotIn("filament/PLA.info", remote_files(self.remote))
        self.assertEqual(self.a.read("filament", "PETG")["nozzle_temperature"], ["235"])
        self.assertEqual(len(self.a.files("filament")), 3)
        self.assertSettled()

    def test_open_merge_after_conflict_copies_gets_no_second_copy(self):
        self.open_merge()
        s = self.a.syncer
        theirs = remote_file(self.remote, "filament/PETG.json").encode()
        s.write_copy("filament/PETG.json", theirs)
        s.stage()
        r = self.a.sync()
        self.assertClean(r)
        self.assertEqual(r.conflicts, [])
        self.assertEqual(len(self.a.files("filament")), 3, self.a.files("filament"))
        self.assertSettled()


class MessageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir()
        isolate_home(self.tmp.name)
        fake_orca.install(self.tmp.name)
        self.remote = bare_repo(self.tmp.name)
        self.a = Mac(self.tmp.name, "mac-a", self.remote)
        self.b = Mac(self.tmp.name, "mac-b", self.remote, user_id="other-id")

    def tearDown(self):
        self.tmp.cleanup()

    def last_message_bytes(self):
        out = subprocess.run(
            ["git", "--git-dir", str(self.remote), "log", "-1", "--format=%B", "main"], capture_output=True
        ).stdout
        out.decode("utf-8")
        return len(out)

    def test_many_long_unicode_names_stay_under_the_stdin_cap(self):
        import orca_git_sync as ogs

        for i in range(300):
            self.a.save("process", f"{i:03d} " + "Ünïcødé " * 20, layer_height="0.2")
        r = self.a.sync()
        self.assertEqual(r.error, "", r.error)
        self.assertEqual(len(r.committed), 300)
        self.assertLess(self.last_message_bytes(), ogs.MSG_BYTES + 64)

    def test_many_conflicts_stay_under_the_stdin_cap(self):
        import orca_git_sync as ogs

        names = [f"{i:03d} " + "Ünïcødé " * 16 for i in range(120)]
        for n in names:
            self.a.save("filament", n, nozzle_temperature=["200"])
            self.b.save("filament", n, nozzle_temperature=["210"])
        self.assertEqual(self.a.sync().error, "")
        r = self.b.sync()
        self.assertEqual(r.error, "", r.error)
        self.assertEqual(len(r.conflicts), 120)
        self.assertTrue(r.pushed)
        self.assertLess(self.last_message_bytes(), ogs.MSG_BYTES + 64)

    def test_over_long_name_is_cut_on_a_character_boundary(self):
        import orca_git_sync as ogs

        name = "process/" + "€" * 4000 + ".json"
        msg = ogs.commit_message("presets: 1 change(s) on mac", [f"- {name}"])
        text = msg.decode("utf-8")
        self.assertLessEqual(len(msg), ogs.MSG_BYTES)
        line = text.splitlines()[2]
        self.assertLessEqual(len(line.encode()), ogs.LINE_BYTES)
        self.assertTrue(line.endswith("€"), line[-5:])
        self.assertGreater(len(line.encode()), ogs.LINE_BYTES - 3)


if __name__ == "__main__":
    unittest.main()
