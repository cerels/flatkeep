from gi.repository import Adw, Gtk

from ..core import store, updater
from .tasks import run_async


class EditDialog(Adw.PreferencesDialog):
    """Settings for one tracked app. Switches save right away; text fields
    save when you press Enter or the ✓ button, after checking that GitHub
    still finds a release with the new value."""

    def __init__(self, app: store.TrackedApp, on_changed):
        super().__init__(title=f"Edit {app.name or app.repo}", search_enabled=False)
        self.app = app
        self.on_changed = on_changed
        self._reverting = False

        page = Adw.PreferencesPage()
        self.add(page)

        general = Adw.PreferencesGroup()
        page.add(general)
        self.name_row = Adw.EntryRow(title="Name", text=app.name, show_apply_button=True)
        self.name_row.connect("apply", lambda row: self._save(row, name=row.get_text()))
        general.add(self.name_row)
        self.repo_row = Adw.EntryRow(
            title="GitHub Repository", text=f"https://github.com/{app.repo}", show_apply_button=True
        )
        self.repo_row.connect("apply", lambda row: self._save(row, repo_text=row.get_text()))
        general.add(self.repo_row)

        updates = Adw.PreferencesGroup(title="Updates")
        page.add(updates)
        self.prerelease_row = Adw.SwitchRow(
            title="Include Pre-releases",
            subtitle="Also use releases marked as alpha, beta, nightly and so on",
            active=app.include_prereleases,
        )
        self.prerelease_row.connect(
            "notify::active", lambda row, _p: self._save(row, include_prereleases=row.get_active())
        )
        updates.add(self.prerelease_row)

        if not app.is_watch:
            auto_row = Adw.SwitchRow(
                title="Install Updates Automatically",
                subtitle="When off, background checks only send a notification",
                active=app.auto_update,
            )
            auto_row.connect("notify::active", lambda row, _p: self._save(row, auto_update=row.get_active()))
            updates.add(auto_row)

            taskbar = Adw.PreferencesGroup(
                title="Taskbar",
                description="If the open app shows up as a separate taskbar icon instead of on its "
                "pinned one, open the app and click Detect.",
            )
            page.add(taskbar)
            self.wm_class_row = Adw.EntryRow(title="Window Class", text=app.wm_class, show_apply_button=True)
            self.wm_class_row.connect("apply", lambda row: self._save(row, wm_class=row.get_text()))
            detect = Gtk.Button(label="Detect", valign=Gtk.Align.CENTER, tooltip_text="Needs KDE Plasma")
            detect.connect("clicked", lambda button: self._detect(button))
            self.wm_class_row.add_suffix(detect)
            taskbar.add(self.wm_class_row)

            filter_group = Adw.PreferencesGroup(
                title="Advanced",
                description="Only needed when a release has several .flatpak files. "
                "Flatkeep uses the first file whose name matches this pattern (a regular expression).",
            )
            page.add(filter_group)
            filter_row = Adw.EntryRow(title="File Name Filter", text=app.asset_pattern, show_apply_button=True)
            filter_row.connect("apply", lambda row: self._save(row, asset_pattern=row.get_text()))
            filter_group.add(filter_row)

        about = Adw.PreferencesGroup()
        page.add(about)
        about.add(Adw.ActionRow(
            title="Notifications only" if app.is_watch else "App ID",
            subtitle="Flatkeep doesn't install anything from this repository" if app.is_watch else app.app_id,
            subtitle_selectable=not app.is_watch,
            css_classes=["property"],
        ))

    def _save(self, row, **changes) -> None:
        if self._reverting:
            return
        row.set_sensitive(False)

        def done(_app, error):
            row.set_sensitive(True)
            if error:
                self.add_toast(Adw.Toast(title=f"Not saved: {error}", use_markup=False))
                self._revert(row)
                return
            self.set_title(f"Edit {self.app.name or self.app.repo}")
            self.on_changed()

        run_async(lambda: updater.edit(self.app, **changes), done)

    def _detect(self, button) -> None:
        button.set_sensitive(False)

        def done(wm_class, error):
            button.set_sensitive(True)
            if error:
                self.add_toast(Adw.Toast(title=str(error), use_markup=False))
            elif wm_class is None:
                self.add_toast(Adw.Toast(title="The window already matches its launcher, nothing to fix"))
            else:
                self.wm_class_row.set_text(wm_class)
                self._save(self.wm_class_row, wm_class=wm_class)
                self.add_toast(Adw.Toast(
                    title=f"Fixed. Close and reopen {self.app.name or 'the app'} to see it", use_markup=False
                ))

        run_async(lambda: updater.detect_wm_class(self.app), done)

    def _revert(self, row) -> None:
        """Put the saved value back so the dialog shows what's actually stored."""
        self._reverting = True
        if row is self.prerelease_row:
            row.set_active(self.app.include_prereleases)
        elif row is self.repo_row:
            row.set_text(f"https://github.com/{self.app.repo}")
        elif row is self.name_row:
            row.set_text(self.app.name)
        elif row is getattr(self, "wm_class_row", None):
            row.set_text(self.app.wm_class)
        elif isinstance(row, Adw.SwitchRow):
            row.set_active(self.app.auto_update)
        elif isinstance(row, Adw.EntryRow):
            row.set_text(self.app.asset_pattern)
        self._reverting = False
