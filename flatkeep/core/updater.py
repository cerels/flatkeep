"""The main workflow: add an app, check it for updates, install updates.

Both the CLI and the GTK interface call these functions; none of them touch
the UI, so they can run in a background thread. Decisions are pure functions
with examples; the functions marked "Effect:" carry them out.
"""

import dataclasses
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from gi.repository import GLib

from . import bundle, desktop_entry, github, host, store, windows
from .github import Asset, Release
from .store import WATCH, TrackedApp
from .windows import WindowInfo

# Examples from the other modules, used in this module's examples.
from .github import MULTI_ARCH_RELEASE, NO_FLATPAK_RELEASE, NUVIO_RELEASE  # noqa: E402
from .store import FLATPAK_REPO, NUVIO  # noqa: E402

CACHE_DIR = Path(GLib.get_user_cache_dir()) / "flatkeep"


class NoFlatpakError(LookupError):
    """The release has no .flatpak file, so the repo can only be watched."""


def normalize(version: str | None) -> str:
    """The version without spaces or a leading "v", so tags compare equal.

    >>> normalize("v1.2.0"), normalize("1.2.0 "), normalize(None)
    ('1.2.0', '1.2.0', '')
    """
    return (version or "").strip().lstrip("vV")


def is_newer(release: Release, have_tags: tuple[str, ...], have_published: str) -> bool:
    """Is release newer than what we have: the release(s) tagged have_tags,
    published at have_published ("" if unknown)?

    Publish dates decide when both are known, because tags like
    "3.5.0-rc.1" and "nightly-2026-10-01" can't be compared reliably.
    Without dates, any other tag counts as newer.

    >>> stable_1_18 = NO_FLATPAK_RELEASE  # 1.18.4, published 2026-08-01
    >>> is_newer(stable_1_18, ("1.19.2",), "2026-09-20T00:00:00Z")  # pre-releases turned off
    False
    >>> is_newer(stable_1_18, ("1.18.3",), "2026-06-01T00:00:00Z")
    True
    >>> is_newer(stable_1_18, ("1.18.4",), "")  # no date saved yet: compare tags
    False
    >>> is_newer(stable_1_18, ("v1.18.3", "1.18.3"), "")
    True
    """
    if have_published and release.published:
        return release.published > have_published
    return normalize(release.tag) not in {normalize(tag) for tag in have_tags if tag}


@dataclass
class Status:
    """What a check found out about a tracked app.

    Interpretation:
    - app: the app that was checked
    - installed: the version flatpak reports, None if not installed
      (always None for watched repos)
    - release: the newest usable release, None if the check failed
    - asset: the .flatpak file to install from release (installed apps only)
    - error: why the check failed, None if it worked
    """

    app: TrackedApp
    installed: str | None
    release: Release | None = None
    asset: Asset | None = None
    error: str | None = None

    @property
    def current(self) -> str:
        """The version to show as "what you have now".

        >>> Status(NUVIO, installed="0.1.27-alpha").current
        '0.1.27-alpha'
        >>> Status(FLATPAK_REPO, installed=None).current
        '1.18.4'
        """
        if self.app.is_watch:
            return self.app.seen_tag
        return self.app.installed_tag or self.installed or ""

    @property
    def update_available(self) -> bool:
        """Is there something new? For an installed app, a release it doesn't
        have yet; for a watched repo, a release the user hasn't seen.

        >>> nuvio_asset = NUVIO_RELEASE.assets[1]
        >>> Status(NUVIO, "0.1.27-alpha", NUVIO_RELEASE, nuvio_asset).update_available
        False
        >>> newer = dataclasses.replace(NUVIO_RELEASE, tag="0.1.28-alpha")
        >>> Status(NUVIO, "0.1.27-alpha", newer, nuvio_asset).update_available
        True
        >>> Status(NUVIO, None, NUVIO_RELEASE, nuvio_asset).update_available  # uninstalled
        True
        >>> Status(NUVIO, "0.1.27-alpha", newer, None).update_available  # nothing to install
        False
        >>> Status(FLATPAK_REPO, None, dataclasses.replace(NO_FLATPAK_RELEASE, tag="1.19.0")).update_available
        True
        >>> Status(NUVIO, "0.1.27-alpha", error="offline").update_available
        False

        Never a downgrade: with an installed rc, an older stable release isn't
        an update (this happens when pre-releases are turned off).

        >>> on_rc = dataclasses.replace(NUVIO, installed_tag="0.2.0-rc.1", installed_published="2026-10-20T00:00:00Z")
        >>> Status(on_rc, "0.2.0-rc.1", NUVIO_RELEASE, nuvio_asset).update_available
        False
        """
        if self.error or not self.release:
            return False
        if self.app.is_watch:
            return is_newer(self.release, (self.app.seen_tag,), self.app.seen_published)
        if not self.asset:
            return False
        if self.installed is None:
            return True
        return is_newer(self.release, (self.app.installed_tag, self.installed), self.app.installed_published)


