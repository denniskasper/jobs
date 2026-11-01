import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_the_image_installs_the_playwright_the_package_is_tested_with() -> None:
    """A Playwright only drives the Chromium build it was released with. The image installs that
    browser before the package, so the two pins must name one version, or CI tests another
    Playwright than the one that ships."""
    (pinned,) = re.findall(
        r"^ARG PLAYWRIGHT_VERSION=(\S+)$", (ROOT / "Dockerfile").read_text(), re.M
    )
    dependencies = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]

    assert f"playwright=={pinned}" in dependencies
