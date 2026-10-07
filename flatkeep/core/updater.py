"""The main workflow: add an app, check it for updates, install updates.

Both the CLI and the GTK interface call these functions; none of them touch
the UI, so they can run in a background thread.
"""

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from gi.repository import GLib

from . import bundle, desktop_entry, github, host, store, windows
from .github import Asset, Release
from .store import WATCH, TrackedApp

CACHE_DIR = Path(GLib.get_user_cache_dir()) / "flatkeep"


def normalize(version: str | None) -> str:
    """'v1.2.0' and '1.2.0' count as the same version."""
    return (version or "").strip().lstrip("vV")


class NoFlatpakError(LookupError):
    """The release has no .flatpak file, so the repo can only be watched."""


@dataclass
class Status:
    app: TrackedApp
    installed: str | None  # version flatpak reports, None if not installed
    release: Release | None = None
    asset: Asset | None = None
    error: str | None = None

    @property
    def current(self) -> str:
        if self.app.is_watch:
            return self.app.seen_tag
        return self.app.installed_tag or self.installed or ""

    @property
    def update_available(self) -> bool:
        """For watched repos: a release the user hasn't seen yet."""
        if self.error or not self.release:
            return False
        if self.app.is_watch:
            return normalize(self.release.tag) != normalize(self.app.seen_tag)
        if not self.asset:
            return False
        if self.installed is None:
            return True
        latest = normalize(self.release.tag)
        return latest not in (normalize(self.app.installed_tag), normalize(self.installed))


def check(app: TrackedApp, installed_apps: dict[str, str] | None = None) -> Status:
    if installed_apps is None:
        installed_apps = host.installed_apps()
    status = Status(app, installed_apps.get(app.app_id))
    try:
        if app.is_watch:
            status.release = github.latest_release(app.repo, app.include_prereleases)
        else:
            status.release, status.asset = find_flatpak_release(app.repo, app.include_prereleases, app.asset_pattern)
    except Exception as e:
        status.error = str(e)
    return status


def find_flatpak_release(repo: str, include_prereleases: bool = False, pattern: str = "") -> tuple[Release, Asset]:
    """The newest release with a .flatpak file for this computer.

    With pre-releases, skip ones without a .flatpak (e.g. a nightly that only
    ships an AppImage) instead of failing.
    """
    first = None
    for release in github.releases(repo, include_prereleases):
        first = first or release
        if asset := github.pick_flatpak_asset(release, pattern):
            return release, asset
    if first is None:
        raise LookupError(f"{repo} has no releases yet")
    raise NoFlatpakError(f"Release {first.tag} of {repo} has no .flatpak file for this computer")


def add(repo_text: str, progress=None, include_prereleases: bool = False) -> TrackedApp:
    """Start tracking a repo, installing its latest release if needed."""
    repo = github.parse_repo(repo_text)
    _check_not_tracked(repo)

    release, asset = find_flatpak_release(repo, include_prereleases)

    path = _download(asset, progress)
    try:
        info = bundle.read_bundle(path)
        installed = host.installed_apps().get(info.app_id)
        if installed is None or normalize(installed) != normalize(release.tag):
            host.install_bundle(path)
    finally:
        path.unlink(missing_ok=True)

    if info.icon:
        store.save_icon(info.app_id, info.icon)
    app = TrackedApp(
        app_id=info.app_id, repo=repo, name=info.name,
        installed_tag=release.tag, include_prereleases=include_prereleases,
    )
    store.put(app)
    return app


def watch(repo_text: str, include_prereleases: bool = False) -> TrackedApp:
    """Track a repo's releases without installing anything."""
    repo = github.parse_repo(repo_text)
    _check_not_tracked(repo)
    release = github.latest_release(repo, include_prereleases)
    app = TrackedApp(
        app_id=f"github:{repo}", repo=repo, name=repo.split("/")[1], kind=WATCH,
        include_prereleases=include_prereleases, seen_tag=release.tag, notified_tag=release.tag,
    )
    _save_avatar(app)
    store.put(app)
    return app


