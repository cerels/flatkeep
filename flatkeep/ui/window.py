"""The main window: the list of tracked apps and what you can do with them.

Every method here changes widgets. Slow work (GitHub, flatpak, files) goes
through run_async; "Effect:" lines say which methods start such work. What
the window *says* comes from core/describe.py.
"""

from gi.repository import Adw, Gio, GLib, Gtk

from .. import APP_ID, VERSION
from ..core import background, describe, host, store, updater
from ..core.describe import Question
from .edit_dialog import EditDialog
from .tasks import run_async


def ask(parent: Gtk.Widget, question: Question, on_answer) -> None:
    """Show question as a dialog; call on_answer(answer id) unless the user
    cancels. Without a destructive answer, the last one is the default."""
    dialog = Adw.AlertDialog(heading=question.heading, body=question.body)
    for answer_id, label in question.answers:
        dialog.add_response(answer_id, label)
    cancel, default = question.answers[0][0], question.answers[-1][0]
    if question.destructive:
        dialog.set_response_appearance(question.destructive, Adw.ResponseAppearance.DESTRUCTIVE)
    else:
        dialog.set_response_appearance(default, Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response(default)
    dialog.set_close_response(cancel)
    dialog.connect("response", lambda _dialog, answer: answer != cancel and on_answer(answer))
    dialog.present(parent)


def open_url(parent: Gtk.Window, url: str) -> None:
    """Effect: opens url in the web browser."""
    Gtk.UriLauncher.new(url).launch(parent, None, None)


class MainWindow(Adw.ApplicationWindow):
    """The window: a header bar (Add, refresh, main menu) over either the
    list of apps or, when there are none, a page inviting you to add one."""

    def __init__(self, **kwargs):
        super().__init__(title="Flatkeep", default_width=560, default_height=620, **kwargs)
        self._add_actions()

        view = Adw.ToolbarView(content=self._make_content())
        view.add_top_bar(self._make_header())
        self.set_content(view)
        self.refresh()

    def _add_actions(self) -> None:
        """Create the main menu's actions. The background switch starts off
        and is corrected once systemd answers.

        Effect: asks systemd whether background checks are on.
        """
        self.background_action = Gio.SimpleAction.new_stateful("background", None, GLib.Variant.new_boolean(False))
        self.background_action.connect("change-state", self._on_background_toggled)
        self.add_action(self.background_action)
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", lambda *_: self.show_about())
        self.add_action(about_action)
        run_async(
            background.is_enabled,
            lambda on, _error: self.background_action.set_state(GLib.Variant.new_boolean(bool(on))),
        )

    def _make_header(self) -> Adw.HeaderBar:
        """The header bar. Add has a text label because some icon themes
        draw list-add-symbolic nearly invisible."""
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
        menu_button = Gtk.MenuButton(
            icon_name="open-menu-symbolic", menu_model=main_menu, primary=True, tooltip_text="Main Menu"
        )

        header = Adw.HeaderBar()
        header.pack_start(add_button)
        header.pack_end(menu_button)
        header.pack_end(self.refresh_button)
        return header

    def _make_content(self) -> Gtk.Widget:
        """The list page and the empty page, switched by self.stack, inside
        the overlay that shows toasts."""
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
        return self.toasts

    def toast(self, message: str) -> None:
        """Show message briefly at the bottom of the window."""
        self.toasts.add_toast(Adw.Toast(title=message, use_markup=False))

    def refresh(self) -> None:
        """Rebuild the list from the saved apps and check each for updates.

        Effect: asks flatpak and GitHub.
        """
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
        """Ask for a GitHub URL, then add it as an app or a watched repo."""
        entry = Gtk.Entry(placeholder_text="https://github.com/owner/repo", activates_default=True)
        watch_only = Gtk.CheckButton(label="Only notify me about new releases")
        prereleases = Gtk.CheckButton(label="Include pre-releases")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for widget in (entry, watch_only, prereleases):
            box.append(widget)

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
        """Add the app from the GitHub repo text, showing progress in a toast.
        If the repo has no .flatpak, offer to watch it instead.

        Effect: downloads and installs (see updater.add).
        """
        toast = Adw.Toast(title="Looking up releases…", timeout=0)
        self.toasts.add_toast(toast)

        def progress(fraction):
            GLib.idle_add(toast.set_title, describe.progress_text(fraction))

        def done(app, error):
            toast.dismiss()
            if isinstance(error, updater.NoFlatpakError):
                question = describe.watch_instead_question(str(error))
                ask(self, question, lambda _answer: self.watch_repo(text, include_prereleases))
            elif error:
                self.toast(f"Couldn't add app: {error}")
            else:
                self.toast(f"Added {app.name} {app.installed_tag}")
                self.refresh()

        run_async(lambda: updater.add(text, progress, include_prereleases), done)

    def watch_repo(self, text: str, include_prereleases: bool = False) -> None:
        """Start watching the GitHub repo text.

        Effect: asks GitHub and saves the repo (see updater.watch).
        """

        def done(app, error):
            if error:
                self.toast(f"Couldn't add repository: {error}")
            else:
                self.toast(f"Watching {app.repo} · latest release is {app.seen_tag}")
                self.refresh()

        run_async(lambda: updater.watch(text, include_prereleases), done)

    def _on_background_toggled(self, action, value) -> None:
        """Turn background checks on or off; the switch only moves once
        that worked.

        Effect: writes or removes the systemd timer.
        """
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
        """Show the About dialog."""
        Adw.AboutDialog(
            application_name="Flatkeep",
            application_icon=APP_ID,
            version=VERSION,
            comments="Install and update Flatpak apps from GitHub releases",
            license_type=Gtk.License.GPL_3_0,
        ).present(self)


class AppRow(Adw.ActionRow):
    """One tracked app in the list: icon, name, what the last check found
    (see describe.row_text), an action button, and a ⋮ menu."""

    def __init__(self, window: MainWindow, app: store.TrackedApp):
        super().__init__(title=app.name or app.app_id, subtitle="Checking for updates…", use_markup=False)
        self.window = window
        self.app = app
        self.status = None

        self.add_prefix(self._make_icon())
        self.spinner = Adw.Spinner()
        self.button = Gtk.Button(valign=Gtk.Align.CENTER, visible=False, css_classes=["suggested-action"])
        self.button.connect("clicked", lambda *_: self._on_button_clicked())
        for widget in (self.spinner, self.button, self._make_menu_button()):
            self.add_suffix(widget)

    def _make_icon(self) -> Gtk.Image:
        """The app's saved icon, or a generic one."""
        path = store.icon_path(self.app.app_id)
        if path.exists():
            image = Gtk.Image.new_from_file(str(path))
        else:
            image = Gtk.Image.new_from_icon_name("folder-download" if self.app.is_watch else "application-x-executable")
        image.set_pixel_size(40)
        return image

    def _make_menu_button(self) -> Gtk.MenuButton:
        """The ⋮ menu and the row actions behind it."""
        menu = Gio.Menu()
        menu.append("Open Releases Page", "row.releases")
        menu.append("Edit…", "row.edit")
        menu.append("Remove…", "row.remove")

        actions = Gio.SimpleActionGroup()
        for name, callback in (
            ("releases", lambda *_: open_url(self.window, f"https://github.com/{self.app.repo}/releases")),
            ("edit", lambda *_: self.show_edit()),
            ("remove", lambda *_: ask(self.window, describe.removal_question(self.app), self.remove)),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            actions.add_action(action)
        self.insert_action_group("row", actions)

        return Gtk.MenuButton(
            icon_name="view-more-symbolic", menu_model=menu,
            valign=Gtk.Align.CENTER, css_classes=["flat"], tooltip_text="More",
        )

    def set_busy(self, busy: bool) -> None:
        """Show the spinner (and hide the button) while work is running."""
        self.spinner.set_visible(busy)
        if busy:
            self.button.set_visible(False)

    def set_status(self, status: updater.Status) -> None:
        """Show what a check found."""
        self.status = status
        self.set_busy(False)
        text = describe.row_text(status)
        self.set_subtitle(text.subtitle)
        self.button.set_label(text.button or "")
        self.button.set_visible(text.button is not None)

    def _on_button_clicked(self) -> None:
        """Watched repos open the new release; apps install the update."""
        if self.app.is_watch:
            self.view_release()
        else:
            self.install_update()

    def view_release(self) -> None:
        """Open the new release's page and count it as seen.

        Effect: opens the browser, saves the app.
        """
        release = self.status.release
        open_url(self.window, release.html_url)
        self.set_busy(True)
        run_async(lambda: updater.mark_seen(self.app, release.tag), lambda _r, _e: self.recheck())

    def show_edit(self) -> None:
        """Open the Edit dialog. Closing it refreshes the list, since the
        repo or pre-release setting may have changed."""
        dialog = EditDialog(self.app, on_changed=lambda: self.set_title(self.app.name or self.app.repo))
        dialog.connect("closed", lambda *_: self.window.refresh())
        dialog.present(self.window)

    def install_update(self) -> None:
        """Install the newest release, showing progress in the subtitle.

        Effect: downloads and installs (see updater.update).
        """
        self.set_busy(True)

        def progress(fraction):
            GLib.idle_add(self.set_subtitle, describe.progress_text(fraction))

        def done(tag, error):
            if error:
                self.window.toast(f"Couldn't update {self.get_title()}: {error}")
            elif tag:
                self.window.toast(f"Updated {self.get_title()} to {tag}")
            self.recheck()

        run_async(lambda: updater.update(self.app, progress), done)

    def recheck(self) -> None:
        """Check this app again.

        Effect: asks flatpak and GitHub.
        """
        self.set_busy(True)
        self.set_subtitle("Checking for updates…")
        run_async(lambda: updater.check(self.app), lambda status, _error: self.set_status(status))

    def remove(self, answer: str) -> None:
        """Remove the app from the list; answer "uninstall" also uninstalls it.

        Effect: see updater.remove.
        """
        self.set_busy(True)

        def done(_result, error):
            if error:
                self.window.toast(f"Couldn't remove {self.get_title()}: {error}")
                self.set_busy(False)
            else:
                self.window.refresh()

        run_async(lambda: updater.remove(self.app.app_id, uninstall=answer == "uninstall"), done)
