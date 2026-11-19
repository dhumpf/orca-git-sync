# /// script
# requires-python = ">=3.12"
#
# [tool.orcaslicer.plugin]
# name = "Git Sync"
# description = "Sync user presets (printer, filament, process) through a git repo."
# author = "Daniel Humpf"
# version = "0.3.2"
# ///
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sync OrcaSlicer user presets through git. Pull on start, commit and push on save.

The header above is the PEP 723 block the plugin host parses (allow-long-comment).
"""

import datetime
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import orca

VERSION = "0.3.2"
PRESET_DIRS = ("filament", "process", "machine")
TRACK_REF = "refs/ogs/remote"
GIT_TIMEOUT = 60
DEBOUNCE_S = 2.0

# Only preset json is tracked. A .info holds per-account cloud ids, and a
# lone .info reads to Orca as a cloud delete request.
EXCLUDE = (
    "\n".join(
        ["/*"]
        + [f"!/{d}/" for d in PRESET_DIRS]
        + [f"/{d}/*" for d in PRESET_DIRS]
        + [f"!/{d}/*.json" for d in PRESET_DIRS]
    )
    + "\n"
)
# A pull checks out only these paths, the rest of the remote tree stays in git.
# The negation drops folders named like a preset and everything inside them.
SPARSE = "".join(f"/{d}/*.json\n!/{d}/*/\n" for d in PRESET_DIRS)
# Suffix for an edited leftover file moved out of git's way during a merge.
ASIDE = ".ogs-aside"

# Fixed argv per call, so the host grants each one once. URL, branch and
# identity travel in env (GIT_CONFIG_*), text goes on stdin.
GIT_ARGS = {
    "init": ["init", "-q"],
    "add": ["add", "-A", "--pathspec-from-file=-", "--pathspec-file-nul"],
    "status": ["status", "--porcelain", "-z", "--untracked-files=all"],
    "commit": ["commit", "-q", "-F", "-"],
    "head": ["rev-parse", "-q", "--verify", "HEAD"],
    "fetch": ["fetch", "-q", "origin"],
    "remote_ref": ["for-each-ref", "--format=%(objectname)", TRACK_REF],
    "merge_tree": ["merge-tree", "--write-tree", "-z", "--allow-unrelated-histories", "HEAD", TRACK_REF],
    "merge": ["merge", "-q", "--no-edit", "--allow-unrelated-histories", "-X", "ours", TRACK_REF],
    "index": ["update-index", "-z", "--index-info"],
    "cat": ["cat-file", "--batch"],
    "push": ["push", "-q", "origin"],
    "credential": ["credential", "approve"],
}

# Commit messages stay well under the 16 KB stdin cap of run().
MSG_BYTES = 12288
LINE_BYTES = 1024

# Redirects are not followed, so a token never goes to a host the user did not set.
REDIRECT_HINT_ON = "returned error: 30"
REDIRECT_HINT = "The repo URL redirects. Set the final URL (often ending in .git) in the plugin config"

DEFAULT_CONFIG = {
    "repo_url": "",
    "branch": "main",
    "username": "",
    "author_name": "",
    "author_email": "",
    "preset_folder": "",
}


GIT_CANDIDATES = {
    "darwin": ("/opt/homebrew/bin/git", "/usr/local/bin/git", "/usr/bin/git"),
    "win32": (r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files (x86)\Git\cmd\git.exe"),
}
LIBSECRET = (
    "/usr/lib/git-core/git-credential-libsecret",
    "/usr/libexec/git-core/git-credential-libsecret",
    "/usr/lib/git/git-credential-libsecret",
)
# All git and the credential helpers need. The rest of the user's environment, API keys
# included, never reaches git.
GIT_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "LANG",
    "TMPDIR",
    "SYSTEMROOT",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "DBUS_SESSION_BUS_ADDRESS",
    "XDG_RUNTIME_DIR",
)


def find_git(system=None, exists=os.path.exists, which=shutil.which):
    system = system or sys.platform
    for candidate in GIT_CANDIDATES.get(system, ("/usr/bin/git", "/usr/local/bin/git")):
        if exists(candidate):
            return candidate
    return which("git") or "git"


def credential_helper(system=None, exists=os.path.exists):
    """The OS credential store git keeps the token in."""
    system = system or sys.platform
    if system == "darwin":
        return "osxkeychain"
    if system == "win32":
        return "manager"
    if any(exists(p) for p in LIBSECRET):
        return "libsecret"
    # No keyring helper: memory only, the token is asked again after a logout.
    return "cache --timeout=2592000"


def is_preset(path):
    """Only `*.json` directly inside a preset dir is ever staged or written."""
    parts = path.split("/")
    return len(parts) == 2 and parts[0] in PRESET_DIRS and parts[1].endswith(".json")


def clip(text, limit):
    """At most `limit` UTF-8 bytes, cut on a character boundary."""
    return text.encode()[:limit].decode(errors="ignore")


def commit_message(title, lines):
    """Title plus as many lines as fit in MSG_BYTES, then a count of the rest."""
    out = clip(title, LINE_BYTES).encode() + b"\n\n"
    for i, line in enumerate(lines):
        item = (clip(line, LINE_BYTES) + "\n").encode()
        if len(out) + len(item) > MSG_BYTES - 64:
            return out + f"- and {len(lines) - i} more\n".encode()
        out += item
    return out


def git_argv(git, name):
    return [git] + GIT_ARGS[name]


def declared_commands(git):
    """The exact strings the host audit hook sees for each git call."""
    return [str(git_argv(git, name)) for name in GIT_ARGS]


class GitError(RuntimeError):
    pass


@dataclass
class SyncResult:
    committed: list = field(default_factory=list)
    pulled: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    pushed: bool = False
    offline: bool = False
    error: str = ""
    skipped: list = field(default_factory=list)

    def summary(self):
        if self.error:
            return f"Git Sync failed: {self.error}"
        parts = []
        if self.committed:
            parts.append(f"committed {len(self.committed)}")
        if self.pulled:
            parts.append(f"pulled {len(self.pulled)}")
        if self.conflicts:
            parts.append(f"{len(self.conflicts)} conflict(s), both versions kept")
        if self.pushed:
            parts.append("pushed")
        if self.skipped:
            parts.append(f"skipped {len(self.skipped)} link(s) or folder(s) named like a preset")
        return "Git Sync: " + (", ".join(parts) if parts else "up to date")


class Syncer:
    """Git work on one preset folder. Knows nothing about Orca."""

    # Shared by every instance: a save, Sync now and quit can overlap.
    lock = threading.Lock()
    run_lock = threading.Lock()

    def __init__(self, work_tree, git_dir, settings, git=None, host=None, hooks_dir=None):
        self.work_tree = Path(work_tree)
        self.git_dir = Path(git_dir)
        self.settings = {**DEFAULT_CONFIG, **(settings or {})}
        self.git = git or find_git()
        self.host = host or platform.node().split(".")[0] or "orca"
        self.hooks_dir = Path(hooks_dir) if hooks_dir else self.git_dir.parent / "no-hooks"
        self.skipped = []

    def env(self):
        branch = self.settings["branch"] or "main"
        # An empty credential.helper clears helpers inherited from the user's git config.
        config = [
            ("remote.origin.url", self.settings["repo_url"]),
            ("remote.origin.fetch", f"+refs/heads/{branch}:{TRACK_REF}"),
            ("remote.origin.push", f"HEAD:refs/heads/{branch}"),
            ("credential.helper", ""),
            ("credential.helper", credential_helper()),
            ("http.followRedirects", "false"),
            ("core.hooksPath", str(self.hooks_dir)),
            ("core.sparseCheckout", "true"),
            ("core.sparseCheckoutCone", "false"),
            ("sparse.expectFilesOutsideOfPatterns", "true"),
            ("core.symlinks", "false"),
            ("commit.gpgSign", "false"),
            ("core.quotePath", "false"),
            ("merge.renames", "false"),
            ("status.renames", "false"),
        ]
        env = {k: os.environ[k] for k in GIT_ENV_ALLOWLIST if k in os.environ}
        name = self.settings["author_name"] or f"OrcaSlicer on {self.host}"
        email = self.settings["author_email"] or "orca-git-sync@localhost"
        env.update(
            GIT_DIR=str(self.git_dir),
            GIT_WORK_TREE=str(self.work_tree),
            GIT_TERMINAL_PROMPT="0",
            GIT_AUTHOR_NAME=name,
            GIT_AUTHOR_EMAIL=email,
            GIT_COMMITTER_NAME=name,
            GIT_COMMITTER_EMAIL=email,
            GIT_CONFIG_COUNT=str(len(config)),
        )
        for i, (key, value) in enumerate(config):
            env[f"GIT_CONFIG_KEY_{i}"] = key
            env[f"GIT_CONFIG_VALUE_{i}"] = value
        return env

    def run(self, name, stdin=None, check=True):
        # The host denies open() on a bare fd, which subprocess pipes use. Output goes to
        # files in plugin storage, stdin through a raw pipe so a token never hits disk.
        argv = git_argv(self.git, name)
        data = stdin or b""
        if len(data) > 16384:
            raise GitError(f"git {name}: stdin too large")
        with self.run_lock:
            io_dir = self.git_dir.parent / "io"
            io_dir.mkdir(parents=True, exist_ok=True)
            out_path, err_path = io_dir / "stdout", io_dir / "stderr"
            read_fd, write_fd = os.pipe()
            try:
                os.write(write_fd, data)
                os.close(write_fd)
                write_fd = None
                with open(out_path, "wb") as out, open(err_path, "wb") as err:
                    code = subprocess.run(
                        argv,
                        stdin=read_fd,
                        stdout=out,
                        stderr=err,
                        cwd=str(self.work_tree),
                        env=self.env(),
                        timeout=GIT_TIMEOUT,
                    ).returncode
            finally:
                os.close(read_fd)
                if write_fd is not None:
                    os.close(write_fd)
            proc = subprocess.CompletedProcess(argv, code, out_path.read_bytes(), err_path.read_bytes())
        if check and proc.returncode != 0:
            raise GitError(f"git {name}: {proc.stderr.decode(errors='replace').strip()}")
        return proc

    def ensure_repo(self):
        if not self.work_tree.is_dir():
            raise GitError(f"preset folder {self.work_tree} does not exist")
        if not (self.git_dir / "HEAD").exists():
            self.git_dir.mkdir(parents=True, exist_ok=True)
            self.run("init")
        self.hooks_dir.mkdir(parents=True, exist_ok=True)
        exclude = self.git_dir / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        if not exclude.exists() or exclude.read_text() != EXCLUDE:
            exclude.write_text(EXCLUDE)
        sparse = self.git_dir / "info" / "sparse-checkout"
        if not sparse.exists() or sparse.read_text() != SPARSE:
            sparse.write_text(SPARSE)

    def has_head(self):
        return self.run("head", check=False).returncode == 0

    def status(self):
        return [e for e in self.run("status").stdout.decode().split("\0") if len(e) > 3]

    def changes(self):
        """Preset paths with changes. Other files, links and folders are never staged."""
        found, self.skipped = [], []
        for path in (e[3:] for e in self.status() if is_preset(e[3:])):
            target = self.work_tree / path
            if target.is_symlink() or (target.exists() and not target.is_file()):
                self.skipped.append(path)
            else:
                found.append(path)
        return found

    def run_batched(self, name, items):
        batch = b""
        for item in items:
            if len(batch) + len(item) > 16384:
                self.run(name, stdin=batch)
                batch = b""
            batch += item
        if batch:
            self.run(name, stdin=batch)

    def stage(self):
        self.run_batched("add", [f":(literal){path}\0".encode() for path in self.changes()])

    def staged(self):
        """True when the index differs from HEAD on any path."""
        return any(e[0] not in " ?" for e in self.status())

    def commit_local(self):
        self.stage()
        changed = self.changes()
        if changed:
            title = f"presets: {len(changed)} change(s) on {self.host}"
            self.run("commit", stdin=commit_message(title, [f"- {p}" for p in sorted(changed)]))
        return changed

    def fetch(self):
        """True when the remote branch exists, False when it does not yet."""
        proc = self.run("fetch", check=False)
        if proc.returncode != 0:
            err = proc.stderr.decode(errors="replace")
            if "couldn't find remote ref" in err:
                return False
            if REDIRECT_HINT_ON in err:
                raise GitError(f"git fetch: {err.strip()}. {REDIRECT_HINT}")
            raise GitError(f"git fetch: {err.strip()}")
        return bool(self.run("remote_ref").stdout.strip())

    def conflicts(self):
        """(merged tree, {path: {stage: (mode, oid)}}) for a merge with the remote."""
        proc = self.run("merge_tree", check=False)
        if proc.returncode not in (0, 1):
            raise GitError(f"git merge-tree: {proc.stderr.decode(errors='replace').strip()}")
        entries = proc.stdout.decode().split("\0")
        found = {}
        if proc.returncode == 0:
            return entries[0], found
        for entry in entries[1:]:
            if not entry:
                break
            meta, _, path = entry.partition("\t")
            mode, oid, stage = meta.split(" ")
            found.setdefault(path, {})[int(stage)] = (mode, oid)
        return entries[0], found

    def blobs(self, names):
        """{name: content} for object names cat-file finds. Missing ones are left out."""
        names = [n for n in names if "\n" not in n]
        result = {}
        for i in range(0, len(names), 300):
            chunk = names[i : i + 300]
            out = self.run("cat", stdin=("\n".join(chunk) + "\n").encode()).stdout
            pos = 0
            for name in chunk:
                nl = out.index(b"\n", pos)
                head = out[pos:nl].split(b" ")
                # A found object reads "<oid> <type> <size>", a missing one "<name> missing".
                if not head[-1].isdigit():
                    pos = nl + 1
                    continue
                size = int(head[-1])
                result[name] = out[nl + 1 : nl + 1 + size]
                pos = nl + 1 + size + 1
        return result

    def head_tree(self):
        out = self.run("cat", stdin=b"HEAD^{tree}\n").stdout
        return out.split(b" ")[0].decode()

    def set_aside(self):
        """Move edited files git still tracks but no longer checks out, so a merge cannot stop on them."""
        for e in self.status():
            if e[0] == " " and e[1] in "MT" and not is_preset(e[3:]):
                src = self.work_tree / e[3:]
                dst = src.with_name(src.name + ASIDE)
                if os.path.lexists(src) and not os.path.lexists(dst):
                    os.rename(src, dst)

    def put_back(self):
        """Every set-aside file goes back where it was, unless something is there now."""
        for root, _dirs, files in os.walk(self.work_tree):
            for name in files:
                if name.endswith(ASIDE):
                    src = Path(root) / name[: -len(ASIDE)]
                    if not os.path.lexists(src):
                        os.rename(Path(root) / name, src)

    def write(self, rel, data):
        target = self.work_tree / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".ogs-tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)

    @staticmethod
    def has_copy(rel, fresh):
        """A copy of rel among the paths this merge added, not one an older merge committed."""
        p = Path(rel)
        start, end = f"{p.stem} (git conflict ", f"){p.suffix}"
        return any(Path(c).parent == p.parent and Path(c).name.startswith(start) and c.endswith(end) for c in fresh)

    def write_copy(self, rel, data):
        p = Path(rel)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H%M")
        new_rel = str(p.with_name(f"{p.stem} (git conflict {stamp}){p.suffix}"))
        old, new = p.stem, Path(new_rel).stem
        try:
            doc = json.loads(data)
            for key in ("name", "print_settings_id", "printer_settings_id"):
                if doc.get(key) == old:
                    doc[key] = new
            ids = doc.get("filament_settings_id")
            if isinstance(ids, list) and ids and ids[0] == old:
                ids[0] = new
            data = (json.dumps(doc, indent=4, ensure_ascii=False) + "\n").encode()
        except (ValueError, AttributeError):
            pass
        self.write(new_rel, data)
        return new_rel

    def snapshot(self):
        out = {}
        for d in PRESET_DIRS:
            for f in (self.work_tree / d).glob("*.json"):
                if f.is_file() and not f.is_symlink():
                    out[f"{d}/{f.name}"] = f.read_bytes()
        return out

    def take_remote(self, found):
        """Conflicts outside the preset files: the remote's version goes in the index, nothing to the folder."""
        items, lines = [], []
        for path, stages in sorted(found.items()):
            zero = "0" * len(next(iter(stages.values()))[1])
            items.append(f"0 {zero}\t{path}\0".encode())
            if 3 in stages:
                mode, oid = stages[3]
                items.append(f"{mode} {oid}\t{path}\0".encode())
            lines.append(f"- {path}: remote version, not a preset")
        self.run_batched("index", items)
        return lines

    def merge_remote(self, resume=False):
        """Merge the fetched branch, or finish a merge left open. Returns (changed paths, [(path, copy)])."""
        before = self.snapshot()
        resolved = []
        if not self.has_head():
            self.run("merge")
        else:
            tree, found = self.conflicts()
            try:
                if not resume and tree != self.head_tree():
                    self.set_aside()
                resolved = self.finish_merge(found, resume)
            finally:
                self.put_back()
        for d in PRESET_DIRS:
            (self.work_tree / d).mkdir(exist_ok=True)
        after = self.snapshot()
        changed = sorted(p for p in after.keys() | before.keys() if after.get(p) != before.get(p))
        return changed, resolved

    def finish_merge(self, found, resume):
        # Only regular preset files are written. Links, submodules and other paths go to take_remote.
        presets = {p: s for p, s in found.items() if is_preset(p) and all(m.startswith("100") for m, _ in s.values())}
        outside = {p: os.path.lexists(self.work_tree / p) for p in found if p not in presets}
        if not resume:
            # -X ours never leaves conflict markers in a preset file. Orca
            # deletes a preset file it cannot parse.
            proc = self.run("merge", check=False)
            if proc.returncode != 0 and not (self.git_dir / "MERGE_HEAD").exists():
                raise GitError(f"git merge: {proc.stderr.decode(errors='replace').strip()}")
        resolved, lines = [], []
        if found:
            fresh = {e[3:] for e in self.status() if e[0] in "?A"} if resume else set()
            names = [oid for stages in presets.values() for _mode, oid in stages.values()]
            data = self.blobs(sorted(set(names) | ({f":{p}" for p in presets} if resume else set())))
            for path, stages in sorted(presets.items()):
                ours, theirs = (data.get(stages.get(n, (None, None))[1]) for n in (2, 3))
                target = self.work_tree / path
                disk = target.read_bytes() if target.is_file() and not target.is_symlink() else None
                # A file saved while the merge was open differs from all the merge left behind.
                edited = resume and disk is not None and disk not in (data.get(f":{path}"), ours, theirs)
                keep = disk if edited else (ours if ours is not None else theirs)
                if not edited and keep is not None:
                    self.write(path, keep)
                if theirs is not None and (ours is not None or edited):
                    if keep != theirs and not self.has_copy(path, fresh):
                        resolved.append((path, self.write_copy(path, theirs)))
                else:
                    resolved.append((path, None))
            lines = [f"- {p}: remote version in {c}" if c else f"- {p}: kept, delete undone" for p, c in resolved]
            lines += self.take_remote({p: s for p, s in found.items() if p not in presets})
            for path, existed in outside.items():
                if not existed and (self.work_tree / path).is_file():
                    (self.work_tree / path).unlink()
        if found or resume:
            self.stage()
            if (self.git_dir / "MERGE_HEAD").exists() or self.staged():
                self.run("commit", stdin=commit_message("presets: merge, kept both versions", lines))
        return resolved

    def sync(self):
        with self.lock:
            result = SyncResult()
            try:
                self.sync_into(result)
            except (GitError, OSError, subprocess.TimeoutExpired) as e:
                result.error = str(e)
            result.skipped = list(self.skipped)
            return result

    def sync_into(self, result):
        self.ensure_repo()
        self.put_back()
        if (self.git_dir / "MERGE_HEAD").exists():
            result.pulled, result.conflicts = self.merge_remote(resume=True)
        result.committed = self.commit_local()
        if not self.settings["repo_url"]:
            raise GitError("no repo URL set, open the plugin config")
        for _attempt in range(2):
            try:
                exists = self.fetch()
            except GitError as e:
                result.offline = True
                result.error = f"{e}. Local changes stay committed and push next time"
                return
            if exists:
                changed, resolved = self.merge_remote()
                result.pulled += [p for p in changed if p not in result.pulled]
                result.conflicts += resolved
            if not self.has_head():
                return
            proc = self.run("push", check=False)
            if proc.returncode == 0:
                result.pushed = True
                return
            err = proc.stderr.decode(errors="replace").strip()
            if "rejected" not in err and "fetch first" not in err:
                result.offline = True
                result.error = f"git push: {err}"
                return
        result.error = "push rejected twice, try Sync now"

    def save_token(self, token):
        url = urllib.parse.urlsplit(self.settings["repo_url"])
        if url.scheme != "https" or not url.hostname:
            raise GitError("a token needs an https repo URL")
        token = token.strip()
        if not token or any(c in token for c in "\r\n"):
            raise GitError("token is empty or has a line break")
        host = url.hostname + (f":{url.port}" if url.port else "")
        user = self.settings["username"] or "git"
        payload = f"protocol=https\nhost={host}\nusername={user}\npassword={token}\n\n"
        self.run("credential", stdin=payload.encode())