def edit(
    app: TrackedApp,
    *,
    name: str | None = None,
    repo_text: str | None = None,
    include_prereleases: bool | None = None,
    auto_update: bool | None = None,
    asset_pattern: str | None = None,
    wm_class: str | None = None,
) -> TrackedApp:
    """Change an app's settings. Only the arguments that are passed change."""
    old_id = app.app_id
    prereleases = app.include_prereleases if include_prereleases is None else include_prereleases

    if asset_pattern is not None:
        try:
            re.compile(asset_pattern)
        except re.error as e:
            raise ValueError(f"File name filter isn't a valid pattern: {e}") from None

    new_repo = github.parse_repo(repo_text) if repo_text is not None else app.repo
    repo_changed = new_repo.lower() != app.repo.lower()
    if repo_changed:
        _check_not_tracked(new_repo)

    # Make sure the new settings still find a release before saving them.
    if repo_changed or include_prereleases is not None or asset_pattern is not None:
        if app.is_watch:
            release = github.latest_release(new_repo, prereleases)
        else:
            pattern = app.asset_pattern if asset_pattern is None else asset_pattern
            release, _asset = find_flatpak_release(new_repo, prereleases, pattern)

    if name is not None:
        app.name = name.strip() or new_repo.split("/")[1]
    if include_prereleases is not None:
        app.include_prereleases = include_prereleases
    if auto_update is not None:
        app.auto_update = auto_update
    if asset_pattern is not None:
        app.asset_pattern = asset_pattern.strip()
    if repo_changed:
        app.repo = new_repo
        if app.is_watch:
            # A different repo is a fresh start: its current release counts as seen.
            app.app_id = f"github:{new_repo}"
            app.seen_tag = app.notified_tag = release.tag

    if wm_class is not None and not app.is_watch:
        wm_class = wm_class.strip()
        if wm_class:
            desktop_entry.apply(app.app_id, wm_class)
        else:
            desktop_entry.remove(app.app_id)
        app.wm_class = wm_class

    if app.app_id != old_id:
        store.remove(old_id)
        _save_avatar(app)
    store.put(app)
    return app


def mark_seen(app: TrackedApp, tag: str) -> None:
    app.seen_tag = tag
    app.notified_tag = tag
    store.put(app)


def update(app: TrackedApp, progress=None) -> str | None:
    """Install the latest release if it's newer. Returns the new tag, or None."""
    if app.is_watch:
        return None
    status = check(app)
    if status.error:
        raise RuntimeError(status.error)
    if not status.update_available:
        return None

    path = _download(status.asset, progress)
    try:
        info = bundle.read_bundle(path)
        # Don't let a release silently replace a different app.
        if info.app_id != app.app_id:
            raise RuntimeError(f"Release contains {info.app_id}, expected {app.app_id}")
        host.install_bundle(path)
    finally:
        path.unlink(missing_ok=True)

    if info.icon:
        store.save_icon(app.app_id, info.icon)
    app.installed_tag = status.release.tag
    store.put(app)
    # Refresh our launcher copy from the new version's launcher.
    if app.wm_class:
        try:
            desktop_entry.apply(app.app_id, app.wm_class)
        except Exception as e:
            print(f"{app.app_id}: couldn't refresh the taskbar fix: {e}", file=sys.stderr)
    return app.installed_tag


def detect_wm_class(app: TrackedApp) -> str | None:
    """The class the app's open window reports, if it differs from its app ID.

    Returns None when the window already matches. Raises LookupError when the
    app has no open window.
    """
    window = windows.find_window(app.app_id)
    if window is None:
        raise LookupError(f"Open {app.name or app.app_id} first, then try again")
    if window.desktop_file == app.app_id or window.wm_class.lower() == app.app_id.lower():
        return None
    return window.wm_class


def remove(app_id: str, uninstall: bool = False) -> None:
    app = next((a for a in store.load() if a.app_id == app_id), None)
    if app and app.wm_class:
        desktop_entry.remove(app_id)
    if uninstall and not app_id.startswith("github:") and app_id in host.installed_apps():
        host.uninstall(app_id)
    store.remove(app_id)


def _save_avatar(app: TrackedApp) -> None:
    """The repo owner's GitHub avatar stands in for a watched repo's icon."""
    try:
        store.ICON_DIR.mkdir(parents=True, exist_ok=True)
        github.download(f"https://github.com/{app.repo.split('/')[0]}.png?size=128", store.icon_path(app.app_id))
    except OSError:
        pass


def _check_not_tracked(repo: str) -> None:
    if any(a.repo.lower() == repo.lower() for a in store.load()):
        raise ValueError(f"{repo} is already in the list")


def _download(asset: Asset, progress) -> Path:
    # The cache lives under the real home folder, so the host's flatpak
    # command can read the file even when we run inside the sandbox.
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / asset.name
    github.download(asset.url, path, progress)
    return path
