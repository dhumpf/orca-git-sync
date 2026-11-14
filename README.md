# Orca Git Sync

An OrcaSlicer plugin that syncs your user presets (printers, filaments, processes) through a git repo. Two computers, or two people, share one set of presets with full history.

Status: for OrcaSlicer 2.5 and later (the plugin API is still experimental). Runs on macOS, Windows and Linux.

## What it does

| When | What |
|---|---|
| Orca starts | commit local changes, pull from the repo, push |
| You save a preset | commit and push, 2 s after the last save |
| Orca quits | same as start |
| Run the Git Sync script action, then Sync now | same as start |
| Both sides changed one preset | your version stays, the other one is saved next to it as `<name> (git conflict <date time>)`. A notification tells you. Pick one and delete the other |
| One side deleted, the other edited | the edit wins, nothing is lost |

It never force-pushes. A rejected push fetches, merges and tries once more.

Only `*.json` presets in `filament/`, `process/` and `machine/` are synced. Orca's `.info` files hold per-account cloud ids and are left out on purpose, as are `.bak` copies and everything else in the folder.

The git repo itself lives in the plugin's storage folder, not in Orca's preset folder, so nothing is added there.

## Install

1. Download [`orca_git_sync.py`](https://github.com/dhumpf/orca-git-sync/releases/latest/download/orca_git_sync.py) from the latest release (or `make build` puts it in `dist/`). For a newer version later, download and install it again.
2. In OrcaSlicer: main menu > Plugins > Browse plugins (split button) > Install local plugin, pick the file.
3. Orca asks once to allow the git commands listed below and to start threads. Allow it.
4. Needs git. macOS: `/usr/bin/git` from the Xcode command line tools works, Homebrew git is picked up first. Windows: Git for Windows. Linux: the distro package.

## Configure

| Where | What |
|---|---|
| Plugins > Git Sync > Config | repo URL (https), branch (default `main`), username, commit author, preset folder (empty = auto) |
| the Git Sync script action | status, Sync now, and the token field |

The token is handed to `git credential approve` and stored by the OS credential store: the macOS keychain (`osxkeychain`), the Windows Credential Manager (`manager`) or libsecret on Linux. It is never written to the plugin config or any file. Use a token that can only read and write the presets repo.

Preset folder auto: Orca keeps presets in `user/<account id>` when you are signed in to a cloud account and in `user/default` when not. The plugin asks Orca which one it loaded, and falls back to the single account folder. With several account folders, set it by hand.

## Git calls

Each call has a fixed command line, so Orca can grant each one once. Repo URL, branch and author go in through `GIT_CONFIG_*` environment variables, text through stdin.

Git gets a short allowlist from your environment, nothing else: `PATH`, `HOME`, `LANG`, `TMPDIR`, plus what the credential helpers need on Windows (`SYSTEMROOT`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`) and Linux (`DBUS_SESSION_BUS_ADDRESS`, `XDG_RUNTIME_DIR`). API keys and tokens in your shell never reach git. That read is why Orca's plugin review lists "Reads environment variables".

| Call | Used for |
|---|---|
| `init -q` | first run |
| `add -A`, `status --porcelain -z`, `commit -q -F -` | local commits |
| `rev-parse -q --verify HEAD` | is there history yet |
| `fetch -q origin`, `for-each-ref ... refs/ogs/remote` | pull |
| `merge-tree --write-tree ...`, `merge ... -X ours refs/ogs/remote`, `cat-file --batch` | merge and keep both versions |
| `push -q origin` | push, never forced |
| `credential approve` | save the token to the credential store |

Your global git hooks and commit signing are switched off for these calls.

## Limits

| Limit | Why |
|---|---|
| A pull at start can land after Orca already loaded presets. Restart Orca to see them | Orca loads plugins in a background thread, racing the preset load. The plugin API has no preset reload for the user folder (only for `_local` bundles) |
| Changes pulled while Orca runs show after a restart | same |
| A preset deleted or renamed in Orca syncs on the next save, Sync now or quit | Orca only fires an event on save |
| If you keep editing a preset after a pull without restarting, your next save overwrites the pulled version | Orca still has the old copy in memory. Git history keeps both |
| Printer presets can hold print host API keys. They go into the repo | keep the repo private |
| Orca Cloud preset sync and this plugin can both write the same folder | use one or the other per account |
| Windows is untested | macOS and Linux are tested, Windows uses Git for Windows and its Credential Manager |
| Linux without libsecret keeps the token in memory only | install `git-credential-libsecret` to keep it across logins |

## Develop

`make` lists the targets. `make setup && make test` runs the unit tests and a two-computer simulation against a bare repo, without Orca. `make lint`, `make build`.

## Credit and license

Built on the OrcaSlicer plugin system. OrcaSlicer is by SoftFever and the OrcaSlicer contributors (github.com/OrcaSlicer/OrcaSlicer), licensed AGPL-3.0.

This plugin is AGPL-3.0-or-later as well, see `LICENSE`. It runs inside OrcaSlicer and uses its `orca` module, so it follows OrcaSlicer's license.