# Orca host glue


def data_dir():
    parts = Path(__file__).resolve().parts
    if "orca_plugins" not in parts:
        raise RuntimeError("install this file through the OrcaSlicer Plugins dialog")
    return Path(*parts[: parts.index("orca_plugins")])


def preset_folder(root, configured=""):
    """The user preset folder: configured, else the one Orca loads from."""
    user = Path(root) / "user"
    if configured:
        return user / configured
    try:
        bundle = orca.host.preset_bundle()
        for coll in (bundle.filaments, bundle.prints, bundle.printers):
            for i in range(coll.size()):
                preset = coll.preset(i)
                parts = Path(preset.file or "").parts
                if preset.is_user() and "user" in parts:
                    return user / parts[parts.index("user") + 1]
    except Exception:
        pass
    # Signed in to a cloud account, Orca loads user/<account id>, not user/default.
    accounts = [p for p in sorted(user.glob("*")) if p.is_dir() and p.name != "default" and not p.name.startswith(".")]
    if len(accounts) > 1:
        raise GitError("several preset folders under user/, set Preset folder in the plugin config")
    return accounts[0] if accounts else user / "default"


def notify(text, warning=False):
    ui = orca.host.ui
    level = ui.NotificationLevel.WarningNotificationLevel if warning else ui.NotificationLevel.RegularNotificationLevel
    try:
        ui.push_notification(level, text)
        return True
    except Exception:
        return False


