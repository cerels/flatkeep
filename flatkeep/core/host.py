"""Run the `flatpak` command, escaping the sandbox when Flatkeep is a Flatpak itself.

Inside the sandbox we can't install apps directly, so commands go through
`flatpak-spawn --host`, which needs the --talk-name=org.freedesktop.Flatpak
permission in the manifest. Everything that touches the host lives here.
"""

import os
import subprocess

IN_SANDBOX = os.path.exists("/.flatpak-info")


class FlatpakError(RuntimeError):
    pass


def run(*command: str, input: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Effect: runs command on the host system (outside the sandbox, if
    we're in one), feeding it input. With check, a failure raises FlatpakError."""
    if IN_SANDBOX:
        command = ("flatpak-spawn", "--host", *command)
    result = subprocess.run(command, input=input, capture_output=True, text=True)
    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise FlatpakError(message or f"{command[0]} failed")
    return result


def flatpak(*args: str) -> str:
    """Effect: runs the host's flatpak command; returns what it printed."""
    return run("flatpak", *args).stdout


def parse_installed(listing: str) -> dict[str, str]:
    r"""App ID -> version, from `flatpak list --columns=application,version`.
    We use --columns because `flatpak info` output is translated.

    >>> parse_installed("com.nuvio.media.desktop\t0.1.27-alpha\nio.github.cerels.Flatkeep\t\n")
    {'com.nuvio.media.desktop': '0.1.27-alpha', 'io.github.cerels.Flatkeep': ''}
    """
    apps = {}
    for line in listing.splitlines():
        app_id, _, version = line.partition("\t")
        if app_id:
            apps[app_id] = version.strip()
    return apps


def installed_apps() -> dict[str, str]:
    """App ID -> version for apps in the user installation.

    Effect: runs `flatpak list` on the host.
    """
    return parse_installed(flatpak("list", "--app", "--user", "--columns=application,version"))


def install_bundle(path) -> None:
    """Effect: installs the bundle file for the user. --reinstall lets a
    newer bundle replace the installed version."""
    flatpak("install", "--user", "--noninteractive", "--reinstall", "--bundle", str(path))


def uninstall(app_id: str) -> None:
    """Effect: uninstalls app_id from the user installation."""
    flatpak("uninstall", "--user", "--noninteractive", app_id)
