"""Find out which window class an open Flatpak app uses.

Only KDE Plasma lets other programs list windows: we load a tiny KWin script
that sends the window list back to us over D-Bus. Each window's process is
matched to its app through its systemd scope, which Flatpak names
app-flatpak-<app id>-<number>.scope.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from gi.repository import Gio, GLib

from .. import APP_ID
from . import host

KWIN = "org.kde.KWin"
REPLY_NAME = f"{APP_ID}.WindowList"  # Flatpak apps may own names under their ID
REPLY_INTERFACE = "io.github.cerels.Flatkeep.WindowList"
SCRIPT_DIR = Path(GLib.get_user_cache_dir()) / "flatkeep"

_SCRIPT = """
const windows = workspace.windowList ? workspace.windowList() : workspace.clientList();
const out = [];
for (const w of windows) {
  if (w.normalWindow) out.push([w.pid, w.resourceClass, w.desktopFileName].join("\\t"));
}
callDBus("%s", "/", "%s", "Report", out.join("\\n"));
""" % (REPLY_NAME, REPLY_INTERFACE)

_INTROSPECTION = f"""
<node>
  <interface name="{REPLY_INTERFACE}">
    <method name="Report"><arg type="s" name="windows" direction="in"/></method>
  </interface>
</node>
"""


class NotSupported(RuntimeError):
    pass


@dataclass
class WindowInfo:
    """An open window, as KWin sees it.

    Interpretation: pid is its process; wm_class is the class it reports
    (what taskbars match launchers by); desktop_file is the launcher KWin
    linked it to, "" if none.
    """

    pid: int
    wm_class: str
    desktop_file: str


# Examples:
KWIN_REPORT = "18848\tQuiverLauncher.Desktop\t\n10835\torg.kde.konsole\torg.kde.konsole\nnot-a-pid\tx\ty"
QUIVER_CGROUP = (
    "0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
    "app-flatpak-io.github.tgeorgiadis.QuiverLauncher-3930108342.scope\n"
)


def parse_window_list(report: str) -> list[WindowInfo]:
    """The windows in the text our KWin script sends: one "pid, class,
    launcher" line per window, separated by tabs.

    >>> parse_window_list(KWIN_REPORT)
    [WindowInfo(pid=18848, wm_class='QuiverLauncher.Desktop', desktop_file=''), WindowInfo(pid=10835, wm_class='org.kde.konsole', desktop_file='org.kde.konsole')]
    """
    windows = []
    for line in report.splitlines():
        pid, wm_class, desktop_file = (line.split("\t") + ["", ""])[:3]
        if pid.isdigit():
            windows.append(WindowInfo(int(pid), wm_class, desktop_file))
    return windows


def runs_in_app(cgroup: str, app_id: str) -> bool:
    """Does a process with this /proc/<pid>/cgroup belong to the Flatpak app?
    Flatpak runs each app in a systemd scope named after it.

    >>> runs_in_app(QUIVER_CGROUP, "io.github.tgeorgiadis.QuiverLauncher")
    True
    >>> runs_in_app(QUIVER_CGROUP, "io.github.tgeorgiadis.Quiver")
    False
    """
    return f"app-flatpak-{app_id}-" in cgroup


def find_window(app_id: str) -> WindowInfo | None:
    """The first open window of app_id, or None if it has no window open.

    Effect: asks KWin, and reads processes' cgroups on the host.
    """
    for window in list_windows():
        if runs_in_app(host.run("cat", f"/proc/{window.pid}/cgroup", check=False).stdout, app_id):
            return window
    return None


def list_windows(timeout_ms: int = 5000) -> list[WindowInfo]:
    """Every normal window. Safe to call from a worker thread.

    Effect: loads a KWin script and waits for its D-Bus reply.
    """
    # Incoming D-Bus calls are dispatched to the thread-default main context,
    # so give this thread its own and spin it until KWin answers.
    context = GLib.MainContext.new()
    context.push_thread_default()
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    if not _call(bus, "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                 "NameHasOwner", GLib.Variant("(s)", (KWIN,)))[0]:
        context.pop_thread_default()
        raise NotSupported("Detecting only works on KDE Plasma. Type the window class instead.")

    replies = []

    def on_call(_conn, _sender, _path, _iface, _method, params, invocation):
        replies.append(params.unpack()[0])
        invocation.return_value(None)

    node = Gio.DBusNodeInfo.new_for_xml(_INTROSPECTION)
    registration = bus.register_object("/", node.interfaces[0], on_call, None, None)
    plugin = f"flatkeep-windows-{os.getpid()}"
    script = SCRIPT_DIR / f"{plugin}.js"
    timed_out = []
    timer = GLib.timeout_source_new(timeout_ms)
    timer.set_callback(lambda *_: timed_out.append(True) or GLib.SOURCE_REMOVE)
    try:
        _call(bus, "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
              "RequestName", GLib.Variant("(su)", (REPLY_NAME, 4)))  # 4 = don't queue
        SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        script.write_text(_SCRIPT)
        _call(bus, KWIN, "/Scripting", "org.kde.kwin.Scripting", "unloadScript", GLib.Variant("(s)", (plugin,)))
        _call(bus, KWIN, "/Scripting", "org.kde.kwin.Scripting", "loadScript",
              GLib.Variant("(ss)", (str(script), plugin)))
        _call(bus, KWIN, "/Scripting", "org.kde.kwin.Scripting", "start", None)

        timer.attach(context)
        while not replies and not timed_out:
            context.iteration(True)
    finally:
        timer.destroy()
        _call(bus, KWIN, "/Scripting", "org.kde.kwin.Scripting", "unloadScript", GLib.Variant("(s)", (plugin,)))
        _call(bus, "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
              "ReleaseName", GLib.Variant("(s)", (REPLY_NAME,)))
        bus.unregister_object(registration)
        script.unlink(missing_ok=True)
        context.pop_thread_default()

    if not replies:
        raise TimeoutError("KWin didn't answer")
    return parse_window_list(replies[0])


def _call(bus, name, path, interface, method, args):
    result = bus.call_sync(name, path, interface, method, args, None, Gio.DBusCallFlags.NONE, 5000, None)
    return result.unpack() if result else ()