def choose_flatpak_release(
    releases: Iterable[Release], repo: str, pattern: str = "", arch: str | None = None
) -> tuple[Release, Asset]:
    """The newest release (releases are newest first) that has a .flatpak
    file for this computer, and that file. A newer release without one, like
    a nightly that only ships an AppImage, is skipped.

    >>> release, asset = choose_flatpak_release([NO_FLATPAK_RELEASE, MULTI_ARCH_RELEASE], "o/r", arch="x86_64")
    >>> release.tag, asset.name
    ('v2.0', 'app-x86_64.flatpak')
    >>> choose_flatpak_release([NO_FLATPAK_RELEASE], "flatpak/flatpak", arch="x86_64")
    Traceback (most recent call last):
    flatkeep.core.updater.NoFlatpakError: Release 1.18.4 of flatpak/flatpak has no .flatpak file for this computer
    >>> choose_flatpak_release([], "o/r")
    Traceback (most recent call last):
    LookupError: o/r has no releases yet
    """
    first = None
    for release in releases:
        first = first or release
        if asset := github.pick_flatpak_asset(release, pattern, arch):
            return release, asset
    if first is None:
        raise LookupError(f"{repo} has no releases yet")
    raise NoFlatpakError(f"Release {first.tag} of {repo} has no .flatpak file for this computer")


def needs_install(installed: str | None, tag: str) -> bool:
    """Should a freshly downloaded release be installed, given the version
    flatpak reports (None if not installed)?

    >>> needs_install(None, "v1.0"), needs_install("1.0", "v1.0"), needs_install("0.9", "v1.0")
    (True, False, True)
    """
    return installed is None or normalize(installed) != normalize(tag)


def is_tracked(apps: list[TrackedApp], repo: str) -> bool:
    """Is repo already in the list? GitHub names ignore case.

    >>> is_tracked([NUVIO], "nuviomedia/nuviodesktop"), is_tracked([NUVIO], "o/r")
    (True, False)
    """
    return any(app.repo.lower() == repo.lower() for app in apps)


def watched_app(repo: str, latest: Release, include_prereleases: bool = False) -> TrackedApp:
    """A new watched repo. Its current release counts as already seen, so
    only later releases are announced.

    >>> app = watched_app("flatpak/flatpak", NO_FLATPAK_RELEASE)
    >>> app.app_id, app.name, app.is_watch, app.seen_tag, app.seen_published, app.notified_tag
    ('github:flatpak/flatpak', 'flatpak', True, '1.18.4', '2026-08-01T09:00:00Z', '1.18.4')
    """
    return TrackedApp(
        app_id=f"github:{repo}", repo=repo, name=repo.split("/")[1], kind=WATCH,
        include_prereleases=include_prereleases,
        seen_tag=latest.tag, seen_published=latest.published, notified_tag=latest.tag,
    )


def check_pattern(pattern: str) -> None:
    """Raise ValueError unless pattern is a valid file name filter (regex).

    >>> check_pattern(r"x86_64.*\\.flatpak")
    >>> check_pattern("[bad")
    Traceback (most recent call last):
    ValueError: File name filter isn't a valid pattern: unterminated character set at position 0
    """
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"File name filter isn't a valid pattern: {e}") from None


def with_changes(
    app: TrackedApp,
    *,
    name: str | None = None,
    repo: str | None = None,
    include_prereleases: bool | None = None,
    auto_update: bool | None = None,
    asset_pattern: str | None = None,
    wm_class: str | None = None,
    latest: Release | None = None,
) -> TrackedApp:
    """The app with the given settings changed; None means "keep". A watched
    repo moved to another repo starts fresh: new ID, and latest (the new
    repo's newest release) counts as seen.

    >>> with_changes(NUVIO, name="  ").name  # blank name: use the repo's
    'NuvioDesktop'
    >>> changed = with_changes(NUVIO, include_prereleases=True, auto_update=False, wm_class=" X ")
    >>> changed.include_prereleases, changed.auto_update, changed.wm_class, NUVIO.auto_update
    (True, False, 'X', True)
    >>> builder_latest = dataclasses.replace(NO_FLATPAK_RELEASE, tag="1.4.12", published="2026-07-01T00:00:00Z")
    >>> moved = with_changes(FLATPAK_REPO, repo="flatpak/flatpak-builder", latest=builder_latest)
    >>> moved.app_id, moved.repo, moved.seen_tag, moved.seen_published, moved.notified_tag
    ('github:flatpak/flatpak-builder', 'flatpak/flatpak-builder', '1.4.12', '2026-07-01T00:00:00Z', '1.4.12')
    >>> with_changes(NUVIO, repo="NuvioMedia/Nuvio-Fork").app_id  # installed apps keep their ID
    'com.nuvio.media.desktop'
    """
    changes = {}
    new_repo = repo if repo is not None else app.repo
    if name is not None:
        changes["name"] = name.strip() or new_repo.split("/")[1]
    if include_prereleases is not None:
        changes["include_prereleases"] = include_prereleases
    if auto_update is not None:
        changes["auto_update"] = auto_update
    if asset_pattern is not None:
        changes["asset_pattern"] = asset_pattern.strip()
    if wm_class is not None and not app.is_watch:
        changes["wm_class"] = wm_class.strip()
    if new_repo.lower() != app.repo.lower():
        changes["repo"] = new_repo
        if app.is_watch:
            changes.update(
                app_id=f"github:{new_repo}",
                seen_tag=latest.tag, seen_published=latest.published, notified_tag=latest.tag,
            )
    return dataclasses.replace(app, **changes)


