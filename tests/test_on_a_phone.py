"""Every page at the width of a phone, in the real browser: nothing reaches past the edge of the
screen, and the pages stay within reach along its bottom edge.

Skips where Chromium cannot start, as the rendering tests do. Every Application, Posting and
Source here is invented, as is every host.
"""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright
from starlette.testclient import TestClient

from conftest import Pages, ScriptedModel, WriteApplication, register
from test_fetching import no_chromium
from test_proposals import write_posting
from test_sources import FOLLOW_TO_LISTING, FOLLOW_TO_POSTINGS, INSTITUTE, serve_institute

# Narrower than any phone sold in years, so what fits here fits them all.
WIDTH, HEIGHT = 320, 640

FOLDER = "hafenstadt-institute-of-technology/2026-09-01-doctoral-researcher"
RECORD = (
    "company: Hafenstadt Institute of Technology and Maritime Engineering\n"
    "role: Doctoral researcher in experimental fluid dynamics and turbulence modelling\n"
    "date: 2026-09-01\nstatus: todo\n"
)


@pytest.fixture
def phone() -> Iterator[Page]:
    if reason := no_chromium():
        pytest.skip(reason)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            yield browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        finally:
            browser.close()


def show(phone: Page, dashboard: TestClient, path: str) -> None:
    """Put a page of the app on the phone, with the stylesheet it links to and nothing fetched
    from anywhere: the type falls back to the system's, which is the wider of the two."""
    markup = dashboard.get(path).text
    stylesheet = dashboard.get("/static/dashboard.css").text
    markup = re.sub(r'<link rel="stylesheet" href="[^"]*dashboard\.css[^"]*">', "", markup)
    markup = re.sub(r"<link[^>]*fonts\.g[^>]*>", "", markup)
    phone.set_content(markup.replace("</head>", f"<style>{stylesheet}</style></head>"))


def past_the_edge(phone: Page) -> int:
    """How far the page can be pushed sideways, in pixels."""
    past: int = phone.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    return past


def fill(
    dashboard: TestClient,
    data_dir: Path,
    network: Pages,
    model: ScriptedModel,
    write_application: WriteApplication,
) -> None:
    """Something on every page: an Application, a Source with its Postings, and a Proposal."""
    write_application(FOLDER, RECORD)
    serve_institute(network)
    model.answers = [FOLLOW_TO_LISTING, FOLLOW_TO_POSTINGS]
    register(dashboard, INSTITUTE, label="Hafenstadt Fluid Dynamics")
    dashboard.post("/poll")
    write_posting(data_dir, "doctoral-researcher")


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/proposals",
        "/proposals/left-out",
        "/sources",
        "/sources/hafenstadt-fluid-dynamics",
        "/documents",
        f"/applications/{FOLDER}",
    ],
)
def test_no_page_reaches_past_the_edge_of_a_phone(
    path: str,
    phone: Page,
    dashboard: TestClient,
    data_dir: Path,
    network: Pages,
    model: ScriptedModel,
    write_application: WriteApplication,
) -> None:
    fill(dashboard, data_dir, network, model, write_application)

    show(phone, dashboard, path)

    assert past_the_edge(phone) == 0


def test_the_pages_are_along_the_bottom_edge_of_a_phone_all_four_in_view(
    phone: Page, dashboard: TestClient
) -> None:
    show(phone, dashboard, "/")

    tabs = phone.evaluate(
        "[...document.querySelectorAll('.bar > .tabs .tab')].map(tab => {"
        "  const box = tab.getBoundingClientRect();"
        "  return [tab.textContent.trim(), box.left, box.right, box.bottom, box.height];"
        "})"
    )

    assert [name for name, *_ in tabs] == ["Applications", "Proposals", "Sources", "Documents"]
    for _, left, right, bottom, height in tabs:
        assert left >= 0
        assert right <= WIDTH
        assert bottom == HEIGHT
        # Big enough for a finger.
        assert height >= 44


def test_what_can_be_done_on_a_page_is_in_view_on_a_phone(
    phone: Page,
    dashboard: TestClient,
    data_dir: Path,
    network: Pages,
    model: ScriptedModel,
    write_application: WriteApplication,
) -> None:
    fill(dashboard, data_dir, network, model, write_application)

    show(phone, dashboard, "/sources/hafenstadt-fluid-dynamics")

    for label in ["Poll now", "Derive again", "Disable"]:
        box = phone.get_by_role("button", name=label).bounding_box()
        assert box is not None
        assert box["x"] >= 0
        assert box["x"] + box["width"] <= WIDTH
        assert box["height"] >= 44
