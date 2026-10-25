"""What the pages show while a slow action runs: the forms that wait on the network or on the
model say so, through the HTTP surface and, where there is one, in the real browser.

Every page here is invented, as is every host.
"""

import html
import re
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Page, Request, sync_playwright
from starlette.applications import Starlette
from starlette.testclient import TestClient

from conftest import Pages, RememberedCommits, ScriptedModel, SettableClock
from jobs.web import create_app
from test_fetching import no_chromium
from test_rederivation import register_institute
from test_sources import FOLLOW_TO_LISTING, FOLLOW_TO_POSTINGS


def busy_forms(page: str) -> dict[str, tuple[str, str]]:
    """The label and the note of every form that says it is slow, by where it posts to."""
    found = {}
    for attributes in re.findall(r"<form\b([^>]*\bdata-busy=[^>]*)>", page):
        said = dict(re.findall(r'([\w-]+)="([^"]*)"', attributes))
        path = re.sub(r"^https?://[^/]+", "", said["action"])
        found[path] = (
            html.unescape(said["data-busy"]),
            html.unescape(said.get("data-busy-note", "")),
        )
    return found


def test_the_forms_that_wait_on_the_network_or_the_model_say_so(
    dashboard: TestClient, network: Pages, model: ScriptedModel
) -> None:
    register_institute(dashboard, network, model)

    on_sources = busy_forms(dashboard.get("/sources").text)
    on_source = busy_forms(dashboard.get("/sources/hafenstadt").text)

    # Not the Register form: it answers at once, and the exploring is done in the background.
    assert on_sources == {
        "/poll": (
            "Polling…",
            "Walking every enabled Source in turn. This can take a few minutes.",
        ),
    }
    assert on_source == {
        "/sources/hafenstadt/poll": ("Polling…", ""),
        "/sources/hafenstadt/rederive": (
            "Deriving…",
            "Exploring the site again. Nothing is written until you accept the difference.",
        ),
    }


DERIVE_AGAIN = "form[data-busy='Deriving…']"

# The form's button and note as they stand, after an event the page would get from the browser.
# A submit made here is only the event: nothing is sent, so the page stays to be looked at.
AFTER = """([selector, event]) => {
    const form = document.querySelector(selector);
    const went = event === 'back'
        ? window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))
        : form.dispatchEvent(new SubmitEvent('submit', {bubbles: true, cancelable: true}));
    const button = form.querySelector('button');
    return {
        went: went,
        disabled: button.disabled,
        label: button.textContent,
        busy: button.getAttribute('aria-busy'),
        note: (form.querySelector('[role="status"]')
            || document.querySelector('[role="status"]')).textContent,
    };
}"""


@contextmanager
def in_the_browser(app: Starlette) -> Iterator[tuple[Page, str]]:
    """A page of the real browser, and where the app is served for it."""
    listening = socket.socket()
    listening.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{listening.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=[listening]), daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            yield browser.new_page(), origin
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


def test_a_rederivation_under_way_shows_that_it_is_and_ends_on_the_difference(
    data_dir: Path,
    clock: SettableClock,
    network: Pages,
    committer: RememberedCommits,
    model: ScriptedModel,
) -> None:
    if reason := no_chromium():
        pytest.skip(reason)
    app = create_app(
        data_dir=data_dir,
        clock=clock,
        network=network,
        committer=committer,
        model=model,
        sleep=lambda seconds: None,
    )
    register_institute(TestClient(app), network, model)
    model.answers = [FOLLOW_TO_LISTING, FOLLOW_TO_POSTINGS]
    with in_the_browser(app) as (page, origin):
        page.goto(f"{origin}/sources/hafenstadt")

        waiting = {
            "disabled": True,
            "label": "Deriving…",
            "busy": "true",
            "note": "Exploring the site again. Nothing is written until you accept the difference.",
        }
        assert page.evaluate(AFTER, [DERIVE_AGAIN, "submit"]) == {"went": True, **waiting}
        # A second submit while the first is under way is stopped.
        assert page.evaluate(AFTER, [DERIVE_AGAIN, "submit"]) == {"went": False, **waiting}
        # The back button brings a kept page back as it was left, and it must not stay busy.
        assert page.evaluate(AFTER, [DERIVE_AGAIN, "back"]) == {
            "went": True,
            "disabled": False,
            "label": "Derive again",
            "busy": None,
            "note": "",
        }

        page.click(f"{DERIVE_AGAIN} button")
        page.wait_for_url(f"{origin}/sources/hafenstadt/rederive")
        assert "The config, derived again" in page.content()


POLL_ALL = "form[data-busy='Polling…']"


def test_the_sources_page_reloads_itself_but_not_under_the_users_hands(
    data_dir: Path,
    clock: SettableClock,
    network: Pages,
    committer: RememberedCommits,
    model: ScriptedModel,
) -> None:
    if reason := no_chromium():
        pytest.skip(reason)
    app = create_app(
        data_dir=data_dir,
        clock=clock,
        network=network,
        committer=committer,
        model=model,
        sleep=lambda seconds: None,
    )
    dashboard = TestClient(app)
    register_institute(dashboard, network, model)
    # Nothing runs this one, so it waits for as long as the test looks.
    dashboard.post("/sources", data={"url": "https://anker.example/", "label": "Anker"})
    with in_the_browser(app) as (page, origin):
        asked_for: list[str] = []

        def remember(request: Request) -> None:
            if request.is_navigation_request():
                asked_for.append(request.url)

        page.on("request", remember)
        # The page's own clock, so that a turn passes when the test says so.
        page.clock.install(time=0)
        # Held two seconds in, in seconds, before the first turn: no turn passes by itself. Held
        # before the page loads, so that however long it takes cannot carry the clock past that.
        page.clock.pause_at(2)
        page.goto(f"{origin}/sources")

        # With a field focused a turn passes, and with a busy form another: no reload.
        page.focus("#label")
        page.clock.run_for(3000)
        page.evaluate("document.activeElement.blur()")
        assert page.evaluate(AFTER, [POLL_ALL, "submit"])["busy"] == "true"
        page.clock.run_for(3000)
        assert page.evaluate(AFTER, [POLL_ALL, "back"])["busy"] is None

        # Had one of the held turns reloaded the page, it would have been asked for by now.
        assert asked_for == [f"{origin}/sources"]

        with page.expect_navigation():
            page.clock.run_for(3000)

        assert asked_for[1] == f"{origin}/sources?by-itself=1"
        # Only the server was to know: reloading by hand from here is looking.
        assert page.url == f"{origin}/sources"
        assert "Anker" in page.inner_text("#registrations")
