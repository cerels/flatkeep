"""The Edit dialog: one tracked app's settings."""

from gi.repository import Adw, Gtk

from ..core import describe, store, updater
from .tasks import run_async


class EditDialog(Adw.PreferencesDialog):
    """Settings for one tracked app. Switches save right away; text fields
    save when you press Enter or the ✓ button. A change GitHub can't find a
    release for isn't saved, and the row goes back to the saved value.

    self.rows maps each setting (a TrackedApp field name) to its row.
    """

    def __init__(self, app: store.TrackedApp, on_changed):
        super().__init__(title=f"Edit {app.name or app.repo}", search_enabled=False)
        self.app = app
        self.on_changed = on_changed
        self.rows = {}
        self._reverting = False

        page = Adw.PreferencesPage()
        self.add(page)

        general = Adw.PreferencesGroup()
        general.add(self._text_row("Name", "name"))
        general.add(self._text_row("GitHub Repository", "repo"))
        page.add(general)

        updates = Adw.PreferencesGroup(title="Updates")
        updates.add(self._switch_row(
            "Include Pre-releases", "Also use releases marked as alpha, beta, nightly and so on",
            "include_prereleases",
        ))
        page.add(updates)

        if not app.is_watch:
            updates.add(self._switch_row(
                "Install Updates Automatically", "When off, background checks only send a notification",
                "auto_update",
            ))

            taskbar = Adw.PreferencesGroup(
                title="Taskbar",
                description="If the open app shows up as a separate taskbar icon instead of on its "
                "pinned one, open the app and click Detect.",
            )
            wm_class_row = self._text_row("Window Class", "wm_class")
            detect = Gtk.Button(label="Detect", valign=Gtk.Align.CENTER, tooltip_text="Needs KDE Plasma")
            detect.connect("clicked", lambda button: self._detect(button))
            wm_class_row.add_suffix(detect)
            taskbar.add(wm_class_row)
            page.add(taskbar)

            advanced = Adw.PreferencesGroup(
                title="Advanced",
                description="Only needed when a release has several .flatpak files. "
                "Flatkeep uses the first file whose name matches this pattern (a regular expression).",
            )
            advanced.add(self._text_row("File Name Filter", "asset_pattern"))
            page.add(advanced)

        about = Adw.PreferencesGroup()
        about.add(Adw.ActionRow(
            title="Notifications only" if app.is_watch else "App ID",
            subtitle="Flatkeep doesn't install anything from this repository" if app.is_watch else app.app_id,
            subtitle_selectable=not app.is_watch,
            css_classes=["property"],
        ))
        page.add(about)

    def _text_row(self, title: str, field: str) -> Adw.EntryRow:
        """A text row for field that saves on Enter or ✓."""
        row = Adw.EntryRow(title=title, text=describe.field_text(self.app, field), show_apply_button=True)
        row.connect("apply", lambda row: self._save(field, row.get_text()))
        self.rows[field] = row
        return row

    def _switch_row(self, title: str, subtitle: str, field: str) -> Adw.SwitchRow:
        """A switch row for field that saves when flipped."""
        row = Adw.SwitchRow(title=title, subtitle=subtitle, active=describe.field_text(self.app, field))
        row.connect("notify::active", lambda row, _param: self._save(field, row.get_active()))
        self.rows[field] = row
        return row

    def _save(self, field: str, value) -> None:
        """Save one setting, or put the row back if that fails.

        Effect: see updater.edit.
        """
        if self._reverting:
            return
        row = self.rows[field]
        row.set_sensitive(False)

        def done(_app, error):
            row.set_sensitive(True)
            if error:
                self.add_toast(Adw.Toast(title=f"Not saved: {error}", use_markup=False))
                self._show_saved(field)
                return
            self.set_title(f"Edit {self.app.name or self.app.repo}")
            self.on_changed()

        run_async(lambda: updater.edit(self.app, **{field: value}), done)

    def _show_saved(self, field: str) -> None:
        """Make field's row show the saved value, without saving it again."""
        self._reverting = True
        row, value = self.rows[field], describe.field_text(self.app, field)
        if isinstance(row, Adw.SwitchRow):
            row.set_active(value)
        else:
            row.set_text(value)
        self._reverting = False

    def _detect(self, button) -> None:
        """Find the open window's class and apply the taskbar fix with it.

        Effect: asks KWin, then saves (see updater.detect_wm_class).
        """
        button.set_sensitive(False)

        def done(wm_class, error):
            button.set_sensitive(True)
            if error:
                self.add_toast(Adw.Toast(title=str(error), use_markup=False))
            elif wm_class is None:
                self.add_toast(Adw.Toast(title="The window already matches its launcher, nothing to fix"))
            else:
                self.rows["wm_class"].set_text(wm_class)
                self._save("wm_class", wm_class)
                self.add_toast(Adw.Toast(
                    title=f"Fixed. Close and reopen {self.app.name or 'the app'} to see it", use_markup=False
                ))

        run_async(lambda: updater.detect_wm_class(self.app), done)
