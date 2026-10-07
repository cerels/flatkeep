# Flatkeep — context for Claude

Flatkeep installs and updates Flatpak apps published as `.flatpak` files on
GitHub releases (like Gear Lever, but for Flatpak bundles), and can "watch"
any GitHub repo to notify about new releases. Python + GTK 4 + libadwaita,
plus a CLI. User-facing features are documented in `README.md`; read it first.

- Repo: https://github.com/cerels/flatkeep (public, GPL-3.0-or-later)
- App ID: `io.github.cerels.Flatkeep` (don't change it: installed copies would stop updating)
- Latest release: v0.1.1. This is the owner's first app; explain things plainly.

## The owner's machine

- Bazzite (Fedora Atomic), KDE Plasma on Wayland. Host Python already has
  GTK 4.22 / libadwaita 1.9, so the app runs from source with no build step.
- GNOME 50 SDK + Platform are installed in the *user* Flatpak installation.
- `gh` (installed with Homebrew) is logged in as `cerels`, with `workflow` scope.
- Git identity is set **in this repo only**: Carlos Alberto Caro
  <92606132+cerels@users.noreply.github.com>. Don't use the account email.
- Icon theme is Tela (light variant on a dark panel): some symbolic icons, e.g.
  `list-add-symbolic`, render nearly invisible. That's why the Add button has a
  text label. Not a Flatkeep bug.
- The `Adwaita-WARNING ... gtk-application-prefer-dark-theme` printed at startup
  comes from KDE's `~/.config/gtk-4.0/settings.ini`. Harmless; ignore it.

## Two copies, two data folders

| Copy | How it runs | Tracked apps / icons |
|---|---|---|
| **Installed Flatpak** (what the owner uses daily) | app menu, `flatpak run io.github.cerels.Flatkeep` | `~/.var/app/io.github.cerels.Flatkeep/config/flatkeep/apps.json`, `.../data/flatkeep/icons/` |
| Source checkout (development) | `python3 -m flatkeep` in this folder | `~/.config/flatkeep/apps.json`, `~/.local/share/flatkeep/icons/` (old copy, now stale) |

The systemd timer (`~/.config/systemd/user/flatkeep-update.{timer,service}`)
runs the **installed Flatpak** (`flatpak run --command=flatkeep ... check-updates`).
Running `python3 -m flatkeep background on` from source would repoint it at
the checkout; don't do that unless asked. The owner's real tracked apps are
Nuvio (`com.nuvio.media.desktop`) and Quiver Launcher
(`io.github.tgeorgiadis.QuiverLauncher`, pre-releases on); both use the taskbar fix.

## Commands

```sh
python3 -m flatkeep                     # GUI from source
python3 -m flatkeep list                # CLI from source (see README for all commands)
flatpak run --command=flatkeep io.github.cerels.Flatkeep list   # CLI of the installed app
./build-flatpak.sh --install            # build dist/flatkeep-x86_64.flatpak and install it
python3 -m py_compile flatkeep/*.py flatkeep/*/*.py   # quick syntax check
appstreamcli validate --no-net data/io.github.cerels.Flatkeep.metainfo.xml
desktop-file-validate data/io.github.cerels.Flatkeep.desktop
```

## Code style: the HtDP design recipe

All the code follows the design recipe from *How to Design Programs*,
adapted to Python. Keep new code in the same shape:

1. **Data definitions:** each dataclass docstring says what it represents and
   what every field means ("Interpretation:"), followed by example values as
   module constants (`NUVIO`, `FLATPAK_REPO`, `NUVIO_RELEASE`, ...). Enumerations
   are commented constants (`# A Kind is one of:`, `# An Action is one of:`).
2. **Signature:** the type hints. Don't repeat them in comments.
3. **Purpose statement:** the first docstring line, saying *what* is produced.
4. **Examples:** `>>>` doctests under the purpose. They are the tests. Use
   `r"""` when an example contains `\t` or `\n`.
5. **Pure vs effects:** decisions are pure functions with examples
   (`decide_action`, `choose_flatpak_release`, `with_changes`, `fixed_launcher`,
   `parse_window_list`, ...). Functions that touch GitHub, flatpak, files,
   D-Bus or KWin say so in an `Effect:` line and stay thin: gather input, call
   the pure function, carry out the result.

`updater.edit()` copies the result of the pure `with_changes()` back into the
same `TrackedApp` object, because the window and Edit dialog keep that object.

In the UI and CLI:
- Everything the program *says* (row subtitles and buttons, `flatkeep list`
  lines, dialog questions, Edit dialog values) comes from pure functions in
  `core/describe.py`, shared by the window and the command line. Change the
  wording there, with examples.
- Yes/no dialogs are `describe.Question` values shown by `window.ask()`.
- UI methods have a purpose statement; an `Effect:` line marks the ones that
  start work outside the window (GitHub, flatpak, files, KWin).
- The Edit dialog keys its rows by `TrackedApp` field name (`self.rows`), and
  `updater.edit()` takes those same names as keyword arguments.

## Architecture rules

- `flatkeep/core/` never imports GTK; `ui/` and `cli.py` both call into it.
  Keep business logic in `core/updater.py` (add / watch / edit / check /
  update / remove) so the CLI and GUI behave the same.
- **Every host command goes through `core/host.py` `run()`**, which adds
  `flatpak-spawn --host` inside the sandbox. That includes reading/writing
  host files the sandbox can't see (`cat`, `tee`, `rm` on
  `~/.local/share/applications`, `~/.config/systemd/user`). Never call
  `subprocess` or touch those paths directly.
