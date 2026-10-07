from gi.repository import Adw

from .. import APP_ID
from .window import MainWindow


class FlatkeepApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        window = self.get_active_window() or MainWindow(application=self)
        window.present()
