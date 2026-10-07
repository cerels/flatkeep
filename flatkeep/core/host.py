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
    """Run a command on the host system."""
    if IN_SANDBOX:
        command = ("flatpak-spawn", "--host", *command)
    result = subprocess.run(command, input=input, capture_output=True, text=True)
    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise FlatpakError(message or f"{command[0]} failed")
    return result


def flatpak(*args: str) -> str:
    return run("flatpak", *args).stdout


def installed_apps() -> dict[str, str]:
    """Map of app ID -> version for apps in the user installation."""
    output = flatpak("list", "--app", "--user", "--columns=application,version")
    apps = {}
    for line in output.splitlines():
        app_id, _, version = line.partition("\t")
        if app_id:
            apps[app_id] = version.strip()
    return apps


def install_bundle(path) -> None:
    # --reinstall lets a newer bundle replace the installed version.
    flatpak("install", "--user", "--noninteractive", "--reinstall", "--bundle", str(path))


def uninstall(app_id: str) -> None:
    flatpak("uninstall", "--user", "--noninteractive", app_id)
