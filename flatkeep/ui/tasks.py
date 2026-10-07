import threading

from gi.repository import GLib


def run_async(func, callback):
    """Run func() in a thread, then callback(result, error) on the main thread.

    Network and flatpak calls are slow; running them here keeps the window
    responsive. Widgets may only be touched from the main thread.
    """

    def worker():
        try:
            result, error = func(), None
        except Exception as e:
            result, error = None, e

        def finish():
            callback(result, error)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(finish)

    threading.Thread(target=worker, daemon=True).start()
