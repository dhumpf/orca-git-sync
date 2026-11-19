# Changelog

The section for a version becomes its GitHub release notes and its Orca Cloud changelog.

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
