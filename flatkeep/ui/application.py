"""The GTK application: one window, reused if Flatkeep is opened again."""

from gi.repository import Adw

from .. import APP_ID
from .window import MainWindow


class FlatkeepApplication(Adw.Application):
    """Flatkeep's GTK application, registered under APP_ID."""

    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        """Show the window, creating it the first time."""
        window = self.get_active_window() or MainWindow(application=self)
        window.present()
