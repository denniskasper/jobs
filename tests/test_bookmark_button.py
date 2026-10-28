"""The bookmark button: a link on the page that creates an Application, which, kept in the
bookmarks bar and pressed on another site's page, opens that page with the other page's title,
address and selection.

The browser tests skip where Chromium cannot start, as the rendering tests do. Every page, title
and Posting here is invented.
"""

import html
import re
from typing import cast
from urllib.parse import unquote

import pytest
from playwright.sync_api import Page
from starlette.applications import Starlette
from starlette.testclient import TestClient

from jobs.web import bookmark as the_button
from test_bringing_in import LINK, field, opened
from test_fetching import no_chromium
from test_waiting import in_the_browser

TITLE = "Simulation Engineer at Nordwind Robotics | Stellenwerk"
POSTING_PAGE = f"""\
<!doctype html>
<html><head><title>{TITLE}</title></head>
<body>
  <h1>Simulation Engineer</h1>
  <p id="description">You build simulations of towing tanks.</p>
  <p id="more">You measure before you believe.</p>
</body></html>
"""


def bookmark(page: str) -> str:
    """Where the bookmark button on a page leads, as the browser keeps it."""
    (href,) = re.findall(r'<a[^>]*class="bookmark"[^>]*href="([^"]*)"', page)
    return html.unescape(href)


def test_the_page_that_creates_an_application_carries_the_button_and_how_to_keep_it(
    dashboard: TestClient,
) -> None:
    page = opened(dashboard)

    assert bookmark(page).startswith("javascript:")
    assert "Drag" in page and "bookmarks bar" in page


@pytest.mark.parametrize(
    "base_url", ["http://testserver", "https://jobs.example.org", "http://192.0.2.7:8000"]
)
def test_the_buttons_address_is_the_apps_own_as_the_request_reached_it(
    dashboard: TestClient, base_url: str
) -> None:
    reached = TestClient(dashboard.app, base_url=base_url)

    code = unquote(bookmark(opened(reached)).removeprefix("javascript:"))

    assert f'"{base_url}/applications/new"' in code


def test_a_selection_that_was_cut_is_said_to_be(dashboard: TestClient) -> None:
    page = opened(dashboard, title=TITLE, link=LINK, text="You build simulations", cut="1")

    assert field(page, "text") == "You build simulations"
    assert "was cut" in page


def test_a_selection_that_was_not_cut_says_nothing_of_it(dashboard: TestClient) -> None:
    assert "was cut" not in opened(dashboard, title=TITLE, link=LINK, text="You build")


def press(page: Page, button: str) -> None:
    """Press a bookmark kept in the bookmarks bar, on the page that is open."""
    page.evaluate("button => { location.href = button }", button)


def on_the_posting(page: Page, origin: str) -> str:
    """The button, read off the app's page, with the invented Posting's page open after it."""
    page.goto(f"{origin}/applications/new")
    button = bookmark(page.content())
    page.route(
        f"{LINK}**", lambda route: route.fulfill(body=POSTING_PAGE, content_type="text/html")
    )
    page.goto(LINK)
    return button


def select(page: Page, selector: str) -> None:
    page.evaluate(
        """selector => {
            const range = document.createRange();
            range.selectNodeContents(document.querySelector(selector));
            getSelection().removeAllRanges();
            getSelection().addRange(range);
        }""",
        selector,
    )


def test_pressed_on_a_page_with_a_selection_it_opens_the_form_filled_in(
    dashboard: TestClient,
) -> None:
    if reason := no_chromium():
        pytest.skip(reason)
    with in_the_browser(cast(Starlette, dashboard.app)) as (page, origin):
        button = on_the_posting(page, origin)
        select(page, "#description")

        press(page, button)
        page.wait_for_url(f"{origin}/applications/new?*")

        assert page.input_value("#link") == LINK
        assert page.input_value("#company") == "Nordwind Robotics"
        assert page.input_value("#role") == "Simulation Engineer"
        assert page.input_value("#text") == "You build simulations of towing tanks."
        assert "was cut" not in page.content()


def test_a_selection_too_long_for_an_address_arrives_cut_and_the_form_says_so(
    dashboard: TestClient,
) -> None:
    if reason := no_chromium():
        pytest.skip(reason)
    long = "Sie messen, bevor Sie glauben — 🌊. " * 1000
    with in_the_browser(cast(Starlette, dashboard.app)) as (page, origin):
        button = on_the_posting(page, origin)
        page.evaluate("long => { document.querySelector('#more').textContent = long }", long)
        select(page, "#more")

        press(page, button)
        page.wait_for_url(f"{origin}/applications/new?*")

        arrived = page.input_value("#text")
        assert 1000 < len(arrived) < len(long)
        assert long.startswith(arrived)
        assert len(page.url) <= the_button.ADDRESS_LIMIT
        assert page.input_value("#link") == LINK
        assert "was cut" in page.content()
