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

# A Launcher is the text of a .desktop file.
# Examples:
EXPORTED = """[Desktop Entry]
Name=Quiver Launcher
Exec=flatpak run io.github.tgeorgiadis.QuiverLauncher

[Desktop Action new]
Name=New Window
"""
USERS_OWN = """[Desktop Entry]
Name=Nuvio Desktop
StartupWMClass=Nuvio Desktop
"""


def set_key(launcher: str, key: str, value: str) -> str:
    """The launcher with key=value in its [Desktop Entry] group, replacing
    any old value. Other groups are left alone.

    >>> print(set_key(EXPORTED, "StartupWMClass", "QuiverLauncher.Desktop"), end="")
    [Desktop Entry]
    Name=Quiver Launcher
    Exec=flatpak run io.github.tgeorgiadis.QuiverLauncher
    <BLANKLINE>
    StartupWMClass=QuiverLauncher.Desktop
    [Desktop Action new]
    Name=New Window
    >>> print(set_key(USERS_OWN, "StartupWMClass", "com-nuvio-app-MainKt"), end="")
    [Desktop Entry]
    Name=Nuvio Desktop
    StartupWMClass=com-nuvio-app-MainKt
    """
    out = []
    in_main = done = False
    for line in launcher.splitlines():
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


def is_flatkeeps(launcher: str) -> bool:
    """Did Flatkeep create this launcher (so it may rewrite or delete it)?

    >>> is_flatkeeps(USERS_OWN)
    False
    >>> is_flatkeeps(set_key(EXPORTED, MARKER_KEY, "true"))
    True
    """
    return f"{MARKER_KEY}=true" in launcher


def fixed_launcher(existing: str | None, exported: str | None, wm_class: str) -> str:
    """The launcher to write so windows of class wm_class join it.

    - Someone else's launcher (existing, not Flatkeep's): only its
      StartupWMClass line changes.
    - Otherwise: a copy of the app's exported launcher, marked as Flatkeep's,
      so it's rebuilt from the newest version after updates.

    >>> fixed_launcher(USERS_OWN, EXPORTED, "com-nuvio-app-MainKt") == set_key(USERS_OWN, "StartupWMClass", "com-nuvio-app-MainKt")
    True
    >>> text = fixed_launcher(None, EXPORTED, "QuiverLauncher.Desktop")
    >>> is_flatkeeps(text), "StartupWMClass=QuiverLauncher.Desktop" in text
    (True, True)
    >>> fixed_launcher(None, None, "X")
    Traceback (most recent call last):
    FileNotFoundError: Couldn't find the app's launcher
    """
    if existing is not None and not is_flatkeeps(existing):
        return set_key(existing, "StartupWMClass", wm_class)
    if exported is None:
        raise FileNotFoundError("Couldn't find the app's launcher")
    return set_key(set_key(exported, "StartupWMClass", wm_class), MARKER_KEY, "true")


def apply(app_id: str, wm_class: str) -> None:
    """Effect: writes ~/.local/share/applications/<app_id>.desktop so windows
    of class wm_class join the app's taskbar icon."""
    target = APPS_DIR / f"{app_id}.desktop"
    exported = next((text for d in EXPORT_DIRS if (text := _read(d / f"{app_id}.desktop")) is not None), None)
    content = fixed_launcher(_read(target), exported, wm_class)
    host.run("mkdir", "-p", str(APPS_DIR))
    host.run("tee", str(target), input=content)
    _refresh()


def remove(app_id: str) -> None:
    """Effect: deletes Flatkeep's launcher copy for app_id. Launchers that
    Flatkeep didn't create are left alone."""
    target = APPS_DIR / f"{app_id}.desktop"
    existing = _read(target)
    if existing is not None and is_flatkeeps(existing):
        host.run("rm", "-f", str(target))
        _refresh()


def _read(path: Path) -> str | None:
    """Effect: reads a host file; None if it doesn't exist."""
    result = host.run("cat", str(path), check=False)
    return result.stdout if result.returncode == 0 else None


def _refresh() -> None:
    """Effect: tells the desktop the launchers changed. KDE caches them in sycoca."""
    for command in (("update-desktop-database", str(APPS_DIR)), ("kbuildsycoca6",)):
        try:
            host.run(*command, check=False)
        except OSError:
            pass  # not installed on this desktop