- Downloads go to `GLib.get_user_cache_dir()/flatkeep`. Inside the sandbox that
  is `~/.var/app/<id>/cache`, a real host path, so the host `flatpak` can read
  the bundle.
- GUI: slow work runs through `ui/tasks.py` `run_async(func, callback)`;
  only touch widgets in the callback (main thread). Progress callbacks from
  threads must go through `GLib.idle_add`.
- "Is this release new?" is `updater.is_newer()`: publish dates decide when
  known (tags can't be compared reliably), so turning pre-releases off never
  offers or installs an older release. Each check brings the app's record
  up to date (`with_release_recorded`): missing dates get filled in, and an
  app updated outside Flatkeep is recorded when its installed version equals
  the latest release's tag. Never trust other installed versions: their
  format can differ from the tags, which would cause reinstall loops.
- `store.put()` replaces an entry in place (keeps list order). Watched repos
  have IDs like `github:owner/name`; `store.icon_path()` replaces `/` with `_`.
- New `TrackedApp` fields need a default value: `store.load()` drops unknown
  keys and fills missing ones, so old `apps.json` files keep working.
- `host.installed_apps()` uses `flatpak list --columns=...` on purpose:
  `flatpak info` output is translated and can't be parsed reliably.
- Bundle metadata (app ID, name, icon, runtime) is read straight from the
  `.flatpak` GVariant in `core/bundle.py`. No install is needed to learn the app ID.

## Sandbox permissions (manifest `finish-args`) and why

- `--talk-name=org.freedesktop.Flatpak`: `flatpak-spawn --host` (install, systemctl, file access)
- `--talk-name=org.freedesktop.Notifications`: notifications from the timer run, without the window open
- `--talk-name=org.kde.KWin`: window-class detection for the taskbar fix
- Window detection also *receives* a D-Bus call from KWin on the name
  `io.github.cerels.Flatkeep.WindowList`. Flatpak lets an app own names under
  its own ID without an extra permission.

All of these were verified working inside the installed Flatpak. Flathub
reviewers will question `org.freedesktop.Flatpak`; it's essential to the app.

## Testing

`python3 -m unittest` runs every docstring example of the modules listed in
`tests/test_examples.py`; add new GTK-free modules there (CI has no GTK, so
`flatkeep/ui/` can't be listed). CI runs them before building. Beyond that, these manual checks have been used and work:

- Run the CLI against real repos: `NuvioMedia/NuvioDesktop` (has a `.flatpak`),
  `flatpak/flatpak` (no `.flatpak`, has pre-releases; good for `watch`).
  Isolate the list with `XDG_CONFIG_HOME=<scratch>/config XDG_CACHE_HOME=<scratch>/cache`
  so the owner's real lists stay untouched.
- **Never set `XDG_DATA_HOME` for a test.** Flatpak keeps the *user installation*
  there, so the host `flatpak` sees an empty one and `add` installs apps into the
  scratch folder.
- Test code that writes launchers or systemd units in a scratch dir first
  (e.g. set `desktop_entry.APPS_DIR` to a temp path). Those files affect the
  owner's real desktop.
- GUI screenshots: start the app in the background, wait, then
  `spectacle -b -n -a -o shot.png` (active window), then kill the app.
- To drive the GUI from a script, create `FlatkeepApplication()` and use
  `GLib.timeout_add` to open dialogs or `row.activate_action("row.edit", None)`.
- Listing KDE windows by hand: see `core/windows.py` (KWin script + D-Bus reply).
  KWin's `print()` output does **not** reach the journal.
- The installing-an-update path hasn't been exercised end-to-end yet: the apps
  were always already up to date.

## Releasing

1. Bump `VERSION` in `flatkeep/__init__.py`.
2. Add `<release version="X.Y.Z" date="YYYY-MM-DD">` (with a short `<description>`)
   at the **top** of `<releases>` in `data/io.github.cerels.Flatkeep.metainfo.xml`.
3. Commit, then `git tag -a vX.Y.Z -m "Flatkeep X.Y.Z" && git push origin main vX.Y.Z`.
4. `.github/workflows/flatpak.yml` builds in the `flatpak-github-actions:gnome-50`
   container, checks the tag matches steps 1–2, and publishes a GitHub Release
   with `flatkeep-x86_64.flatpak`. A tag containing `-` becomes a pre-release.
   Watch it with `gh run watch <id> --exit-status`.

Only commit, push, tag or release when the owner asks. Commit messages end with
the `Co-Authored-By` line the session gives you.

## Problems already solved (don't reintroduce)

- **Launcher icon missing in KDE:** KDE didn't notice the new `hicolor/scalable`
  folder until plasmashell restarted. The manifest now also renders 64–512 px
  PNGs with `rsvg-convert`. Keep both.
- **Taskbar shows a second icon for an app:** the window class doesn't match the
  launcher. Fixed via `StartupWMClass` in `~/.local/share/applications/<id>.desktop`
  (see README "Taskbar fix"). Launchers marked `X-Flatkeep-Generated=true` are
  Flatkeep's own and are regenerated after updates. Others only get that one line
  edited and are never deleted. Nuvio's launcher there is *not* Flatkeep's.
- `PyGObject` `GLib.KeyFile` has no `has_key()`; use `get_string` in try/except.
- `--reinstall` is required for `flatpak install --bundle` to replace an installed version.
- Don't send the GitHub token when downloading release assets: the download
  redirects to another host.

## Ideas not done yet

See "Ideas for next steps" in `README.md`. Also: Flatkeep doesn't track itself
yet (the owner hasn't decided). GitHub Actions warns that its actions run on
Node 20; bump `actions/*` versions when newer ones are available.
