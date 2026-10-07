"""Command line interface: `flatkeep add|watch|edit|list|update|remove|...`.

Each cmd_* function carries out one command and returns the exit status
(0 = success). What gets printed comes from core/describe.py.
"""

import argparse
import sys

from .core import background, describe, host, store, updater
from .core.store import TrackedApp

# Examples from the other modules, used in this module's examples.
from .core.store import FLATPAK_REPO, NUVIO  # noqa: E402


def find_app(apps: list[TrackedApp], app_id: str, installed_only: bool = False) -> TrackedApp | None:
    """The app with app_id; with installed_only, watched repos don't count.

    >>> find_app([NUVIO, FLATPAK_REPO], "github:flatpak/flatpak").name
    'flatpak'
    >>> find_app([NUVIO, FLATPAK_REPO], "github:flatpak/flatpak", installed_only=True) is None
    True
    """
    return next((a for a in apps if a.app_id == app_id and not (installed_only and a.is_watch)), None)


def apps_to_update(apps: list[TrackedApp], app_ids: list[str]) -> list[TrackedApp]:
    """The installed apps `flatkeep update` should update: the ones named,
    or all of them when none are.

    >>> [a.name for a in apps_to_update([NUVIO, FLATPAK_REPO], [])]
    ['Nuvio']
    >>> apps_to_update([NUVIO], ["org.example.Other"])
    []
    """
    installed = [a for a in apps if not a.is_watch]
    return [a for a in installed if a.app_id in app_ids] if app_ids else installed


def _progress(fraction: float) -> None:
    """Effect: shows download progress on one terminal line."""
    print(f"\r{describe.progress_text(fraction):<20}", end="", file=sys.stderr, flush=True)
    if fraction >= 1:
        print(file=sys.stderr)


def cmd_add(args) -> int:
    """Track and install an app; suggests `watch` if it has no .flatpak."""
    try:
        app = updater.add(args.repo, _progress, include_prereleases=args.prereleases)
    except updater.NoFlatpakError as e:
        print(f"error: {e}\nTo get notified about its releases instead: flatkeep watch {args.repo}", file=sys.stderr)
        return 1
    print(f"Tracking {app.name} ({app.app_id}) at {app.installed_tag}")
    return 0


def cmd_watch(args) -> int:
    """Watch a repo for new releases."""
    app = updater.watch(args.repo, include_prereleases=args.prereleases)
    print(f"Watching {app.repo}, latest release is {app.seen_tag}")
    return 0


def cmd_edit(args) -> int:
    """Change an app's settings; options left out stay as they are."""
    app = find_app(store.load(), args.app_id)
    if app is None:
        print(f"error: {args.app_id} isn't tracked (see: flatkeep list)", file=sys.stderr)
        return 1
    app = updater.edit(
        app, name=args.name, repo=args.repo, include_prereleases=args.prereleases,
        auto_update=args.auto_update, asset_pattern=args.file_filter, wm_class=args.window_class,
    )
    print(describe.settings_line(app))
    return 0


def cmd_fix_taskbar(args) -> int:
    """Apply the taskbar fix, detecting the window class unless given."""
    app = find_app(store.load(), args.app_id, installed_only=True)
    if app is None:
        print(f"error: {args.app_id} isn't an installed app tracked by Flatkeep", file=sys.stderr)
        return 1
    wm_class = args.window_class or updater.detect_wm_class(app)
    if wm_class is None:
        print(f"{app.name}'s window already matches its launcher, nothing to fix")
        return 0
    updater.edit(app, wm_class=wm_class)
    print(f"Set window class {wm_class!r} for {app.app_id}. Close and reopen the app to see it.")
    return 0


def cmd_list(args) -> int:
    """Show every tracked app and whether there's something new."""
    apps = store.load()
    if not apps:
        print("No apps tracked yet. Add one with: flatkeep add <github-repo>")
        return 0
    installed = host.installed_apps()
    for app in apps:
        print(describe.list_line(updater.check(app, installed)))
    return 0


def cmd_update(args) -> int:
    """Install available updates; fails if any app couldn't be updated."""
    failed = 0
    for app in apps_to_update(store.load(), args.app_ids):
        try:
            tag = updater.update(app, _progress)
        except Exception as e:
            print(f"{app.app_id}: {e}", file=sys.stderr)
            failed += 1
            continue
        print(f"{app.app_id}: updated to {tag}" if tag else f"{app.app_id}: up to date")
    return 1 if failed else 0


def cmd_remove(args) -> int:
    """Stop tracking an app, optionally uninstalling it."""
    updater.remove(args.app_id, uninstall=args.uninstall)
    print(f"Removed {args.app_id}" + (" and uninstalled it" if args.uninstall else ""))
    return 0


def cmd_check_updates(args) -> int:
    """What the background timer runs."""
    background.check_updates()
    return 0


def cmd_background(args) -> int:
    """Turn background checks on or off, or show whether they're on."""
    if args.state == "on":
        background.enable()
    elif args.state == "off":
        background.disable()
    print(f"Background checks are {'on' if background.is_enabled() else 'off'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The command line's commands and options; each command's function is
    stored as `func`.

    >>> build_parser().parse_args(["edit", "a.b.C", "--no-prereleases"]).prereleases
    False
    >>> build_parser().parse_args(["background"]).state
    'status'
    """
    parser = argparse.ArgumentParser(
        prog="flatkeep",
        description="Install and update Flatpak bundles from GitHub releases. Run without arguments to open the app.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add", help="track a GitHub repo and install its latest .flatpak")
    p.add_argument("repo", help="GitHub URL or owner/name")
    p.add_argument("--prereleases", action="store_true", help="also use pre-releases")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("watch", help="get notified about new releases of any GitHub repo, without installing")
    p.add_argument("repo", help="GitHub URL or owner/name")
    p.add_argument("--prereleases", action="store_true", help="also notify about pre-releases")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("edit", help="change an app's settings")
    p.add_argument("app_id")
    p.add_argument("--name")
    p.add_argument("--repo", help="new GitHub URL or owner/name")
    p.add_argument("--prereleases", action=argparse.BooleanOptionalAction, help="use pre-releases")
    p.add_argument("--auto-update", action=argparse.BooleanOptionalAction, help="install updates in the background")
    p.add_argument("--file-filter", help="regex to choose between several .flatpak files")
    p.add_argument("--window-class", help="taskbar fix: the class the app's window reports ('' to undo)")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("fix-taskbar", help="make an app's open window join its pinned taskbar icon")
    p.add_argument("app_id")
    p.add_argument("--window-class", help="skip detection and use this class (detection needs KDE Plasma)")
    p.set_defaults(func=cmd_fix_taskbar)

    p = sub.add_parser("list", help="show tracked apps and available updates")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("update", help="install available updates")
    p.add_argument("app_ids", nargs="*", help="only update these apps (default: all)")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("check-updates", help="install or announce updates with notifications (used by the timer)")
    p.set_defaults(func=cmd_check_updates)

    p = sub.add_parser("background", help="turn background checks on or off")
    p.add_argument("state", nargs="?", choices=["on", "off", "status"], default="status")
    p.set_defaults(func=cmd_background)

    p = sub.add_parser("remove", help="stop tracking an app")
    p.add_argument("app_id")
    p.add_argument("--uninstall", action="store_true", help="also uninstall the app")
    p.set_defaults(func=cmd_remove)
    return parser


def main(argv: list[str]) -> int:
    """Run the command in argv; errors are printed, not raised."""
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