def with_dates_filled(app: TrackedApp, release: Release) -> TrackedApp:
    """The app with its missing publish date filled in, when release is the
    one it has (installed, or last seen). Apps saved before Flatkeep stored
    dates get them this way on their next check.

    >>> with_dates_filled(NUVIO, NUVIO_RELEASE).installed_published
    '2026-10-03T10:58:40Z'
    >>> newer = dataclasses.replace(NUVIO_RELEASE, tag="0.1.28-alpha", published="2026-10-10T00:00:00Z")
    >>> with_dates_filled(NUVIO, newer).installed_published  # not the one installed
    ''
    >>> with_dates_filled(FLATPAK_REPO, NO_FLATPAK_RELEASE).seen_published
    '2026-08-01T09:00:00Z'
    """
    if app.is_watch and not app.seen_published and normalize(release.tag) == normalize(app.seen_tag):
        return dataclasses.replace(app, seen_published=release.published)
    if not app.is_watch and not app.installed_published and normalize(release.tag) == normalize(app.installed_tag):
        return dataclasses.replace(app, installed_published=release.published)
    return app


def window_matches(app_id: str, window: WindowInfo) -> bool:
    """Does the window already join the app's launcher, so no taskbar fix
    is needed?

    >>> window_matches("org.kde.konsole", WindowInfo(1, "org.kde.konsole", "org.kde.konsole"))
    True
    >>> window_matches("io.github.tgeorgiadis.QuiverLauncher", WindowInfo(1, "QuiverLauncher.Desktop", ""))
    False
    """
    return window.desktop_file == app_id or window.wm_class.lower() == app_id.lower()


# Effects: everything below talks to GitHub, flatpak, files or KWin.


def find_flatpak_release(repo: str, include_prereleases: bool = False, pattern: str = "") -> tuple[Release, Asset]:
    """Effect: asks GitHub (see choose_flatpak_release)."""
    return choose_flatpak_release(github.releases(repo, include_prereleases), repo, pattern)


def check(app: TrackedApp, installed_apps: dict[str, str] | None = None) -> Status:
    """The app's status. installed_apps saves asking flatpak once per app.

    Effect: asks GitHub (and flatpak, if installed_apps isn't given), and
    saves a missing publish date (see with_dates_filled). Never raises:
    problems end up in Status.error.
    """
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

    filled = with_dates_filled(app, status.release)
    if filled != app:
        app.__dict__.update(filled.__dict__)  # callers keep this object, like edit()
        store.put(app)
    return status


def add(repo_text: str, progress=None, include_prereleases: bool = False) -> TrackedApp:
    """Start tracking a repo's app, installing its newest release if needed.

    Effect: downloads, installs, and saves the app and its icon.
    """
    repo = github.parse_repo(repo_text)
    _check_not_tracked(repo)
    release, asset = find_flatpak_release(repo, include_prereleases)

    path = _download(asset, progress)
    try:
        info = bundle.read_bundle(path)
        if needs_install(host.installed_apps().get(info.app_id), release.tag):
            host.install_bundle(path)
    finally:
        path.unlink(missing_ok=True)

    if info.icon:
        store.save_icon(info.app_id, info.icon)
    app = TrackedApp(
        app_id=info.app_id, repo=repo, name=info.name,
        installed_tag=release.tag, installed_published=release.published,
        include_prereleases=include_prereleases,
    )
    store.put(app)
    return app


