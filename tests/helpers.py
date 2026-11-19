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


def push_files(tmp, remote, files, delete=()):
    """Commit arbitrary files to the remote's main with plain git, as another tool would."""
    work = Path(tmp) / "raw-clone"
    if not work.exists():
        subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True, capture_output=True)
    git = ["git", "-C", str(work), "-c", "user.name=raw", "-c", "user.email=raw@example.com"]
    subprocess.run(git + ["pull", "-q", "--no-rebase", "origin", "main"], capture_output=True)
    for rel, text in files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(text)
    for rel in delete:
        (work / rel).unlink()
    subprocess.run(git + ["add", "-A", "-f", "."], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "raw"], check=True)
    subprocess.run(git + ["push", "-q", "origin", "HEAD:main"], check=True, capture_output=True)


def remote_files(remote):
    out = subprocess.run(
        ["git", "-c", "core.quotePath=false", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main"],
        capture_output=True,
        text=True,
    )
    return out.stdout.splitlines()


def remote_file(remote, path):
    return subprocess.run(
        ["git", "--git-dir", str(remote), "show", f"main:{path}"], capture_output=True, text=True
    ).stdout
