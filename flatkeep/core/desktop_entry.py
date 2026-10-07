"""Make an app's open window join its pinned taskbar icon.

Desktops match a window to its launcher by the window class. When an app
reports a class that doesn't match its app ID (common with Java, .NET and
Electron apps), the open window shows up as a separate taskbar icon.

The fix is a launcher in ~/.local/share/applications with StartupWMClass
set. That folder takes priority over Flatpak's exported launchers and isn't
touched when the app updates. Files are read and written through host.run
because the sandbox can't see that folder.
"""

from pathlib import Path

from . import host

APPS_DIR = Path.home() / ".local" / "share" / "applications"
EXPORT_DIRS = (
    Path.home() / ".local" / "share" / "flatpak" / "exports" / "share" / "applications",
    Path("/var/lib/flatpak/exports/share/applications"),
)
MARKER_KEY = "X-Flatkeep-Generated"


def apply(app_id: str, wm_class: str) -> None:
    target = APPS_DIR / f"{app_id}.desktop"
    existing = _read(target)
    if existing is not None and f"{MARKER_KEY}=true" not in existing:
        # A launcher someone else made: only change the window class line.
        content = _set_key(existing, "StartupWMClass", wm_class)
    else:
        exported = next((text for d in EXPORT_DIRS if (text := _read(d / f"{app_id}.desktop")) is not None), None)
        if exported is None:
            raise FileNotFoundError(f"Couldn't find the launcher of {app_id}")
        content = _set_key(_set_key(exported, "StartupWMClass", wm_class), MARKER_KEY, "true")

    host.run("mkdir", "-p", str(APPS_DIR))
    host.run("tee", str(target), input=content)
    _refresh()


def remove(app_id: str) -> None:
    """Delete our launcher copy. Launchers we didn't create are left alone."""
    target = APPS_DIR / f"{app_id}.desktop"
    existing = _read(target)
    if existing is not None and f"{MARKER_KEY}=true" in existing:
        host.run("rm", "-f", str(target))
        _refresh()


def _read(path: Path) -> str | None:
    result = host.run("cat", str(path), check=False)
    return result.stdout if result.returncode == 0 else None


def _set_key(text: str, key: str, value: str) -> str:
    """Set key=value in the [Desktop Entry] group, replacing any old value."""
    out = []
    in_main = done = False
    for line in text.splitlines():
        if line.startswith("["):
            if in_main and not done:
                out.append(f"{key}={value}")
                done = True
            in_main = line.strip() == "[Desktop Entry]"
        elif in_main and line.split("=", 1)[0].strip() == key:
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue
        out.append(line)
    if in_main and not done:
        out.append(f"{key}={value}")
    return "\n".join(out) + "\n"


def _refresh() -> None:
    """Tell the desktop the launchers changed. KDE caches them in sycoca."""
    for command in (("update-desktop-database", str(APPS_DIR)), ("kbuildsycoca6",)):
        try:
            host.run(*command, check=False)
        except OSError:
            pass  # not installed on this desktop
