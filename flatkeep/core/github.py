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
    """A file attached to a release.

    Interpretation: name is the file name, url downloads it, size is in bytes.
    """

    name: str
    url: str
    size: int


@dataclass
class Release:
    """A published version of a repository.

    Interpretation:
    - tag: the git tag, e.g. "v1.2.0"; this is what Flatkeep compares
    - name: the title shown on GitHub (the tag if it has none)
    - html_url: the release's page
    - prerelease: marked as alpha/beta/nightly by the author
    - published: when it was published, ISO 8601 in UTC ("2026-10-03T10:58:40Z"),
      "" if unknown. Same-format UTC strings sort in time order.
    - assets: the attached files, in GitHub's order
    """

    tag: str
    name: str
    html_url: str
    prerelease: bool = False
    published: str = ""
    assets: list[Asset] = field(default_factory=list)


# Examples:
def _asset(name: str) -> Asset:
    return Asset(name=name, url=f"https://example.com/{name}", size=1)


NUVIO_RELEASE = Release(
    tag="0.1.27-alpha", name="0.1.27-alpha", published="2026-10-03T10:58:40Z",
    html_url="https://github.com/NuvioMedia/NuvioDesktop/releases/tag/0.1.27-alpha",
    assets=[_asset("Nuvio-0.1.27.AppImage"), _asset("Nuvio-Linux-x86_64-0.1.27-alpha.flatpak")],
)
NEWER_NUVIO = Release(
    tag="0.1.28-alpha", name="0.1.28-alpha", published="2026-10-10T00:00:00Z",
    html_url="https://github.com/NuvioMedia/NuvioDesktop/releases/tag/0.1.28-alpha",
    assets=[_asset("Nuvio-Linux-x86_64-0.1.28-alpha.flatpak")],
)
MULTI_ARCH_RELEASE = Release(
    tag="v2.0", name="Version 2", html_url="https://github.com/o/r/releases/tag/v2.0", published="2026-05-01T12:00:00Z",
    assets=[_asset("app-aarch64.flatpak"), _asset("app-x86_64.flatpak"), _asset("app-x86_64-debug.flatpak")],
)
NO_FLATPAK_RELEASE = Release(
    tag="1.18.4", name="1.18.4", html_url="https://github.com/flatpak/flatpak/releases/tag/1.18.4",
    published="2026-08-01T09:00:00Z",
    assets=[_asset("flatpak-1.18.4.tar.xz")],
)


def parse_repo(text: str) -> str:
    """The "owner/name" of a GitHub repository, from a URL or "owner/name".

    >>> parse_repo("https://github.com/NuvioMedia/NuvioDesktop")
    'NuvioMedia/NuvioDesktop'
    >>> parse_repo("github.com/flatpak/flatpak.git")
    'flatpak/flatpak'
    >>> parse_repo("https://github.com/o/r/releases/tag/v1")
    'o/r'
    >>> parse_repo("  owner/name ")
    'owner/name'
    >>> parse_repo("not-a-url")
    Traceback (most recent call last):
    ValueError: Not a GitHub repository: 'not-a-url'
    """
    match = _REPO_RE.match(text.strip())
    if not match:
        raise ValueError(f"Not a GitHub repository: {text!r}")
    return f"{match[1]}/{match[2]}"


def release_from_json(data: dict) -> Release:
    """A Release from one entry of GitHub's releases API.

    >>> release_from_json({"tag_name": "v1", "html_url": "u", "name": None, "published_at": "2026-01-02T03:04:05Z",
    ...     "assets": [{"name": "a.flatpak", "browser_download_url": "d", "size": 5}]})
    Release(tag='v1', name='v1', html_url='u', prerelease=False, published='2026-01-02T03:04:05Z', assets=[Asset(name='a.flatpak', url='d', size=5)])
    """
    return Release(
        tag=data["tag_name"],
        name=data.get("name") or data["tag_name"],
        html_url=data["html_url"],
        prerelease=data.get("prerelease", False),
        published=data.get("published_at") or "",
        assets=[
            Asset(name=a["name"], url=a["browser_download_url"], size=a.get("size", 0))
            for a in data.get("assets", [])
        ],
    )


def mentions_arch(file_name: str, aliases) -> bool:
    """Does the file name contain any of these architecture names?

    >>> mentions_arch("App-Linux-x86_64.flatpak", ("x86_64", "amd64"))
    True
    >>> mentions_arch("app.flatpak", ("x86_64", "amd64"))
    False
    """
    return any(alias in file_name.lower() for alias in aliases)


def pick_flatpak_asset(release: Release, pattern: str = "", arch: str | None = None) -> Asset | None:
    """The .flatpak file for this computer: the first one naming our CPU
    architecture, else the first one naming none. pattern (a regex) narrows
    the choice; arch defaults to this machine's.

    >>> pick_flatpak_asset(NUVIO_RELEASE, arch="x86_64").name
    'Nuvio-Linux-x86_64-0.1.27-alpha.flatpak'
    >>> pick_flatpak_asset(MULTI_ARCH_RELEASE, arch="aarch64").name
    'app-aarch64.flatpak'
    >>> pick_flatpak_asset(MULTI_ARCH_RELEASE, pattern="debug", arch="x86_64").name
    'app-x86_64-debug.flatpak'
    >>> pick_flatpak_asset(NUVIO_RELEASE, arch="aarch64") is None  # only an x86_64 file
    True
    >>> pick_flatpak_asset(NO_FLATPAK_RELEASE, arch="x86_64") is None
    True
    >>> no_arch = Release(tag="1", name="1", html_url="u", assets=[_asset("app.flatpak")])
    >>> pick_flatpak_asset(no_arch, arch="aarch64").name
    'app.flatpak'
    """
    arch = arch or platform.machine()
    ours = _ARCH_ALIASES.get(arch, (arch,))
    all_aliases = [alias for aliases in _ARCH_ALIASES.values() for alias in aliases]

    candidates = [a for a in release.assets if a.name.endswith(".flatpak")]
    if pattern:
        candidates = [a for a in candidates if re.search(pattern, a.name)]

    ours_first = [a for a in candidates if mentions_arch(a.name, ours)]
    no_arch = [a for a in candidates if not mentions_arch(a.name, all_aliases)]
    return next(iter(ours_first + no_arch), None)


def _get_json(url: str):
    """Effect: one request to the GitHub API."""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    # Without a token GitHub allows 60 API requests per hour.
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def releases(repo: str, include_prereleases: bool = False) -> Iterator[Release]:
    """The repo's releases, newest first. Without pre-releases, only the
    latest stable one. Drafts are skipped.

    Effect: asks the GitHub API, lazily (stopping early saves requests).
    """
    try:
        if not include_prereleases:
            yield release_from_json(_get_json(f"{API}/repos/{repo}/releases/latest"))
            return
        for data in _get_json(f"{API}/repos/{repo}/releases?per_page=20"):
            if not data.get("draft"):
                yield release_from_json(data)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        hint = "" if include_prereleases else ", or include pre-releases"
        raise LookupError(f"No releases found for {repo}. Check the URL{hint}") from None


def latest_release(repo: str, include_prereleases: bool = False) -> Release:
    """Effect: asks the GitHub API (see releases)."""
    for release in releases(repo, include_prereleases):
        return release
    raise LookupError(f"{repo} has no releases yet")


def download(url: str, dest, progress=None) -> None:
    """Effect: downloads url into the file dest, calling progress(fraction)
    as data arrives. No token is sent: downloads redirect to another host."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response, open(dest, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total)