CONFIG_UI = r"""<!doctype html><html><head><meta charset="utf-8"><style>
:root { color-scheme: light dark; font: 13px system-ui, sans-serif; }
body { margin: 0; padding: 14px; }
label { display: block; margin: 0 0 10px; }
label span { display: block; margin-bottom: 3px; font-weight: 600; }
input { width: 100%; box-sizing: border-box; padding: 5px; font: inherit; }
small { color: GrayText; }
</style></head><body>
<form id="f">
<label><span>Repo URL</span><input name="repo_url" placeholder="https://git.example.com/team/presets.git"></label>
<label><span>Branch</span><input name="branch" placeholder="main"></label>
<label><span>Username</span><input name="username"><small>The token goes to the system credential store from the Git Sync window, never into this config.</small></label>
<label><span>Commit author name</span><input name="author_name" placeholder="OrcaSlicer on this computer"></label>
<label><span>Commit author email</span><input name="author_email"></label>
<label><span>Preset folder</span><input name="preset_folder" placeholder="auto"><small>Folder name under user/. Empty picks the one Orca loads.</small></label>
<button type="submit">Save</button>
</form><script>
const f = document.getElementById('f');
orca.onConfig(cfg => { for (const el of f.elements) if (el.name) el.value = cfg[el.name] || ''; });
f.addEventListener('submit', e => {
  e.preventDefault();
  const cfg = {};
  for (const el of f.elements) if (el.name) cfg[el.name] = el.value.trim();
  orca.saveConfig(cfg);
});
</script></body></html>"""