def watch(repo_text: str, include_prereleases: bool = False) -> TrackedApp:
    """Track a repo's releases without installing anything.

    Effect: asks GitHub, saves the repo and the owner's avatar as its icon.
    """
    repo = github.parse_repo(repo_text)
    _check_not_tracked(repo)
    app = watched_app(repo, github.latest_release(repo, include_prereleases), include_prereleases)
    _save_avatar(app)
    store.put(app)
    return app


def edit(
    app: TrackedApp,
    *,
    name: str | None = None,
    repo: str | None = None,
    include_prereleases: bool | None = None,
    auto_update: bool | None = None,
    asset_pattern: str | None = None,
    wm_class: str | None = None,
) -> TrackedApp:
    """Change an app's settings (see with_changes); repo may be a URL. Nothing
    is saved unless GitHub still finds a usable release with the new settings.

    Effect: asks GitHub, saves the app, applies or undoes the taskbar fix.
    Also updates app itself, because the UI keeps hold of that object.
    """
    if asset_pattern is not None:
        check_pattern(asset_pattern)
    new_repo = github.parse_repo(repo) if repo is not None else app.repo
    repo_changed = new_repo.lower() != app.repo.lower()
    if repo_changed:
        _check_not_tracked(new_repo)

    latest = None
    if repo_changed or include_prereleases is not None or asset_pattern is not None:
        prereleases = app.include_prereleases if include_prereleases is None else include_prereleases
        if app.is_watch:
            latest = github.latest_release(new_repo, prereleases)
        else:
            pattern = app.asset_pattern if asset_pattern is None else asset_pattern
            latest = find_flatpak_release(new_repo, prereleases, pattern)[0]

    changed = with_changes(
        app, name=name, repo=new_repo, include_prereleases=include_prereleases,
        auto_update=auto_update, asset_pattern=asset_pattern, wm_class=wm_class, latest=latest,
    )
    # Re-applied even when unchanged: that restores a launcher that went missing.
    if wm_class is not None and not app.is_watch:
        if changed.wm_class:
            desktop_entry.apply(changed.app_id, changed.wm_class)
        else:
            desktop_entry.remove(changed.app_id)
    if changed.app_id != app.app_id:
        store.remove(app.app_id)
        _save_avatar(changed)
    store.put(changed)
    app.__dict__.update(changed.__dict__)
    return app


def mark_seen(app: TrackedApp, release: Release) -> None:
    """Effect: records that the user looked at release (and was told about it)."""
    app.seen_tag = release.tag
    app.seen_published = release.published
    app.notified_tag = release.tag
    store.put(app)


def update(app: TrackedApp, progress=None) -> str | None:
    """Install the newest release if the app doesn't have it. Returns the
    installed tag, or None if there was nothing to do.

    Effect: downloads and installs, saves the app, refreshes the taskbar fix.
    """
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
    app.installed_published = status.release.published
    store.put(app)
    # Rebuild our launcher copy from the new version's launcher.
    if app.wm_class:
        try:
            desktop_entry.apply(app.app_id, app.wm_class)
        except Exception as e:
            print(f"{app.app_id}: couldn't refresh the taskbar fix: {e}", file=sys.stderr)
    return app.installed_tag


def detect_wm_class(app: TrackedApp) -> str | None:
    """The class the app's open window reports, or None if it already
    matches the launcher. Raises LookupError if the app has no open window.

    Effect: asks KWin.
    """
    window = windows.find_window(app.app_id)
    if window is None:
        raise LookupError(f"Open {app.name or app.app_id} first, then try again")
    return None if window_matches(app.app_id, window) else window.wm_class


def remove(app_id: str, uninstall: bool = False) -> None:
    """Effect: stops tracking app_id, undoes its taskbar fix, and uninstalls
    it if asked."""
    app = next((a for a in store.load() if a.app_id == app_id), None)
    if app and app.wm_class:
        desktop_entry.remove(app_id)
    if uninstall and not app_id.startswith("github:") and app_id in host.installed_apps():
        host.uninstall(app_id)
    store.remove(app_id)


def _save_avatar(app: TrackedApp) -> None:
    """Effect: saves the repo owner's GitHub avatar as the app's icon
    (watched repos have no icon of their own). Failures are ignored."""
    try:
        store.ICON_DIR.mkdir(parents=True, exist_ok=True)
        github.download(f"https://github.com/{app.repo.split('/')[0]}.png?size=128", store.icon_path(app.app_id))
    except OSError:
        pass


def _check_not_tracked(repo: str) -> None:
    """Effect: reads the list; raises ValueError if repo is already in it."""
    if is_tracked(store.load(), repo):
        raise ValueError(f"{repo} is already in the list")


def _download(asset: Asset, progress) -> Path:
    """Effect: downloads asset into the cache. The cache is under the real
    home folder, so the host's flatpak can read it even from the sandbox."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / asset.name
    github.download(asset.url, path, progress)
    return path
