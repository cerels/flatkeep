"""Remember which apps Flatkeep tracks, in ~/.config/flatkeep/apps.json."""

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from gi.repository import GLib

CONFIG_FILE = Path(GLib.get_user_config_dir()) / "flatkeep" / "apps.json"
ICON_DIR = Path(GLib.get_user_data_dir()) / "flatkeep" / "icons"


# A Kind is one of:
FLATPAK = "flatpak"  # Flatkeep installs and updates the app
WATCH = "watch"  # Flatkeep only tells you about new releases


@dataclass
class TrackedApp:
    """An app or repository Flatkeep keeps an eye on.

    Interpretation:
    - app_id: the Flatpak app ID, or "github:owner/name" for a watched repo
    - repo: the GitHub repository, as "owner/name"
    - name: what the list shows; empty means "use the app ID"
    - kind: FLATPAK or WATCH
    - installed_tag: release tag Flatkeep last installed ("" if unknown)
    - installed_published: when that release was published ("" if unknown)
    - asset_pattern: regex choosing between several .flatpak files ("" = any)
    - auto_update: background checks install updates (True) or only notify
    - include_prereleases: also consider releases marked as pre-release
    - wm_class: window class for the taskbar fix ("" = no fix)
    - seen_tag: watched repos only, the release the user last looked at
    - seen_published: when that release was published ("" if unknown)
    - notified_tag: the last release we sent a notification about
    """

    app_id: str
    repo: str
    name: str = ""
    kind: str = FLATPAK
    installed_tag: str = ""
    installed_published: str = ""
    asset_pattern: str = ""
    auto_update: bool = True
    include_prereleases: bool = False
    wm_class: str = ""
    seen_tag: str = ""
    seen_published: str = ""
    notified_tag: str = ""

    @property
    def is_watch(self) -> bool:
        """Is this a watched repository rather than an installed app?

        >>> NUVIO.is_watch, FLATPAK_REPO.is_watch
        (False, True)
        """
        return self.kind == WATCH


# Examples:
NUVIO = TrackedApp(
    app_id="com.nuvio.media.desktop", repo="NuvioMedia/NuvioDesktop", name="Nuvio",
    installed_tag="0.1.27-alpha", wm_class="com-nuvio-app-MainKt",
)
FLATPAK_REPO = TrackedApp(
    app_id="github:flatpak/flatpak", repo="flatpak/flatpak", name="flatpak", kind=WATCH,
    seen_tag="1.18.4", notified_tag="1.18.4",
)


def from_dict(data: dict) -> TrackedApp:
    """Make a TrackedApp from one saved JSON entry.

    Keys from newer or older versions of Flatkeep are ignored, and missing
    keys get their defaults, so old apps.json files keep working.

    >>> from_dict({"app_id": "a.b.C", "repo": "o/r", "no_longer_used": 1})
    TrackedApp(app_id='a.b.C', repo='o/r', name='', kind='flatpak', installed_tag='', installed_published='', asset_pattern='', auto_update=True, include_prereleases=False, wm_class='', seen_tag='', seen_published='', notified_tag='')
    """
    known = {f.name for f in fields(TrackedApp)}
    return TrackedApp(**{key: value for key, value in data.items() if key in known})


def with_app(apps: list[TrackedApp], app: TrackedApp) -> list[TrackedApp]:
    """The list with app replacing the entry that has its app ID, in place,
    or added at the end if it's new.

    >>> renamed = TrackedApp(app_id=NUVIO.app_id, repo=NUVIO.repo, name="Nuvio Beta")
    >>> [a.name for a in with_app([NUVIO, FLATPAK_REPO], renamed)]
    ['Nuvio Beta', 'flatpak']
    >>> [a.name for a in with_app([FLATPAK_REPO], NUVIO)]
    ['flatpak', 'Nuvio']
    """
    if any(existing.app_id == app.app_id for existing in apps):
        return [app if existing.app_id == app.app_id else existing for existing in apps]
    return [*apps, app]


def without_app(apps: list[TrackedApp], app_id: str) -> list[TrackedApp]:
    """The list without the entry for app_id.

    >>> [a.name for a in without_app([NUVIO, FLATPAK_REPO], "com.nuvio.media.desktop")]
    ['flatpak']
    """
    return [app for app in apps if app.app_id != app_id]


def icon_file_name(app_id: str) -> str:
    """The file name for an app's icon. Watched repos' IDs contain a "/",
    which would otherwise make a subfolder.

    >>> icon_file_name("com.nuvio.media.desktop")
    'com.nuvio.media.desktop.png'
    >>> icon_file_name("github:flatpak/flatpak")
    'github:flatpak_flatpak.png'
    """
    return f"{app_id.replace('/', '_')}.png"


def icon_path(app_id: str) -> Path:
    return ICON_DIR / icon_file_name(app_id)


def load() -> list[TrackedApp]:
    """The tracked apps.

    Effect: reads CONFIG_FILE.
    """
    try:
        data = json.loads(CONFIG_FILE.read_text())
    except FileNotFoundError:
        return []
    return [from_dict(item) for item in data]


def save(apps: list[TrackedApp]) -> None:
    """Effect: replaces CONFIG_FILE with apps, all at once so a crash can't
    leave a half-written file."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(app) for app in apps], indent=2))
    tmp.replace(CONFIG_FILE)


def put(app: TrackedApp) -> None:
    """Effect: saves app, replacing the entry with the same app ID."""
    save(with_app(load(), app))


def remove(app_id: str) -> None:
    """Effect: forgets app_id and deletes its icon."""
    save(without_app(load(), app_id))
    icon_path(app_id).unlink(missing_ok=True)


def save_icon(app_id: str, png: bytes) -> None:
    """Effect: stores png as the icon of app_id."""
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    icon_path(app_id).write_bytes(png)
