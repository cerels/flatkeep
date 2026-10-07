"""Command line interface: `flatkeep add|watch|edit|list|update|remove|...`."""

import argparse
import sys

from .core import background, host, store, updater


def _progress(fraction: float) -> None:
    label = "Installing…" if fraction >= 1 else f"Downloading… {fraction:.0%}"
    print(f"\r{label:<20}", end="", file=sys.stderr, flush=True)
    if fraction >= 1:
        print(file=sys.stderr)


def cmd_add(args) -> int:
    try:
        app = updater.add(args.repo, _progress, include_prereleases=args.prereleases)
    except updater.NoFlatpakError as e:
        print(f"error: {e}\nTo get notified about its releases instead: flatkeep watch {args.repo}", file=sys.stderr)
        return 1
    print(f"Tracking {app.name} ({app.app_id}) at {app.installed_tag}")
    return 0


def cmd_watch(args) -> int:
    app = updater.watch(args.repo, include_prereleases=args.prereleases)
    print(f"Watching {app.repo}, latest release is {app.seen_tag}")
    return 0


def cmd_edit(args) -> int:
    app = next((a for a in store.load() if a.app_id == args.app_id), None)
    if app is None:
        print(f"error: {args.app_id} isn't tracked (see: flatkeep list)", file=sys.stderr)
        return 1
    app = updater.edit(
        app, name=args.name, repo_text=args.repo, include_prereleases=args.prereleases,
        auto_update=args.auto_update, asset_pattern=args.file_filter, wm_class=args.window_class,
    )
    print(f"{app.app_id}: repo {app.repo}, pre-releases {'on' if app.include_prereleases else 'off'}"
          + ("" if app.is_watch else f", auto-update {'on' if app.auto_update else 'off'}"))
    return 0


def cmd_fix_taskbar(args) -> int:
    app = next((a for a in store.load() if a.app_id == args.app_id and not a.is_watch), None)
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
    apps = store.load()
    if not apps:
        print("No apps tracked yet. Add one with: flatkeep add <github-repo>")
        return 0
    installed = host.installed_apps()
    for app in apps:
        status = updater.check(app, installed)
        if status.error:
            state = f"error: {status.error}"
        elif app.is_watch:
            state = f"new release {status.release.tag}" if status.update_available else f"watching ({status.release.tag})"
        elif status.installed is None:
            state = f"not installed (latest {status.release.tag})"
        elif status.update_available:
            state = f"{status.current} -> {status.release.tag}"
        else:
            state = f"{status.current} (up to date)"
        if status.release and status.release.prerelease:
            state += " [pre-release]"
        print(f"{app.name or app.app_id:<24} {app.app_id:<36} {state}")
    return 0


def cmd_update(args) -> int:
    apps = [a for a in store.load() if not a.is_watch]
    if args.app_ids:
        apps = [a for a in apps if a.app_id in args.app_ids]
    failed = 0
    for app in apps:
        try:
            tag = updater.update(app, _progress)
        except Exception as e:
            print(f"{app.app_id}: {e}", file=sys.stderr)
            failed += 1
            continue
        print(f"{app.app_id}: updated to {tag}" if tag else f"{app.app_id}: up to date")
    return 1 if failed else 0


def cmd_remove(args) -> int:
    updater.remove(args.app_id, uninstall=args.uninstall)
    print(f"Removed {args.app_id}" + (" and uninstalled it" if args.uninstall else ""))
    return 0


def cmd_check_updates(args) -> int:
    background.check_updates()
    return 0


def cmd_background(args) -> int:
    if args.state == "on":
        background.enable()
    elif args.state == "off":
        background.disable()
    print(f"Background checks are {'on' if background.is_enabled() else 'off'}")
    return 0


def main(argv: list[str]) -> int:
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

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
