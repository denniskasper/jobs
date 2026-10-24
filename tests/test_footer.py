import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from jobs import __version__

PAGES = ("/", "/proposals", "/proposals/left-out", "/documents", "/sources")

LICENCE = Path(__file__).resolve().parent.parent / "LICENSE"


def owner() -> str:
    """Who holds the copyright, read from the licence so that no test has to name them."""
    holder = re.search(r"^Copyright \(c\) \d{4}(?:-\d{4})? (.+)$", LICENCE.read_text(), re.M)
    assert holder is not None, "the licence names no copyright holder"
    return holder.group(1).strip()


@pytest.mark.parametrize("path", PAGES)
def test_every_page_ends_in_the_version(dashboard: TestClient, path: str) -> None:
    footer = re.search(r"<footer[^>]*>(.*?)</footer>", dashboard.get(path).text, re.S)

    assert footer, "the page has no footer"
    assert re.sub(r"<[^>]+>|\s", "", footer.group(1)) == f"v{__version__}"


@pytest.mark.parametrize("path", PAGES)
def test_no_page_names_the_owner(dashboard: TestClient, path: str) -> None:
    page = dashboard.get(path).text

    assert owner() not in page
    assert "©" not in page
    assert "copyright" not in page.lower()
