from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

GITHUB_REPO = "raphamaster/sqlshelf"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases"


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse 'v1.2.3' or '1.2.3' into (1, 2, 3). Returns (0,) on failure."""
    v = v.lstrip("v").strip()
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


@dataclass
class UpdateInfo:
    current_version: str
    latest_version: str
    release_url: str
    release_notes: str
    has_update: bool
    assets: list[dict] = field(default_factory=list)

    def installer_asset_url(self) -> Optional[str]:
        """Return the Windows setup .exe download URL if present in release assets."""
        for asset in self.assets:
            name: str = asset.get("name", "")
            if name.lower().endswith("-setup.exe"):
                return asset.get("browser_download_url")
        return None


def check_for_updates(current_version: str, timeout: int = 10) -> UpdateInfo:
    """
    Query GitHub Releases API and compare with *current_version*.

    Raises urllib.error.URLError or OSError on network/timeout failure.
    The caller is responsible for running this off the UI thread.
    """
    req = urllib.request.Request(
        GITHUB_API_URL,
        headers={"User-Agent": f"SQLShelf/{current_version}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data: dict = json.loads(resp.read().decode("utf-8"))

    latest_tag: str = data.get("tag_name", "")
    latest_clean = latest_tag.lstrip("v").strip()
    release_url: str = data.get("html_url", RELEASES_PAGE)
    release_notes: str = data.get("body", "")
    assets: list[dict] = data.get("assets", [])

    has_update = _parse_version(latest_clean) > _parse_version(current_version)

    return UpdateInfo(
        current_version=current_version,
        latest_version=latest_clean,
        release_url=release_url,
        release_notes=release_notes,
        has_update=has_update,
        assets=assets,
    )
