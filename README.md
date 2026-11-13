# Orca Git Sync

An OrcaSlicer plugin that syncs your user presets (printers, filaments, processes) through a git repo. Two Macs, or two people, share one set of presets with full history.

Status: first working version, for OrcaSlicer 2.5 (plugin API is still experimental).

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

1. Get `orca_git_sync.py` (from a release zip, or `make build` puts it in `dist/`).
2. In OrcaSlicer: main menu > Plugins > Browse plugins (split button) > Install local plugin, pick the file.
3. Orca asks once to allow the git commands listed below and to start threads. Allow it.
4. Needs git on the Mac. `/usr/bin/git` from the Xcode command line tools works, Homebrew git is picked up first if present.

## Configure

| Where | What |
|---|---|
| Plugins > Git Sync > Config | repo URL (https), branch (default `main`), username, commit author, preset folder (empty = auto) |
| the Git Sync script action | status, Sync now, and the token field |

The token is handed to `git credential approve` and stored by git's `osxkeychain` helper in your login keychain. It is never written to the plugin config or any file. Use a token that can only read and write the presets repo.

Preset folder auto: Orca keeps presets in `user/<account id>` when you are signed in to a cloud account and in `user/default` when not. The plugin asks Orca which one it loaded, and falls back to the single account folder. With several account folders, set it by hand.

## Git calls

Each call has a fixed command line, so Orca can grant each one once. Repo URL, branch and author go in through `GIT_CONFIG_*` environment variables, text through stdin.

| Call | Used for |
|---|---|
| `init -q` | first run |
| `add -A`, `status --porcelain -z`, `commit -q -F -` | local commits |
| `rev-parse -q --verify HEAD` | is there history yet |
| `fetch -q origin`, `for-each-ref ... refs/ogs/remote` | pull |
| `merge-tree --write-tree ...`, `merge ... -X ours refs/ogs/remote`, `cat-file --batch` | merge and keep both versions |
| `push -q origin` | push, never forced |
| `credential approve` | save the token to the keychain |

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
| macOS only for now | the token lives in the macOS keychain |

## Develop

`make` lists the targets. `make setup && make test` runs the unit tests and a two-Mac simulation against a bare repo, without Orca. `make lint`, `make build`.

## Credit and license

Built on the OrcaSlicer plugin system. OrcaSlicer is by SoftFever and the OrcaSlicer contributors (github.com/OrcaSlicer/OrcaSlicer), licensed AGPL-3.0.

This plugin is AGPL-3.0-or-later as well, see `LICENSE`. It runs inside OrcaSlicer and uses its `orca` module, so it follows OrcaSlicer's license.
