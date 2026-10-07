"""Desktop notifications that work without the window being open.

GApplication notifications need a running app, but background checks run as a
short command from a systemd timer, so we talk to the notification server
directly. Inside the Flatpak this needs --talk-name=org.freedesktop.Notifications.
"""

import sys

from gi.repository import Gio, GLib

from .. import APP_ID


def send(title: str, body: str = "", icon: str = "") -> None:
    hints = {"desktop-entry": GLib.Variant("s", APP_ID)}
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        bus.call_sync(
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications",
            "Notify",
            GLib.Variant("(susssasa{sv}i)", ("Flatkeep", 0, icon or APP_ID, title, body, [], hints, -1)),
            None, Gio.DBusCallFlags.NONE, -1, None,
        )
    except GLib.Error as e:
        print(f"Couldn't show notification: {e.message}", file=sys.stderr)
