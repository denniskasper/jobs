"""The app installed on a phone's home screen, and in its share sheet: a manifest, its icons, and a
share target that opens the page that creates an Application. No service worker is needed for
that, and none is added.

Installing the app and sharing to it are not covered here; that is tried by hand, once.
"""

import html
import re
import struct
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import pytest
from starlette.testclient import TestClient

from conftest import WriteApplication
from test_bringing_in import field

STYLESHEET = Path(__file__).parent.parent / "src" / "jobs" / "web" / "static" / "dashboard.css"


def manifest_of(dashboard: TestClient, page: str = "/") -> tuple[str, dict[str, Any]]:
    """Where a page links its manifest, and what is served there."""
    (href,) = re.findall(r'<link rel="manifest" href="([^"]+)">', dashboard.get(page).text)
    address = urljoin(f"http://testserver{page}", html.unescape(href))
    served = dashboard.get(address)
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("application/manifest+json")
    manifest: dict[str, Any] = served.json()
    return address, manifest


def the_ground() -> str:
    """The colour of the desk, as the stylesheet sets it where nothing is dark."""
    found = re.search(r"--ground:\s*(#[0-9A-Fa-f]{6})", STYLESHEET.read_text(encoding="utf-8"))
    assert found is not None
    return found[1]


@pytest.mark.parametrize("page", ["/", "/applications/new", "/proposals", "/sources", "/documents"])
def test_every_page_links_the_manifest(dashboard: TestClient, page: str) -> None:
    manifest_of(dashboard, page)


def test_the_manifest_names_the_app_and_takes_its_colours_from_the_stylesheet(
    dashboard: TestClient,
) -> None:
    address, manifest = manifest_of(dashboard)

    assert manifest["name"] == "Job search"
    assert manifest["short_name"]
    assert manifest["display"] == "standalone"
    assert manifest["background_color"] == the_ground()
    assert manifest["theme_color"] == the_ground()
    assert dashboard.get(urljoin(address, manifest["start_url"])).status_code == 200


def test_the_share_target_opens_the_page_that_creates_an_application_filled_in(
    dashboard: TestClient,
) -> None:
    address, manifest = manifest_of(dashboard)
    target = manifest["share_target"]

    assert target["method"] == "GET"
    assert urljoin(address, target["action"]) == "http://testserver/applications/new"
    # What a phone does with a share: the page's title, its text and its link, as a query.
    params = target["params"]
    shared = {
        params["title"]: "Simulation Engineer at Nordwind Robotics | Stellenwerk",
        params["text"]: "You build simulations of towing tanks.",
        params["url"]: "https://www.stellenwerk.example/jobs/4711",
    }
    page = dashboard.get(urljoin(address, target["action"]), params=shared).text

    assert field(page, "company") == "Nordwind Robotics"
    assert field(page, "role") == "Simulation Engineer"
    assert field(page, "text") == "You build simulations of towing tanks."
    assert field(page, "link") == "https://www.stellenwerk.example/jobs/4711"


def png_size(content: bytes) -> tuple[int, int]:
    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", content[16:24])
    return width, height


def test_its_icons_are_served_in_the_two_sizes_a_phone_asks_for(dashboard: TestClient) -> None:
    address, manifest = manifest_of(dashboard)
    sizes = set()

    for icon in manifest["icons"]:
        served = dashboard.get(urljoin(address, icon["src"]))
        assert served.status_code == 200
        assert served.headers["content-type"] == icon["type"] == "image/png"
        width, height = png_size(served.content)
        assert icon["sizes"] == f"{width}x{height}"
        sizes.add(icon["sizes"])

    assert sizes == {"192x192", "512x512"}
    assert {icon.get("purpose", "any") for icon in manifest["icons"]} == {"any", "maskable"}


def test_no_service_worker_is_registered(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    write_application(
        "nordwind-robotics/2026-09-18-simulation-engineer",
        "company: Nordwind Robotics\nrole: Simulation Engineer\ndate: 2026-09-18\nstatus: todo\n",
    )
    pages = [
        "/",
        "/applications/new",
        "/applications/nordwind-robotics/2026-09-18-simulation-engineer",
        "/proposals",
        "/sources",
        "/documents",
    ]

    for page in pages:
        served = dashboard.get(page)
        assert served.status_code == 200
        assert "serviceWorker" not in served.text
    _, manifest = manifest_of(dashboard)
    assert "serviceworker" not in manifest
