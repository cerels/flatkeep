"""Background update checks: a systemd user timer that runs `flatkeep check-updates`.

The unit files go in the host's ~/.config/systemd/user and are written
through host.run, so this works the same inside and outside the sandbox.
"""

import dataclasses
import sys
from pathlib import Path

from .. import APP_ID
from . import host, notify, store, updater
from .updater import Status

# Examples from the other modules, used in this module's examples.
from .github import NO_FLATPAK_RELEASE, NUVIO_RELEASE  # noqa: E402
from .store import FLATPAK_REPO, NUVIO  # noqa: E402

UNIT = "flatkeep-update"
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
INTERVAL = "6h"

# An Action is one of:
INSTALL = "install"  # install the update, then say so
NOTIFY = "notify"  # tell the user a release is out
NOTHING = "nothing"  # nothing new, or the user was already told

# Examples:
_NEWER_NUVIO = dataclasses.replace(NUVIO_RELEASE, tag="0.1.28-alpha")
NUVIO_UPDATE = Status(NUVIO, installed="0.1.27-alpha", release=_NEWER_NUVIO, asset=NUVIO_RELEASE.assets[1])
NEW_FLATPAK_RELEASE = Status(FLATPAK_REPO, installed=None, release=dataclasses.replace(NO_FLATPAK_RELEASE, tag="1.19.0"))


def decide_action(status: Status) -> str:
    """What a background check does about one app.

    Updates install by themselves only for installed apps with "Install
    Updates Automatically" on. Everything else gets one notification per
    release.

    >>> decide_action(NUVIO_UPDATE)
    'install'
    >>> decide_action(dataclasses.replace(NUVIO_UPDATE, app=dataclasses.replace(NUVIO, auto_update=False)))
    'notify'
    >>> decide_action(dataclasses.replace(NUVIO_UPDATE, installed=None))  # uninstalled: don't reinstall
    'notify'
    >>> decide_action(NEW_FLATPAK_RELEASE)
    'notify'
    >>> told = dataclasses.replace(FLATPAK_REPO, notified_tag="1.19.0")
    >>> decide_action(dataclasses.replace(NEW_FLATPAK_RELEASE, app=told))  # already told
    'nothing'
    >>> decide_action(dataclasses.replace(NUVIO_UPDATE, release=NUVIO_RELEASE))  # up to date
    'nothing'
    """
    if not status.update_available:
        return NOTHING
    app = status.app
    if not app.is_watch and app.auto_update and status.installed is not None:
        return INSTALL
    if app.notified_tag == status.release.tag:
        return NOTHING
    return NOTIFY


def announcement(status: Status) -> tuple[str, str]:
    """The title and body of the notification about status's new release.

    >>> announcement(NEW_FLATPAK_RELEASE)
    ('flatpak 1.19.0 released', 'https://github.com/flatpak/flatpak/releases')
    >>> announcement(NUVIO_UPDATE)
    ('Nuvio 0.1.28-alpha is available', 'https://github.com/NuvioMedia/NuvioDesktop/releases')
    """
    app = status.app
    verb = "released" if app.is_watch else "is available"
    return f"{app.name or app.app_id} {status.release.tag} {verb}", f"https://github.com/{app.repo}/releases"


def service_unit(command: str) -> str:
    """The systemd service that runs command once.

    >>> print(service_unit("flatkeep check-updates"), end="")
    [Unit]
    Description=Check Flatkeep apps for new releases
    Wants=network-online.target
    After=network-online.target
    <BLANKLINE>
    [Service]
    Type=oneshot
    ExecStart=flatkeep check-updates
    """
    return f"""[Unit]
Description=Check Flatkeep apps for new releases
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart={command}
"""


def timer_unit(interval: str) -> str:
    """The systemd timer that starts the service 5 minutes after boot, then
    every interval. Persistent catches up on checks missed while off.

    >>> "OnUnitActiveSec=6h" in timer_unit("6h")
    True
    """
    return f"""[Unit]
Description=Check Flatkeep apps for new releases every {interval}

[Timer]
OnBootSec=5min
OnUnitActiveSec={interval}
Persistent=true

[Install]
WantedBy=timers.target
"""


def check_command() -> str:
    """The command the timer runs: this copy of Flatkeep, installed or not."""
    if host.IN_SANDBOX:
        return f"flatpak run --command=flatkeep {APP_ID} check-updates"
    # Running from a source checkout while developing.
    launcher = Path(__file__).resolve().parents[2] / "bin" / "flatkeep"
    return f"{sys.executable} {launcher} check-updates"


def is_enabled() -> bool:
    """Effect: asks systemd on the host."""
    return host.run("systemctl", "--user", "is-enabled", f"{UNIT}.timer", check=False).returncode == 0


def enable() -> None:
    """Effect: writes the unit files on the host and starts the timer."""
    host.run("mkdir", "-p", str(UNIT_DIR))
    host.run("tee", str(UNIT_DIR / f"{UNIT}.service"), input=service_unit(check_command()))
    host.run("tee", str(UNIT_DIR / f"{UNIT}.timer"), input=timer_unit(INTERVAL))
    host.run("systemctl", "--user", "daemon-reload")
    host.run("systemctl", "--user", "enable", "--now", f"{UNIT}.timer")


def disable() -> None:
    """Effect: stops the timer and deletes the unit files."""
    host.run("systemctl", "--user", "disable", "--now", f"{UNIT}.timer", check=False)
    host.run("rm", "-f", str(UNIT_DIR / f"{UNIT}.service"), str(UNIT_DIR / f"{UNIT}.timer"))
    host.run("systemctl", "--user", "daemon-reload")


def check_updates() -> None:
    """What the timer runs: carry out decide_action for every app.

    Effect: asks GitHub and flatpak, installs updates, sends notifications,
    and logs one line per app with news (or a problem) to the journal.
    """
    installed = host.installed_apps()
    for app in store.load():
        status = updater.check(app, installed)
        if status.error:
            print(f"{app.app_id}: {status.error}", file=sys.stderr)
            continue

        icon_file = store.icon_path(app.app_id)
        icon = str(icon_file) if icon_file.exists() else ""
        action = decide_action(status)

        if action == INSTALL:
            old, name = status.current, app.name or app.app_id
            try:
                updater.update(app)
            except Exception as e:
                print(f"{app.app_id}: update failed: {e}", file=sys.stderr)
                notify.send(f"Couldn't update {name}", str(e), icon)
                continue
            print(f"{app.app_id}: updated {old} -> {status.release.tag}")
            notify.send(f"{name} updated", f"{old} → {status.release.tag}", icon)
            continue

        if action == NOTIFY:
            notify.send(*announcement(status), icon)
            app.notified_tag = status.release.tag
            store.put(app)
        if status.update_available:
            print(f"{app.app_id}: {status.release.tag} available")
