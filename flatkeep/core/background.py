"""Background update checks: a systemd user timer that runs `flatkeep check-updates`.

The unit files go in the host's ~/.config/systemd/user and are written
through host.run, so this works the same inside and outside the sandbox.
"""

import sys
from pathlib import Path

from .. import APP_ID
from . import host, notify, store, updater

UNIT = "flatkeep-update"
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
INTERVAL = "6h"


def _command() -> str:
    if host.IN_SANDBOX:
        return f"flatpak run --command=flatkeep {APP_ID} check-updates"
    # Running from a source checkout while developing.
    launcher = Path(__file__).resolve().parents[2] / "bin" / "flatkeep"
    return f"{sys.executable} {launcher} check-updates"


def _service() -> str:
    return f"""[Unit]
Description=Check Flatkeep apps for new releases
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart={_command()}
"""


def _timer() -> str:
    return f"""[Unit]
Description=Check Flatkeep apps for new releases every {INTERVAL}

[Timer]
OnBootSec=5min
OnUnitActiveSec={INTERVAL}
Persistent=true

[Install]
WantedBy=timers.target
"""


def is_enabled() -> bool:
    return host.run("systemctl", "--user", "is-enabled", f"{UNIT}.timer", check=False).returncode == 0


def enable() -> None:
    host.run("mkdir", "-p", str(UNIT_DIR))
    host.run("tee", str(UNIT_DIR / f"{UNIT}.service"), input=_service())
    host.run("tee", str(UNIT_DIR / f"{UNIT}.timer"), input=_timer())
    host.run("systemctl", "--user", "daemon-reload")
    host.run("systemctl", "--user", "enable", "--now", f"{UNIT}.timer")


def disable() -> None:
    host.run("systemctl", "--user", "disable", "--now", f"{UNIT}.timer", check=False)
    host.run("rm", "-f", str(UNIT_DIR / f"{UNIT}.service"), str(UNIT_DIR / f"{UNIT}.timer"))
    host.run("systemctl", "--user", "daemon-reload")


def check_updates() -> None:
    """What the timer runs: install or announce new releases for every app."""
    installed = host.installed_apps()
    for app in store.load():
        status = updater.check(app, installed)
        if status.error:
            print(f"{app.app_id}: {status.error}", file=sys.stderr)
            continue
        if not status.update_available:
            continue

        name = app.name or app.app_id
        tag = status.release.tag
        icon = str(store.icon_path(app.app_id)) if store.icon_path(app.app_id).exists() else ""

        if app.is_watch or not app.auto_update or status.installed is None:
            # Only announce each release once.
            if app.notified_tag != tag:
                verb = "released" if app.is_watch else "is available"
                notify.send(f"{name} {tag} {verb}", f"https://github.com/{app.repo}/releases", icon)
                app.notified_tag = tag
                store.put(app)
            print(f"{app.app_id}: {tag} available")
            continue

        old = status.current
        try:
            updater.update(app)
        except Exception as e:
            print(f"{app.app_id}: update failed: {e}", file=sys.stderr)
            notify.send(f"Couldn't update {name}", str(e), icon)
            continue
        print(f"{app.app_id}: updated {old} -> {tag}")
        notify.send(f"{name} updated", f"{old} → {tag}", icon)
