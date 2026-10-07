"""The GTK interface. Importing it selects the GTK 4 and libadwaita 1 APIs."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
