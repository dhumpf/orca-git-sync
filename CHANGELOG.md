# Changelog

The section for a version becomes its GitHub release notes and its Orca Cloud changelog.

## 0.3.2

Sync touches only preset json, and the token goes only to the plugin's credential store.

| | |
|---|---|
| Commits | only `*.json` directly inside `filament/`, `process/` and `machine/` are staged, picked by name. A `.gitignore` in the folder or a file the repo already tracks can no longer get anything else committed. `.info` files are never committed. Links and folders named like a preset are skipped |
| Pulls | only preset json is written to the preset folder. Other files in the repo stay in git. A link in the repo arrives as a plain file, and a folder named like a preset is not checked out |
| Open merges | a merge git left open, for example a preset deleted on one side and edited on the other, is finished on the next sync. A preset you saved in between stays yours, and the other version is kept as a copy. A merge git could finish by itself is committed at once and not reopened. If Orca stops before the conflict copy for it is written, the copy is not made later, and the other version stays only in the repo history |
| Extra tracked files | files in the repo other than preset json no longer sync. They stay in the repo as they are. Ones an older version checked out are removed from the folder on the next pull that brings changes. Ones you or Orca edited, `.info` files included, stay in the folder and no longer stop the sync. The plugin never stages them or deletes them from the repo. When an unpushed commit from an older version changed one and the repo changed it too, the repo's version wins and nothing is written to the folder |
| Large syncs | the commit message lists changed paths up to about 12 KB and then counts the rest, so a sync or merge with hundreds of presets no longer fails |
| Token | credential helpers from your own git config are cleared for the plugin's git calls. A global `store` helper no longer gets a copy. If you relied on your own helper and never saved a token in the Git Sync window, save it there once |
| Redirects | not followed. A moved or renamed repo, or a URL your host redirects, now fails with a message. Set the final URL, often the one ending in `.git` |
| Permissions | the `add` and `status` commands changed and `update-index -z --index-info` is new, so Orca asks once more to allow the git commands |

## 0.3.1

Releases publish themselves.

| | |
|---|---|
| Orca Cloud | each release also goes to the Git Sync listing on Orca Cloud. Subscribed users get it through the Update button in Plugins |
| Plugin | no change to the sync itself |
| Moving to Orca Cloud | remove the local install before you subscribe. The two may keep separate plugin storage, so the token, the clone and the log would start fresh, and two copies could sync the same folder |

## 0.3.0

Git gets only what it needs from your environment.

| | |
|---|---|
| Environment | git sees `PATH`, `HOME`, `LANG`, `TMPDIR`, plus the credential helper variables on Windows (`SYSTEMROOT`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`) and Linux (`DBUS_SESSION_BUS_ADDRESS`, `XDG_RUNTIME_DIR`). Tokens and API keys in your shell never reach git |
| Plugin review | still lists "Reads environment variables": the allowlist is read from the environment, see the README |
| Tested | macOS and Linux. Windows is untested, reports welcome |

## 0.2.0

Runs on Windows and Linux too.

| | |
|---|---|
| git | found per OS: Homebrew or Xcode git on macOS, Git for Windows, the distro git on Linux |
| Token | stored by the OS credential store: macOS keychain, Windows Credential Manager, libsecret on Linux (in-memory cache when libsecret is missing) |
| Tested | macOS and Linux. Windows is untested, reports welcome |

## 0.1.0

First release. Syncs OrcaSlicer user presets (printers, filaments, processes) through a git repo, so two Macs or two people share one set of presets with full history.

| | |
|---|---|
| Orca | 2.5 plugin API (experimental upstream) |
| Install | download `orca_git_sync.py`, Orca > Plugins > Browse plugins > Install local plugin |
| Needs | git on the Mac (Xcode command line tools or Homebrew) |
| Auth | HTTPS token in the macOS keychain |

Conflicts keep both versions (`<name> (git conflict <date>)`), a delete against an edit keeps the edit, and it never force-pushes. See the README for the details.
