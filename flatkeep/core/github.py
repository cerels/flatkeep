"""Look up releases and download files from GitHub."""

import json
import os
import platform
import re
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field

API = "https://api.github.com"
USER_AGENT = "Flatkeep"

_REPO_RE = re.compile(r"^(?:https?://)?(?:www\.)?(?:github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/.*)?$")

# Names that show up in release file names for each CPU architecture.
_ARCH_ALIASES = {
    "x86_64": ("x86_64", "x86-64", "amd64", "x64"),
    "aarch64": ("aarch64", "arm64"),
}


@dataclass
class Asset:
    name: str
    url: str
    size: int


@dataclass
class Release:
    tag: str
    name: str
    html_url: str
    prerelease: bool = False
    assets: list[Asset] = field(default_factory=list)


def parse_repo(text: str) -> str:
    """Turn 'https://github.com/owner/repo' or 'owner/repo' into 'owner/repo'."""
    match = _REPO_RE.match(text.strip())
    if not match:
        raise ValueError(f"Not a GitHub repository: {text!r}")
    return f"{match[1]}/{match[2]}"


def _get_json(url: str):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    # Without a token GitHub allows 60 API requests per hour.
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _release(data: dict) -> Release:
    return Release(
        tag=data["tag_name"],
        name=data.get("name") or data["tag_name"],
        html_url=data["html_url"],
        prerelease=data.get("prerelease", False),
        assets=[
            Asset(name=a["name"], url=a["browser_download_url"], size=a.get("size", 0))
            for a in data.get("assets", [])
        ],
    )


def releases(repo: str, include_prereleases: bool = False) -> Iterator[Release]:
    """Releases newest first. Without pre-releases, only the latest stable one."""
    try:
        if not include_prereleases:
            yield _release(_get_json(f"{API}/repos/{repo}/releases/latest"))
            return
        for data in _get_json(f"{API}/repos/{repo}/releases?per_page=20"):
            if not data.get("draft"):
                yield _release(data)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        hint = "" if include_prereleases else ", or include pre-releases"
        raise LookupError(f"No releases found for {repo}. Check the URL{hint}") from None


def latest_release(repo: str, include_prereleases: bool = False) -> Release:
    for release in releases(repo, include_prereleases):
        return release
    raise LookupError(f"{repo} has no releases yet")


def pick_flatpak_asset(release: Release, pattern: str = "") -> Asset | None:
    """Choose the .flatpak file for this machine's architecture."""
    candidates = [a for a in release.assets if a.name.endswith(".flatpak")]
    if pattern:
        candidates = [a for a in candidates if re.search(pattern, a.name)]

    arch = platform.machine()
    ours = _ARCH_ALIASES.get(arch, (arch,))
    all_aliases = [alias for aliases in _ARCH_ALIASES.values() for alias in aliases]

    for asset in candidates:
        if any(alias in asset.name.lower() for alias in ours):
            return asset
    # Fall back to a file that doesn't name any architecture.
    for asset in candidates:
        if not any(alias in asset.name.lower() for alias in all_aliases):
            return asset
    return None


def download(url: str, dest, progress=None) -> None:
    """Download url to dest. progress(fraction) is called as data arrives."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response, open(dest, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total)