WINDOW_UI = r"""<!doctype html><html><head><meta charset="utf-8"><style>
:root { color-scheme: light dark; font: 13px system-ui, sans-serif; }
body { margin: 0; padding: 14px; }
pre { white-space: pre-wrap; background: rgba(127,127,127,.12); padding: 8px; border-radius: 6px; min-height: 3em; }
input { width: 100%; box-sizing: border-box; padding: 5px; font: inherit; margin: 6px 0; }
</style></head><body>
<pre id="status">Loading</pre>
<button id="sync">Sync now</button>
<h4>Access token</h4>
<input id="token" type="password" autocomplete="off" placeholder="Forgejo or GitHub token">
<button id="save">Save token</button>
<script>
const $ = id => document.getElementById(id);
$('sync').onclick = () => { $('status').textContent = 'Syncing'; orca.postMessage({command: 'sync'}); };
$('save').onclick = () => { const t = $('token').value; $('token').value = ''; orca.postMessage({command: 'token', token: t}); };
orca.onMessage(m => { if (m && m.status) $('status').textContent = m.status; });
orca.postMessage({command: 'status'});
</script></body></html>"""


class GitSync(orca.script.ScriptPluginCapabilityBase):
    window = None
    timer = None
    pending = None
    storage_dir = None
    last = "Not synced yet."

    def get_name(self):
        return "Git Sync"

    def has_config_ui(self):
        return True

    def get_config_ui(self):
        return CONFIG_UI

    def get_default_config(self):
        return dict(DEFAULT_CONFIG)

    def settings(self):
        try:
            return {**DEFAULT_CONFIG, **json.loads(self.get_config())}
        except (ValueError, TypeError):
            return dict(DEFAULT_CONFIG)

    def remember_storage(self):
        # Orca 2.5.0 answers storage() only inside a plugin callback, so every callback caches it for the workers.
        try:
            self.storage_dir = Path(orca.host.plugin.storage())
        except Exception:
            pass

    def make_syncer(self):
        cfg = self.settings()
        storage = self.storage_dir or Path(orca.host.plugin.storage())
        work = preset_folder(data_dir(), cfg["preset_folder"])
        return Syncer(work, storage / f"git-{work.name}", cfg, hooks_dir=storage / "no-hooks")

    def run_sync(self, startup=False):
        try:
            result = self.make_syncer().sync()
        except Exception as e:
            result = SyncResult(error=str(e))
        self.last = result.summary()
        text = self.message(result, startup)
        self.log(text)
        if result.error or result.conflicts or result.pulled:
            warn = bool(result.error or result.conflicts)
            # Orca 2.5.0 crashes on a notification before the plater exists, so startup only queues it.
            if startup:
                self.pending = (text, warn)
            else:
                threading.Thread(target=notify, args=(text, warn), daemon=True).start()
        self.post_status(text)
        return result

    def log(self, text):
        try:
            path = (self.storage_dir or Path(orca.host.plugin.storage())) / "sync.log"
            stamp = datetime.datetime.now().isoformat(timespec="seconds")
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{stamp} {text}\n")
        except Exception:
            pass

    @staticmethod
    def message(result, startup):
        text = result.summary()
        copies = [Path(c).stem for _, c in result.conflicts if c]
        if copies:
            text += ". Kept the other version as: " + ", ".join(copies) + ". Compare and delete the one you do not want"
        if startup and result.pulled:
            text += ". Restart Orca if a pulled preset does not show"
        return text

    def flush_pending(self):
        if self.pending:
            text, warn = self.pending
            self.pending = None
            threading.Thread(target=notify, args=(text, warn), daemon=True).start()

    def sync_later(self):
        if self.timer:
            self.timer.cancel()
        self.timer = threading.Timer(DEBOUNCE_S, self.run_sync)
        self.timer.daemon = True
        self.timer.start()

    def on_load(self):
        self.remember_storage()
        threading.Thread(target=self.run_sync, kwargs={"startup": True}, daemon=True).start()

    def on_unload(self):
        self.remember_storage()
        if self.timer:
            self.timer.cancel()
        self.run_sync()

    def on_lifecycle_event(self, event, ctx):
        # Lifecycle events come from the plater, so the UI exists now.
        self.remember_storage()
        self.flush_pending()
        if event == orca.LifecycleEvent.PresetSaved:
            self.sync_later()

    def execute(self):
        self.remember_storage()
        self.flush_pending()
        if self.window is not None and self.window.is_open():
            self.post_status()
        else:
            self.window = orca.host.ui.create_window(
                title="Git Sync",
                html=WINDOW_UI,
                width=460,
                height=320,
                on_message=self.on_message,
                on_close=self.on_close,
            )
        return orca.ExecutionResult.success("Git Sync opened.")

    def on_close(self):
        self.window = None

    def post_status(self, text=None):
        if self.window is not None and self.window.is_open():
            self.window.post({"status": text or self.last})

    def on_message(self, message):
        self.remember_storage()
        command = (message or {}).get("command")
        if command == "sync":
            threading.Thread(target=self.run_sync, daemon=True).start()
        elif command == "token":
            token = str(message.get("token") or "")
            threading.Thread(target=self.store_token, args=(token,), daemon=True).start()
        else:
            self.post_status()

    def store_token(self, token):
        try:
            self.make_syncer().save_token(token)
            self.post_status("Token saved to the system credential store.")
        except Exception as e:
            self.post_status(f"Token not saved: {e}")


@orca.plugin
class GitSyncPackage(orca.base):
    def register_capabilities(self):
        orca.request_permissions(process=declared_commands(find_git()), threading=True)
        orca.register_capability(GitSync)
