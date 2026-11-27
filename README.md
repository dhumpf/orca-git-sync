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
| Both sides changed a file that is not a preset | the repo's version wins. The file is not written to your preset folder |

It never force-pushes. A rejected push fetches, merges and tries once more.

Only `*.json` presets directly inside `filament/`, `process/` and `machine/` are synced, both ways. Orca's `.info` files hold per-account cloud ids and are left out on purpose, as are `.bak` copies and everything else in the folder. Other files in the repo stay in git and are never written to the folder.

The git repo itself lives in the plugin's storage folder, not in Orca's preset folder, so nothing is added there.

## What this plugin accesses

Orca's plugin review lists what a plugin can touch. This is what Git Sync does with each of those, read from the code.

| What | Why | How to limit or change it |
|---|---|---|
| Runs git as a separate process, only the fixed commands under [Git calls](#git-calls). Each call stops after 60 s | all the sync work is git. It is looked up in the usual install places (Homebrew or Xcode on macOS, Git for Windows) and otherwise on your `PATH` | Orca asks once to allow these commands. Without that the plugin cannot sync |
| Environment variables and the computer name | the plugin reads neither. Git starts with Orca's own environment as it is, like any program Orca starts | nothing to set. Git still reads your own global git config, except hooks, commit signing, credential helpers, HTTP redirects and links, which the clone's config switches off. A `GIT_*` variable set in Orca's environment does reach git, see [Git calls](#git-calls) |
| Reads and writes your Orca preset folder | commits only `*.json` directly inside `filament/`, `process/` and `machine/`. A pull writes, updates or deletes only those files, plus a `(git conflict ...)` copy when both sides changed a preset. A link in the repo arrives as a plain file, and a folder named like a preset is not checked out. Links and folders in the preset folder are never read or committed as presets. Any other file in the repo stays in git and is not written to the folder. When a merge conflicts on such a file, the repo's version wins in git. Such files an older version wrote there are removed on the next pull that brings changes, unless you edited them. An edited one stays, and while a pull runs it is renamed to `<name>.ogs-aside` and put back right after, so git does not stop on it. See the exceptions below the table | pick the folder with Preset folder. Give push access to the repo only to people you trust |
| Writes its own storage folder | the git repo with its config, a `git-<folder>-run` folder git starts in, the last git output and `sync.log` (one line per sync) live here, so no `.git` folder ends up among your presets | nothing to set. `sync.log` holds the sync summaries and git error messages, no token. The plugin never writes your global git config |
| Talks to the repo URL you set | fetch and push through git, on start, 2 s after a save, on Sync now and on quit. HTTP redirects are not followed, so git never goes on to another address. The plugin makes no other network call | the repo URL in the config. If it redirects, sync stops with a message to set the final URL. A proxy or `insteadOf` rule in your global git config still applies |
| Saves the token to the system credential store | the token field goes to `git credential approve` on stdin. macOS keychain, Windows Credential Manager, or libsecret on Linux. Credential helpers from your own git config are cleared for every plugin git call, so only that store gets the token. The plugin does not write it to its config, `sync.log` or any other file | use a token that can only read and write the presets repo. Revoke it at your git host, or delete it from the keychain or Credential Manager. Linux without libsecret keeps it in git's memory cache, for up to 30 days or until you log out |
| Starts background threads | the sync runs beside Orca so saving a preset does not freeze it | Orca asks once together with the git commands |

Exceptions to the preset folder row:

- A submodule in the repo named like a preset is checked out as an empty folder. It is never read or committed.
- A file you name `<name>.ogs-aside` anywhere in the preset folder is taken as one the plugin set aside. The next sync renames it back to `<name>` if nothing is there.
- If Orca rewrites an edited leftover while a pull has it set aside, and the repo changed that file too, later syncs stop with git's "local changes would be overwritten" until you remove `<name>.ogs-aside` by hand. Only clones made by 0.3.1 or older have such files.

## Install

1. Download [`orca_git_sync.py`](https://github.com/dhumpf/orca-git-sync/releases/latest/download/orca_git_sync.py) from the latest release (or `make build` puts it in `dist/`). For a newer version later, download and install it again.
2. In OrcaSlicer: main menu > Plugins > Browse plugins (split button) > Install local plugin, pick the file.
3. Orca asks once to allow the git commands listed below and to start threads. Allow it.
4. Needs git. macOS: `/usr/bin/git` from the Xcode command line tools works, Homebrew git is picked up first. Windows: Git for Windows. Linux: the distro package.

Orca Cloud: with an Orca Cloud account you can instead go to Browse plugins, search Git Sync and Subscribe. Updates then come through the Update button in Plugins.

Switching from a local install to Orca Cloud: remove the local copy in Plugins first, then subscribe. The two installs may keep separate plugin storage, so the cloud copy would start with no token, no clone and an empty log, and two copies running at once could both sync the same preset folder.

## Configure

| Where | What |
|---|---|
| Plugins > Git Sync > Config | repo URL (https), branch (default `main`), username, commit author, preset folder (empty = auto) |
| the Git Sync script action | status, Sync now, and the token field |

The token is handed to `git credential approve` and stored by the OS credential store: the macOS keychain (`osxkeychain`), the Windows Credential Manager (`manager`) or libsecret on Linux. The plugin clears the credential helpers from your own git config for its calls, so only that store gets the token, and fetch and push only read it from there. The plugin never writes it to its config or any other file. Use a token that can only read and write the presets repo.

Preset folder auto: Orca keeps presets in `user/<account id>` when you are signed in to a cloud account and in `user/default` when not. The plugin asks Orca which one it loaded, and falls back to the single account folder. With several account folders, set it by hand.

## Git calls

Each call has a fixed command line, so Orca can grant each one once. Repo URL, branch, author and the switches below are written to the clone's own config file in the plugin's storage folder before each sync, text goes through stdin. Git runs in the `git-<folder>-run` folder next to the clone, whose `.git` file points at it.

The plugin reads no environment variables and passes git no environment of its own, so git starts with Orca's environment unchanged. A `GIT_*` variable set there, for example `GIT_DIR` or `GIT_CONFIG_COUNT`, reaches git and can override the clone's config. Orca started from the Dock or Start menu has none of these. Git runs without a terminal. For an https repo URL, the clone's last credential helper stops git when the credential store has no token, so git never waits on a password prompt or runs an askpass program. An ssh repo URL logs in through ssh itself, with your keys and ssh agent. If a key needs a passphrase and none is loaded, ssh may open the askpass program set in Orca's environment.

| Call | Used for |
|---|---|
| `init -q --bare` | first run, in the new clone folder. The plugin then writes its config |
| `status --porcelain -z --untracked-files=all`, `add -A --pathspec-from-file=- --pathspec-file-nul`, `commit -q -F -` | local commits. The plugin picks the preset paths from `status` and stages only those, by name on stdin |
| `rev-parse -q --verify HEAD` | is there history yet |
| `fetch -q origin`, `for-each-ref ... refs/ogs/remote` | pull |
| `merge-tree --write-tree ...`, `merge ... -X ours refs/ogs/remote`, `cat-file --batch` | merge and keep both versions |
| `update-index -z --index-info` | a merge conflict on a file that is not a preset: put the repo's version in git without writing the folder |
| `push -q origin` | push, never forced |
| `credential approve` | save the token to the credential store |

Your global git hooks, commit signing, credential helpers and HTTP redirects are switched off for these calls by the clone's config, and links are checked out as plain files. A pull uses a sparse checkout, so only the preset json lands in the preset folder. The exceptions are an empty folder for a submodule named like a preset, and a file you named `*.ogs-aside`, which is renamed back. Both are listed under [What this plugin accesses](#what-this-plugin-accesses).

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
| A repo URL that redirects does not sync | redirects are not followed, so a token never goes to another address. Set the final URL, often the one ending in `.git` |

## Develop

`make` lists the targets. `make setup && make test` runs the unit tests and a two-computer simulation against a bare repo, without Orca. `make lint`, `make build`. `make ci` is what CI runs.

## Release

1. Bump `version` in the plugin header and `VERSION`, add a `## x.y.z` section to `CHANGELOG.md`, merge to main.
2. On an up to date main: `make release TAG=vX.Y.Z && gh release create vX.Y.Z --target main --title vX.Y.Z --notes-file dist/release-notes.md`

`make release` runs locally first and stops before anything is published when the tag is not `vX.Y.Z`, does not match the version in the file, or `CHANGELOG.md` has no section for it. CI runs lint, the tests and the version and changelog checks on every pull request.

Publishing the release starts `publish-orcacloud.yml`. It checks again, attaches `orca_git_sync.py`, the zip and `orca_git_sync_any.py`, and sends the release to Orca Cloud with the release notes as the changelog. A pre-release gets its files but does not go to Orca Cloud.

Promoting that pre-release to a full release later does not send it to Orca Cloud either. GitHub fires `released` then, and the workflow only listens for `published`. To ship that version, bump it and cut a new normal release.

## Credit and license

Built on the OrcaSlicer plugin system. OrcaSlicer is by SoftFever and the OrcaSlicer contributors (github.com/OrcaSlicer/OrcaSlicer), licensed AGPL-3.0.

This plugin is AGPL-3.0-or-later as well, see `LICENSE`. It runs inside OrcaSlicer and uses its `orca` module, so it follows OrcaSlicer's license.
