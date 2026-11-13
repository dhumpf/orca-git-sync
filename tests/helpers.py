import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))


def isolate_home(tmp):
    """Keep the developer's git config, hooks and keychain out of the tests."""
    home = Path(tmp) / "home"
    home.mkdir(exist_ok=True)
    os.environ["HOME"] = str(home)
    os.environ["XDG_CONFIG_HOME"] = str(home / ".config")


def bare_repo(tmp, name="remote.git"):
    path = Path(tmp) / name
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(path)], check=True)
    return path


def preset(kind, name, **values):
    key = {"filament": "filament_settings_id", "process": "print_settings_id", "machine": "printer_settings_id"}[kind]
    doc = {"name": name, "from": "User", "inherits": "", "version": "2.5.0.0"}
    doc[key] = [name] if kind == "filament" else name
    doc.update(values)
    return json.dumps(doc, indent=4) + "\n"


class Mac:
    """One simulated Mac: an Orca user preset folder plus plugin storage."""

    def __init__(self, tmp, label, remote, user_id="user-1234"):
        import orca_git_sync as ogs

        self.root = Path(tmp) / label
        self.presets = self.root / "user" / user_id
        for d in ("filament", "process", "machine"):
            (self.presets / d).mkdir(parents=True, exist_ok=True)
        storage = self.root / "orca_plugins" / "plugin_data" / "orca_git_sync"
        self.syncer = ogs.Syncer(
            self.presets,
            storage / "git",
            {"repo_url": str(remote)},
            git="git",
            host=label,
        )

    def save(self, kind, name, **values):
        (self.presets / kind / f"{name}.json").write_text(preset(kind, name, **values))

    def read(self, kind, name):
        return json.loads((self.presets / kind / f"{name}.json").read_text())

    def files(self, kind):
        return sorted(p.name for p in (self.presets / kind).iterdir())

    def sync(self):
        return self.syncer.sync()


def remote_log(remote):
    out = subprocess.run(
        ["git", "--git-dir", str(remote), "log", "--format=%H %s", "main"],
        capture_output=True,
        text=True,
    )
    return out.stdout.splitlines()


def tmpdir():
    return tempfile.TemporaryDirectory(prefix="ogs-test-")
