"""Remember which apps Flatkeep tracks, in ~/.config/flatkeep/apps.json."""

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from gi.repository import GLib

CONFIG_FILE = Path(GLib.get_user_config_dir()) / "flatkeep" / "apps.json"
ICON_DIR = Path(GLib.get_user_data_dir()) / "flatkeep" / "icons"


FLATPAK = "flatpak"  # Flatkeep installs and updates the app
WATCH = "watch"  # Flatkeep only tells you about new releases


@dataclass
class TrackedApp:
    app_id: str  # flatpak app ID, or "github:owner/name" for watched repos
    repo: str  # "owner/name" on GitHub
    name: str = ""
    kind: str = FLATPAK
    installed_tag: str = ""  # release tag Flatkeep last installed
    asset_pattern: str = ""  # optional regex to pick between several .flatpak files
    auto_update: bool = True  # install updates in the background, or only notify
    include_prereleases: bool = False
    wm_class: str = ""  # window class for the taskbar fix, see desktop_entry.py
    seen_tag: str = ""  # watched repos: latest release the user has looked at
    notified_tag: str = ""  # last release we sent a notification about

    @property
    def is_watch(self) -> bool:
        return self.kind == WATCH


def load() -> list[TrackedApp]:
    try:
        data = json.loads(CONFIG_FILE.read_text())
    except FileNotFoundError:
        return []
    known = {f.name for f in fields(TrackedApp)}
    return [TrackedApp(**{k: v for k, v in item.items() if k in known}) for item in data]


def save(apps: list[TrackedApp]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(app) for app in apps], indent=2))
    tmp.replace(CONFIG_FILE)


def put(app: TrackedApp) -> None:
    """Add app, or replace the entry with the same app ID in its place."""
    apps = load()
    for i, existing in enumerate(apps):
        if existing.app_id == app.app_id:
            apps[i] = app
            break
    else:
        apps.append(app)
    save(apps)


def remove(app_id: str) -> None:
    save([a for a in load() if a.app_id != app_id])
    icon_path(app_id).unlink(missing_ok=True)


def icon_path(app_id: str) -> Path:
    # Watched repos have IDs like "github:owner/name"; keep them one file.
    return ICON_DIR / f"{app_id.replace('/', '_')}.png"


def save_icon(app_id: str, png: bytes) -> None:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    icon_path(app_id).write_bytes(png)
