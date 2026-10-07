from gi.repository import Adw, Gio, GLib, Gtk

from .. import APP_ID, VERSION
from ..core import background, host, store, updater
from .edit_dialog import EditDialog
from .tasks import run_async


def progress_text(fraction: float) -> str:
    return "Installing…" if fraction >= 1 else f"Downloading… {fraction:.0%}"


def tag_text(release) -> str:
    return f"{release.tag} (pre-release)" if release.prerelease else release.tag


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(title="Flatkeep", default_width=560, default_height=620, **kwargs)

        add_button = Gtk.Button(
            child=Adw.ButtonContent(icon_name="list-add-symbolic", label="Add"),
            tooltip_text="Add an app or a GitHub repository",
        )
        add_button.connect("clicked", lambda *_: self.show_add_dialog())
        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Check for Updates")
        self.refresh_button.connect("clicked", lambda *_: self.refresh())

        main_menu = Gio.Menu()
        main_menu.append("Check for Updates in the Background", "win.background")
        main_menu.append("About Flatkeep", "win.about")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=main_menu, primary=True, tooltip_text="Main Menu")

        header = Adw.HeaderBar()
        header.pack_start(add_button)
        header.pack_end(menu_button)
        header.pack_end(self.refresh_button)

        # The switch reflects whether the systemd timer is enabled.
        self.background_action = Gio.SimpleAction.new_stateful("background", None, GLib.Variant.new_boolean(False))
        self.background_action.connect("change-state", self._on_background_toggled)
        self.add_action(self.background_action)
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", lambda *_: self.show_about())
        self.add_action(about_action)
        run_async(background.is_enabled, lambda on, _e: self.background_action.set_state(GLib.Variant.new_boolean(bool(on))))

        self.list_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE, valign=Gtk.Align.START, css_classes=["boxed-list"]
        )
        clamp = Adw.Clamp(
            child=self.list_box, maximum_size=600,
            margin_top=24, margin_bottom=24, margin_start=12, margin_end=12,
        )
        scrolled = Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER)

        empty_button = Gtk.Button(label="Add App", halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
        empty_button.connect("clicked", lambda *_: self.show_add_dialog())
        empty = Adw.StatusPage(
            icon_name="system-software-install-symbolic",
            title="No Apps Yet",
            description="Add a GitHub repository to install its .flatpak releases, or just to get notified about new versions.",
            child=empty_button,
        )

        self.stack = Gtk.Stack()
        self.stack.add_named(empty, "empty")
        self.stack.add_named(scrolled, "list")
        self.toasts = Adw.ToastOverlay(child=self.stack)

        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        self.set_content(view)

        self.refresh()

    def toast(self, message: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=message, use_markup=False))

    def refresh(self) -> None:
        """Rebuild the list and check every app for updates."""
        self.list_box.remove_all()
        apps = store.load()
        self.stack.set_visible_child_name("list" if apps else "empty")
        if not apps:
            return

        rows = {app.app_id: AppRow(self, app) for app in apps}
        for row in rows.values():
            self.list_box.append(row)

        def check_all():
            installed = host.installed_apps()
            return [updater.check(app, installed) for app in apps]

        def done(statuses, error):
            self.refresh_button.set_sensitive(True)
            if error:
                self.toast(f"Couldn't check for updates: {error}")
                return
            for status in statuses:
                rows[status.app.app_id].set_status(status)

        self.refresh_button.set_sensitive(False)
        run_async(check_all, done)

    def show_add_dialog(self) -> None:
        entry = Gtk.Entry(placeholder_text="https://github.com/owner/repo", activates_default=True)
        watch_only = Gtk.CheckButton(label="Only notify me about new releases")
        prereleases = Gtk.CheckButton(label="Include pre-releases")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(entry)
        box.append(watch_only)
        box.append(prereleases)

        dialog = Adw.AlertDialog(
            heading="Add from GitHub",
            body="Flatkeep installs the .flatpak file from the latest release and keeps it updated. "
            "For other programs, it can tell you when a new release comes out.",
            extra_child=box,
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")

        def on_response(_dialog, response):
            if response != "add":
                return
            if watch_only.get_active():
                self.watch_repo(entry.get_text(), prereleases.get_active())
            else:
                self.add_app(entry.get_text(), prereleases.get_active())

        dialog.connect("response", on_response)
        dialog.present(self)

    def add_app(self, text: str, include_prereleases: bool = False) -> None:
        toast = Adw.Toast(title="Looking up releases…", timeout=0)
        self.toasts.add_toast(toast)

        def progress(fraction):
            GLib.idle_add(toast.set_title, progress_text(fraction))

        def done(app, error):
            toast.dismiss()
            if isinstance(error, updater.NoFlatpakError):
                self.offer_watch(text, str(error), include_prereleases)
            elif error:
                self.toast(f"Couldn't add app: {error}")
            else:
                self.toast(f"Added {app.name} {app.installed_tag}")
                self.refresh()

        run_async(lambda: updater.add(text, progress, include_prereleases), done)

    def offer_watch(self, text: str, reason: str, include_prereleases: bool) -> None:
        dialog = Adw.AlertDialog(
            heading="No Flatpak in This Release",
            body=f"{reason}. Flatkeep can't install it, but it can notify you when a new release comes out.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("watch", "Notify Me")
        dialog.set_response_appearance("watch", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("watch")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, response: response == "watch" and self.watch_repo(text, include_prereleases))
        dialog.present(self)

    def watch_repo(self, text: str, include_prereleases: bool = False) -> None:
        def done(app, error):
            if error:
                self.toast(f"Couldn't add repository: {error}")
            else:
                self.toast(f"Watching {app.repo} · latest release is {app.seen_tag}")
                self.refresh()

        run_async(lambda: updater.watch(text, include_prereleases), done)

    def _on_background_toggled(self, action, value) -> None:
        enable = value.get_boolean()

        def done(_result, error):
            if error:
                self.toast(f"Couldn't change background checks: {error}")
                return
            action.set_state(value)
            if enable:
                self.toast(f"Flatkeep will check for updates every {background.INTERVAL}, even when closed")

        run_async(background.enable if enable else background.disable, done)

    def show_about(self) -> None:
        Adw.AboutDialog(
            application_name="Flatkeep",
            application_icon=APP_ID,
            version=VERSION,
            comments="Install and update Flatpak apps from GitHub releases",
            license_type=Gtk.License.GPL_3_0,
        ).present(self)


class AppRow(Adw.ActionRow):
    def __init__(self, window: MainWindow, app: store.TrackedApp):
        super().__init__(title=app.name or app.app_id, subtitle="Checking for updates…", use_markup=False)
        self.window = window
        self.app = app
        self.status = None

        self.add_prefix(self._make_icon())

        self.spinner = Adw.Spinner()
        self.button = Gtk.Button(valign=Gtk.Align.CENTER, visible=False, css_classes=["suggested-action"])
        self.button.connect("clicked", lambda *_: self._on_button_clicked())

        menu = Gio.Menu()
        menu.append("Open Releases Page", "row.releases")
        menu.append("Edit…", "row.edit")
        menu.append("Remove…", "row.remove")
        menu_button = Gtk.MenuButton(
            icon_name="view-more-symbolic", menu_model=menu,
            valign=Gtk.Align.CENTER, css_classes=["flat"], tooltip_text="More",
        )
        for widget in (self.spinner, self.button, menu_button):
            self.add_suffix(widget)

        actions = Gio.SimpleActionGroup()
        for name, callback in (
            ("releases", self._open_releases), ("edit", self._show_edit), ("remove", self._confirm_remove),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            actions.add_action(action)
        self.insert_action_group("row", actions)

    def _make_icon(self) -> Gtk.Image:
        path = store.icon_path(self.app.app_id)
        if path.exists():
            image = Gtk.Image.new_from_file(str(path))
        else:
            image = Gtk.Image.new_from_icon_name("folder-download" if self.app.is_watch else "application-x-executable")
        image.set_pixel_size(40)
        return image

    def set_busy(self, busy: bool) -> None:
        self.spinner.set_visible(busy)
        if busy:
            self.button.set_visible(False)

    def set_status(self, status: updater.Status) -> None:
        self.status = status
        self.set_busy(False)
        if status.error:
            self.set_subtitle(f"Couldn't check for updates: {status.error}")
        elif self.app.is_watch:
            if status.update_available:
                self.set_subtitle(f"New release: {tag_text(status.release)}")
            else:
                self.set_subtitle(f"Latest release: {tag_text(status.release)} · Notifications only")
            self.button.set_label("View Release")
            self.button.set_visible(status.update_available)
            return
        elif status.installed is None:
            self.set_subtitle(f"Not installed · latest is {tag_text(status.release)}")
        elif status.update_available:
            self.set_subtitle(f"{status.current} → {tag_text(status.release)}")
        else:
            self.set_subtitle(f"{status.current} · Up to date")
        self.button.set_label("Install" if status.installed is None else "Update")
        self.button.set_visible(status.update_available)

    def _on_button_clicked(self) -> None:
        if self.app.is_watch:
            self.view_release()
        else:
            self.install_update()

    def view_release(self) -> None:
        release = self.status.release
        Gtk.UriLauncher.new(release.html_url).launch(self.window, None, None)
        self.set_busy(True)
        run_async(lambda: updater.mark_seen(self.app, release.tag), lambda _r, _e: self.recheck())

    def _show_edit(self, *_):
        dialog = EditDialog(self.app, on_changed=lambda: self.set_title(self.app.name or self.app.repo))
        # Re-check once the dialog closes, since the repo or pre-release setting may have changed.
        dialog.connect("closed", lambda *_: self.window.refresh())
        dialog.present(self.window)

    def install_update(self) -> None:
        self.set_busy(True)

        def progress(fraction):
            GLib.idle_add(self.set_subtitle, progress_text(fraction))

        def done(tag, error):
            if error:
                self.window.toast(f"Couldn't update {self.get_title()}: {error}")
            elif tag:
                self.window.toast(f"Updated {self.get_title()} to {tag}")
            self.recheck()

        run_async(lambda: updater.update(self.app, progress), done)

    def recheck(self) -> None:
        self.set_busy(True)
        self.set_subtitle("Checking for updates…")
        run_async(lambda: updater.check(self.app), lambda status, _error: self.set_status(status))

    def _open_releases(self, *_):
        Gtk.UriLauncher.new(f"https://github.com/{self.app.repo}/releases").launch(self.window, None, None)

    def _confirm_remove(self, *_):
        if self.app.is_watch:
            dialog = Adw.AlertDialog(
                heading=f"Stop Watching {self.app.repo}?",
                body="You won't be notified about its new releases anymore.",
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("untrack", "Stop Watching")
            dialog.set_response_appearance("untrack", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_close_response("cancel")
            dialog.connect("response", self._on_remove_response)
            dialog.present(self.window)
            return

        dialog = Adw.AlertDialog(
            heading=f"Remove {self.get_title()}?",
            body="Flatkeep will stop checking it for updates. You can keep the app installed or uninstall it too.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("untrack", "Keep Installed")
        dialog.add_response("uninstall", "Uninstall")
        dialog.set_response_appearance("uninstall", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_remove_response)
        dialog.present(self.window)

    def _on_remove_response(self, _dialog, response):
        if response == "cancel":
            return
        self.set_busy(True)

        def done(_result, error):
            if error:
                self.window.toast(f"Couldn't remove {self.get_title()}: {error}")
                self.set_busy(False)
            else:
                self.window.refresh()

        run_async(lambda: updater.remove(self.app.app_id, uninstall=response == "uninstall"), done)
