# Flatkeep

Install and update Flatpak apps that are published as `.flatpak` files on
GitHub releases, before (or instead of) being on Flathub. Like Gear Lever, but
for Flatpak bundles.

It can also watch any other GitHub repository and notify you when a new
release comes out, without installing anything.

## Run it while developing

No build step needed; the host already has Python, GTK 4 and libadwaita:

```sh
python3 -m flatkeep                  # open the window
python3 -m flatkeep add https://github.com/NuvioMedia/NuvioDesktop
python3 -m flatkeep watch https://github.com/owner/repo   # notify only
python3 -m flatkeep add|watch <repo> --prereleases          # include alpha/beta/nightly
python3 -m flatkeep edit <app-id> [--name N] [--repo URL] [--[no-]prereleases]
                                  [--[no-]auto-update] [--file-filter REGEX]
                                  [--window-class CLASS]
python3 -m flatkeep fix-taskbar <app-id> [--window-class CLASS]
python3 -m flatkeep list             # tracked apps and available updates
python3 -m flatkeep update           # install every available update
python3 -m flatkeep remove com.nuvio.media.desktop [--uninstall]
python3 -m flatkeep background on|off|status
python3 -m flatkeep check-updates    # what the background timer runs
```

Set `GITHUB_TOKEN` if you hit GitHub's limit of 60 API requests per hour.

Run the tests (the examples written in the docstrings of `flatkeep/core/`):

```sh
python3 -m unittest
```

## Build the Flatpak

```sh
./build-flatpak.sh            # builds dist/flatkeep-x86_64.flatpak
./build-flatpak.sh --install  # and installs it for your user
flatpak run io.github.cerels.Flatkeep
```

The first build downloads the GNOME 50 SDK (about 1 GB).

## Automatic builds and releases

`.github/workflows/flatpak.yml` runs `build-flatpak.sh` on GitHub Actions:

- **Every push and pull request:** builds the Flatpak. Download it from the
  run's *Artifacts* section.
- **Pushing a tag like `v0.2.0`:** also creates a GitHub Release with
  `flatkeep-x86_64.flatpak` attached. Tags with a `-` (`v0.2.0-beta.1`) are
  marked as pre-releases.

To release a new version:

1. Set `VERSION` in `flatkeep/__init__.py`.
2. Add a `<release version="..." date="...">` entry at the top of
   `<releases>` in `data/io.github.cerels.Flatkeep.metainfo.xml`.
3. Commit, then `git tag v0.2.0 && git push --tags`.

The workflow fails if the tag doesn't match those two places. Once a release
exists, Flatkeep can keep itself updated: add its own GitHub repo in the app.

## Editing apps

Each app's ⋮ menu has **Edit…**: name, GitHub URL, pre-releases, automatic
installs and a file name filter. Switches save immediately; text fields save
with Enter or ✓. A change is only saved if GitHub still finds a usable release
with it, otherwise the old value comes back.

With pre-releases on, an installed app gets the newest release that has a
`.flatpak` file; a newer pre-release without one is skipped.

## Taskbar fix

Some apps (often Java, .NET or Electron ones) report a window class that
doesn't match their app ID, so the open window gets its own taskbar icon
instead of joining the pinned one. In **Edit… → Taskbar**, open the app and
click **Detect** (or run `flatkeep fix-taskbar <app-id>`):

1. A small KWin script lists the open windows (KDE Plasma only; elsewhere,
   type the class by hand).
2. Each window's process is matched to the app through its systemd scope,
   `app-flatpak-<app-id>-*.scope`.
3. If the class differs from the app ID, Flatkeep writes
   `~/.local/share/applications/<app-id>.desktop` with `StartupWMClass` set.
   That folder overrides Flatpak's own launcher and survives app updates.

Flatkeep marks its own launcher copies with `X-Flatkeep-Generated=true` and
rebuilds them from the app's launcher after every update. A launcher that was
already there (made by you or the app) only gets its `StartupWMClass` line
changed and is never deleted.

## Background checks

"Check for Updates in the Background" in the main menu (or `flatkeep background on`)
writes a systemd user timer, `~/.config/systemd/user/flatkeep-update.timer`, that
runs `flatkeep check-updates` 5 minutes after boot and every 6 hours. For each app it:

- installs the update and sends a notification, if "Install Updates Automatically"
  is on for that app (the default);
- otherwise, and for watched repos, sends one notification per new release.

The timer runs whichever copy of Flatkeep turned it on (your source checkout
or the Flatpak). After moving the folder or switching to the Flatpak build,
turn it off and on again.

Logs: `journalctl --user -u flatkeep-update`

## How it works

1. Ask the GitHub API for the repo's latest release and pick the `.flatpak`
   file for this CPU architecture.
2. Download it and read the metadata inside the bundle (app ID, name, icon,
   runtime), so the user only has to paste a repo URL.
3. Install it with `flatpak install --user --bundle`. Inside the sandbox this
   goes through `flatpak-spawn --host`.
4. Remember the repo and installed release tag in `~/.config/flatkeep/apps.json`
   and compare it with the latest tag on each check.

## Project layout

```
flatkeep/
  core/            no GTK here: usable from the CLI, the UI or tests;
                   written with the HtDP design recipe (see CLAUDE.md)
    github.py      release lookup, picking the asset, downloading
    bundle.py      reading app ID/name/icon from a .flatpak file
    host.py        every call to the `flatpak` command
    store.py       the list of tracked apps
    updater.py     add / watch / edit / check / update / remove workflows
    background.py  the systemd timer and what it runs
    notify.py      desktop notifications without the window open
    windows.py     reading window classes from KWin
    desktop_entry.py  launcher copies with StartupWMClass (taskbar fix)
  ui/              GTK 4 + libadwaita window and the Edit dialog
  cli.py           `flatkeep add|list|update|remove`
tests/             runs the docstring examples of flatkeep/core
bin/flatkeep       launcher used inside the Flatpak
data/              desktop file, metainfo, icon
io.github.cerels.Flatkeep.yml   Flatpak manifest
```

## Ideas for next steps

- Click a notification to open Flatkeep or the release page.
- Let the user choose which file to use when a release has several `.flatpak` files
  (`asset_pattern` is already stored for this).
- Pre-releases, GitLab and Codeberg support.
- Install a missing runtime from Flathub before installing a bundle.
- Settings for the GitHub token and how often to check (now fixed at 6 hours).
