"""What Flatkeep says about apps, in the window and on the command line.

Only text, no GTK, so the wording lives in one place and has examples.
"""

import dataclasses
from dataclasses import dataclass

from .github import Release
from .store import TrackedApp
from .updater import Status

# Examples from the other modules, used in this module's examples.
from .background import NEW_FLATPAK_RELEASE, NUVIO_UPDATE  # noqa: E402
from .github import NUVIO_RELEASE  # noqa: E402
from .store import FLATPAK_REPO, NUVIO  # noqa: E402

_UP_TO_DATE = dataclasses.replace(NUVIO_UPDATE, release=NUVIO_RELEASE)


@dataclass
class RowText:
    """What an app's row in the window shows under its name.

    Interpretation: subtitle is the line under the name; button is the label
    of the row's action button, or None for no button.
    """

    subtitle: str
    button: str | None


@dataclass
class Question:
    """A question the window asks before doing something.

    Interpretation: answers are (id, label) pairs, shown left to right; the
    first one cancels; destructive is the id of the answer to show in red,
    if any.
    """

    heading: str
    body: str
    answers: list[tuple[str, str]]
    destructive: str | None = None


def tag_text(release: Release) -> str:
    """A release's tag, marked when it's a pre-release.

    >>> tag_text(NUVIO_RELEASE)
    '0.1.27-alpha'
    >>> tag_text(dataclasses.replace(NUVIO_RELEASE, tag="v2.0-rc.1", prerelease=True))
    'v2.0-rc.1 (pre-release)'
    """
    return f"{release.tag} (pre-release)" if release.prerelease else release.tag


def progress_text(fraction: float) -> str:
    """What to show while a download is fraction (0 to 1) done.

    >>> progress_text(0.42), progress_text(1.0)
    ('Downloading… 42%', 'Installing…')
    """
    return "Installing…" if fraction >= 1 else f"Downloading… {fraction:.0%}"


def row_text(status: Status) -> RowText:
    """What an app's row shows, given what the last check found.

    >>> row_text(_UP_TO_DATE)
    RowText(subtitle='0.1.27-alpha · Up to date', button=None)
    >>> row_text(NUVIO_UPDATE)
    RowText(subtitle='0.1.27-alpha → 0.1.28-alpha', button='Update')
    >>> row_text(dataclasses.replace(NUVIO_UPDATE, installed=None))
    RowText(subtitle='Not installed · latest is 0.1.28-alpha', button='Install')
    >>> row_text(NEW_FLATPAK_RELEASE)
    RowText(subtitle='New release: 1.19.0', button='View Release')
    >>> row_text(dataclasses.replace(NEW_FLATPAK_RELEASE, app=dataclasses.replace(FLATPAK_REPO, seen_tag="1.19.0")))
    RowText(subtitle='Latest release: 1.19.0 · Notifications only', button=None)
    >>> row_text(dataclasses.replace(NUVIO_UPDATE, release=None, error="offline"))
    RowText(subtitle="Couldn't check for updates: offline", button=None)
    """
    if status.error:
        return RowText(f"Couldn't check for updates: {status.error}", None)
    new, tag = status.update_available, tag_text(status.release)
    if status.app.is_watch:
        if new:
            return RowText(f"New release: {tag}", "View Release")
        return RowText(f"Latest release: {tag} · Notifications only", None)
    if status.installed is None:
        return RowText(f"Not installed · latest is {tag}", "Install" if new else None)
    if new:
        return RowText(f"{status.current} → {tag}", "Update")
    return RowText(f"{status.current} · Up to date", None)


def list_line(status: Status) -> str:
    """One line of `flatkeep list`: name, app ID and state, in columns.

    >>> list_line(_UP_TO_DATE)
    'Nuvio                    com.nuvio.media.desktop              0.1.27-alpha (up to date)'
    >>> list_line(NEW_FLATPAK_RELEASE).split(maxsplit=2)[2]
    'new release 1.19.0'
    >>> list_line(dataclasses.replace(NUVIO_UPDATE, release=dataclasses.replace(NUVIO_UPDATE.release, prerelease=True))).endswith('0.1.27-alpha -> 0.1.28-alpha [pre-release]')
    True
    """
    app = status.app
    if status.error:
        state = f"error: {status.error}"
    elif app.is_watch:
        state = f"new release {status.release.tag}" if status.update_available else f"watching ({status.release.tag})"
    elif status.installed is None:
        state = f"not installed (latest {status.release.tag})"
    elif status.update_available:
        state = f"{status.current} -> {status.release.tag}"
    else:
        state = f"{status.current} (up to date)"
    if status.release and status.release.prerelease:
        state += " [pre-release]"
    return f"{app.name or app.app_id:<24} {app.app_id:<36} {state}"


def settings_line(app: TrackedApp) -> str:
    """An app's main settings, as `flatkeep edit` reports them.

    >>> settings_line(NUVIO)
    'com.nuvio.media.desktop: repo NuvioMedia/NuvioDesktop, pre-releases off, auto-update on'
    >>> settings_line(FLATPAK_REPO)
    'github:flatpak/flatpak: repo flatpak/flatpak, pre-releases off'
    """
    def on_off(flag):
        return "on" if flag else "off"

    line = f"{app.app_id}: repo {app.repo}, pre-releases {on_off(app.include_prereleases)}"
    return line if app.is_watch else f"{line}, auto-update {on_off(app.auto_update)}"


def removal_question(app: TrackedApp) -> Question:
    """What to ask before removing app from the list. Installed apps can
    stay installed or be uninstalled too.

    >>> removal_question(FLATPAK_REPO).answers
    [('cancel', 'Cancel'), ('untrack', 'Stop Watching')]
    >>> q = removal_question(NUVIO)
    >>> q.heading, q.answers, q.destructive
    ('Remove Nuvio?', [('cancel', 'Cancel'), ('untrack', 'Keep Installed'), ('uninstall', 'Uninstall')], 'uninstall')
    """
    if app.is_watch:
        return Question(
            heading=f"Stop Watching {app.repo}?",
            body="You won't be notified about its new releases anymore.",
            answers=[("cancel", "Cancel"), ("untrack", "Stop Watching")],
            destructive="untrack",
        )
    return Question(
        heading=f"Remove {app.name or app.app_id}?",
        body="Flatkeep will stop checking it for updates. You can keep the app installed or uninstall it too.",
        answers=[("cancel", "Cancel"), ("untrack", "Keep Installed"), ("uninstall", "Uninstall")],
        destructive="uninstall",
    )


def watch_instead_question(reason: str) -> Question:
    """What to ask when a repo has no .flatpak file to install.

    >>> watch_instead_question("Release 1.18.4 of flatpak/flatpak has no .flatpak file").answers
    [('cancel', 'Cancel'), ('watch', 'Notify Me')]
    """
    return Question(
        heading="No Flatpak in This Release",
        body=f"{reason}. Flatkeep can't install it, but it can notify you when a new release comes out.",
        answers=[("cancel", "Cancel"), ("watch", "Notify Me")],
    )


def field_text(app: TrackedApp, field: str) -> str | bool:
    """What the Edit dialog shows for one of app's settings (a TrackedApp
    field name). Switches get a bool, text fields a str.

    >>> field_text(NUVIO, "repo"), field_text(NUVIO, "auto_update")
    ('https://github.com/NuvioMedia/NuvioDesktop', True)
    """
    if field == "repo":
        return f"https://github.com/{app.repo}"
    return getattr(app, field)
