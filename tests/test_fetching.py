"""Fetching: the hop walk, driven over real HTTP against a site served from this machine.

The site is invented and its pages link to each other relatively, so a walk never leaves
127.0.0.1. Its shape is the one verified against a university: an institute page that only
signposts a central listing, whose posting pages carry the structured record.

The tests at the end render the site in the real browser, where there is one: the image has it,
and `playwright install --with-deps --only-shell chromium` brings it to any other machine.
"""

import functools
import json
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jobs.extraction import FilterRule, Hop, Render, Selectors, SourceConfig, Strategy
from jobs.fetching import (
    Chromium,
    HttpNetwork,
    NetworkUnavailable,
    NoBrowser,
    Ready,
    Response,
    Walked,
    walk,
)


@dataclass(frozen=True)
class Served:
    body: bytes = b""
    status: int = 200
    location: str | None = None


@dataclass
class Site:
    """A local web server whose pages a test writes, and which remembers what was asked of it."""

    origin: str = ""
    pages: dict[str, Served] = field(default_factory=dict)
    requested: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)

    def url(self, path: str) -> str:
        return self.origin + path

    def serve(self, path: str, markup: str) -> None:
        self.pages[path] = Served(body=markup.encode())

    def answer(self, path: str, status: int) -> None:
        self.pages[path] = Served(status=status)

    def redirect(self, path: str, location: str) -> None:
        self.pages[path] = Served(status=302, location=location)


@pytest.fixture
def site() -> Iterator[Site]:
    served = Site()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            served.requested.append(self.path)
            served.agents.append(self.headers.get("User-Agent", ""))
            page = served.pages.get(self.path, Served(status=404))
            self.send_response(page.status)
            if page.location:
                self.send_header("Location", page.location)
            self.send_header("Content-Length", str(len(page.body)))
            self.end_headers()
            self.wfile.write(page.body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    served.origin = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield served
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture(autouse=True)
def only_this_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """A page that links off this machine fails its test instead of reaching the internet."""
    connect = socket.socket.connect

    def guarded(self: socket.socket, address: tuple[str, int]) -> None:
        assert address[0] == "127.0.0.1", f"a test tried to reach {address[0]}"
        connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@dataclass
class Rendering:
    """A Browser that shows the markup a test says a page's script ends up with. A page it was
    told nothing about never gets any further than the markup the site serves."""

    site: Site
    scripted: dict[str, list[str]] = field(default_factory=dict)
    rendered: list[str] = field(default_factory=list)

    def fills_in(self, path: str, *markup: str) -> None:
        """What the page shows each time it is looked at, the last for good."""
        self.scripted[path] = list(markup)

    def render(self, url: str, ready: Ready) -> Response:
        path = url.removeprefix(self.site.origin)
        self.rendered.append(path)
        served = self.site.pages.get(path, Served(status=404))
        shown = [served.body.decode(), *self.scripted.get(path, [])]
        for markup in shown:
            response = Response(url=url, status=served.status, body=markup.encode())
            if ready(response):
                break
        return response


def walk_without_waiting(
    url: str, config: SourceConfig, browser: Rendering | None = None
) -> Walked:
    return walk(url, config, HttpNetwork(browser=browser), sleep=lambda seconds: None)


def listing(*links: str) -> str:
    rows = "".join(f'<li><a href="{link}">A position</a></li>' for link in links)
    return f"<html><body><ul>{rows}</ul></body></html>"


def posting_page(role: str, reference: str) -> str:
    record = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": role,
        "description": f"<p>ID {reference}</p><p>Work on {role.lower()}.</p>",
        "hiringOrganization": {"@type": "Organization", "name": "Nordlicht Institute"},
    }
    return (
        f'<html><head><script type="application/ld+json">{json.dumps(record)}</script></head>'
        f"<body><h1>{role}</h1></body></html>"
    )


def signpost(link: str) -> str:
    return (
        "<html><body><p>Our openings are advertised on the"
        f' <a href="{link}">central portal</a>.</p><a href="/imprint">Imprint</a></body></html>'
    )


class Stopwatch:
    """Time that passes only while something sleeps."""

    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def timer(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


ONE_HOP = SourceConfig(strategy=Strategy.STRUCTURED_RECORD, hops=(Hop(follow="/posting/"),))


def test_a_single_hop_source_produces_postings_from_its_listing_page(site: Site) -> None:
    site.serve("/careers/", listing("posting/1", "posting/2"))
    site.serve("/careers/posting/1", posting_page("Doctoral Researcher in Wave Dynamics", "11M8"))
    site.serve("/careers/posting/2", posting_page("Research Software Engineer", "12K3"))

    walked = walk_without_waiting(site.url("/careers/"), ONE_HOP)

    assert [(posting.role, posting.url) for posting in walked.postings] == [
        ("Doctoral Researcher in Wave Dynamics", site.url("/careers/posting/1")),
        ("Research Software Engineer", site.url("/careers/posting/2")),
    ]
    assert walked.errors == []


def test_a_multi_hop_source_follows_the_signpost_to_the_listing_and_on(site: Site) -> None:
    site.serve("/institute/vacancies", signpost("/portal/listing"))
    site.serve("/portal/listing", listing("/portal/posting/7"))
    site.serve("/portal/posting/7", posting_page("Postdoctoral Researcher in Turbulence", "07M8"))
    config = SourceConfig(
        strategy=Strategy.STRUCTURED_RECORD,
        hops=(Hop(follow="/portal/listing$"), Hop(follow="/portal/posting/")),
    )

    walked = walk_without_waiting(site.url("/institute/vacancies"), config)

    assert [(posting.role, posting.url) for posting in walked.postings] == [
        ("Postdoctoral Researcher in Turbulence", site.url("/portal/posting/7"))
    ]
    assert site.requested == ["/institute/vacancies", "/portal/listing", "/portal/posting/7"]


def test_a_source_filter_rule_reduces_the_result_and_says_what_it_removed(site: Site) -> None:
    site.serve("/careers/", listing("posting/1", "posting/2"))
    site.serve("/careers/posting/1", posting_page("Doctoral Researcher in Wave Dynamics", "11M8"))
    site.serve("/careers/posting/2", posting_page("Research Software Engineer", "12K3"))
    config = SourceConfig(
        strategy=Strategy.STRUCTURED_RECORD,
        hops=(Hop(follow="/posting/"),),
        filters=(FilterRule(field="description", pattern=r"\bID \w+M8\b"),),
    )

    walked = walk_without_waiting(site.url("/careers/"), config)

    assert [posting.role for posting in walked.postings] == ["Doctoral Researcher in Wave Dynamics"]
    [rejected] = walked.rejected
    assert rejected.posting.role == "Research Software Engineer"
    assert rejected.reason == (
        "its description does not match \\bID \\w+M8\\b, which this Source only keeps"
    )


def test_a_posting_page_that_fails_does_not_abandon_the_walk(site: Site) -> None:
    site.serve("/careers/", listing("posting/1", "posting/2"))
    site.answer("/careers/posting/1", 500)
    site.serve("/careers/posting/2", posting_page("Research Software Engineer", "12K3"))

    walked = walk_without_waiting(site.url("/careers/"), ONE_HOP)

    assert [posting.role for posting in walked.postings] == ["Research Software Engineer"]
    [error] = walked.errors
    assert error.url == site.url("/careers/posting/1")
    assert error.status == 500


def test_a_rate_limit_is_told_apart_and_the_host_is_asked_nothing_more(site: Site) -> None:
    site.serve("/careers/", listing("posting/1", "posting/2"))
    site.answer("/careers/posting/1", 429)
    site.serve("/careers/posting/2", posting_page("Research Software Engineer", "12K3"))

    walked = walk_without_waiting(site.url("/careers/"), ONE_HOP)

    [error] = walked.errors
    assert error.status == 429
    assert "rate limit" in error.reason
    assert site.requested == ["/careers/", "/careers/posting/1"]


def test_a_redesign_shows_as_a_listing_whose_links_no_longer_lead_onward(site: Site) -> None:
    site.serve("/careers/", listing("/jobs/1", "/jobs/2"))

    walked = walk_without_waiting(site.url("/careers/"), ONE_HOP)

    assert walked.postings == []
    [error] = walked.errors
    assert error.url == site.url("/careers/")
    assert error.status is None
    assert error.reason == "no link on the page matches /posting/, so the walk ends here"


def test_a_page_that_has_moved_away_is_reported_as_gone(site: Site) -> None:
    walked = walk_without_waiting(site.url("/careers/"), ONE_HOP)

    [error] = walked.errors
    assert error.status == 404
    assert "no longer there" in error.reason


def test_a_host_that_cannot_be_reached_is_an_error_not_a_crash(site: Site) -> None:
    site.serve("/careers/", listing("http://127.0.0.1:1/posting/1", "posting/2"))
    site.serve("/careers/posting/2", posting_page("Research Software Engineer", "12K3"))

    walked = walk_without_waiting(site.url("/careers/"), ONE_HOP)

    assert [posting.role for posting in walked.postings] == ["Research Software Engineer"]
    [error] = walked.errors
    assert error.url == "http://127.0.0.1:1/posting/1"
    assert error.status is None
    assert "could not be reached" in error.reason


def test_a_posting_page_that_cannot_be_read_is_reported_with_the_reason(site: Site) -> None:
    site.serve("/careers/", listing("posting/1"))
    site.serve("/careers/posting/1", posting_page("Research Software Engineer", "12K3")[:-30])

    walked = walk_without_waiting(site.url("/careers/"), ONE_HOP)

    assert walked.postings == []
    [error] = walked.errors
    assert error.url == site.url("/careers/posting/1")
    assert error.reason == "the page is truncated: it ends before its markup does"


def test_redirects_are_followed_and_the_final_url_is_recorded(site: Site) -> None:
    site.serve("/careers/", listing("posting/1"))
    site.redirect("/careers/posting/1", "/portal/doctoral-researcher")
    site.serve("/portal/doctoral-researcher", posting_page("Doctoral Researcher", "11M8"))

    walked = walk_without_waiting(site.url("/careers/"), ONE_HOP)

    [posting] = walked.postings
    assert posting.url == site.url("/portal/doctoral-researcher")


def test_links_are_resolved_against_where_a_redirect_ended(site: Site) -> None:
    site.redirect("/careers", "/portal/openings/")
    site.serve("/portal/openings/", listing("posting/1"))
    site.serve("/portal/openings/posting/1", posting_page("Doctoral Researcher", "11M8"))

    walked = walk_without_waiting(site.url("/careers"), ONE_HOP)

    assert [posting.role for posting in walked.postings] == ["Doctoral Researcher"]


def test_requests_to_a_source_are_spaced_by_its_interval(site: Site) -> None:
    site.serve("/careers/", listing("posting/1", "posting/2"))
    site.serve("/careers/posting/1", posting_page("Doctoral Researcher", "11M8"))
    site.serve("/careers/posting/2", posting_page("Research Software Engineer", "12K3"))
    config = SourceConfig(
        strategy=Strategy.STRUCTURED_RECORD, hops=(Hop(follow="/posting/"),), interval=5.0
    )
    stopwatch = Stopwatch()

    walked = walk(
        site.url("/careers/"),
        config,
        HttpNetwork(),
        sleep=stopwatch.sleep,
        timer=stopwatch.timer,
    )

    assert len(walked.postings) == 2
    assert stopwatch.slept == [5.0, 5.0]


def test_a_posting_reached_through_two_listings_is_fetched_once(site: Site) -> None:
    site.serve(
        "/institute/vacancies",
        "<html><body>"
        '<a href="/portal/listing/research">Research</a>'
        '<a href="/portal/listing/all">All</a>'
        "</body></html>",
    )
    site.serve("/portal/listing/research", listing("/portal/posting/7"))
    site.serve("/portal/listing/all", listing("/portal/posting/7", "/portal/posting/8"))
    site.serve("/portal/posting/7", posting_page("Postdoctoral Researcher in Turbulence", "07M8"))
    site.serve("/portal/posting/8", posting_page("Laboratory Engineer", "08K3"))
    config = SourceConfig(
        strategy=Strategy.STRUCTURED_RECORD,
        hops=(Hop(follow="/portal/listing/"), Hop(follow="/portal/posting/")),
    )

    walked = walk_without_waiting(site.url("/institute/vacancies"), config)

    assert [posting.role for posting in walked.postings] == [
        "Postdoctoral Researcher in Turbulence",
        "Laboratory Engineer",
    ]
    assert site.requested.count("/portal/posting/7") == 1


def test_a_link_with_characters_outside_ascii_is_followed(site: Site) -> None:
    site.serve("/careers/", listing("posting/strömungsmechanik 1"))
    site.serve(
        "/careers/posting/str%C3%B6mungsmechanik%201",
        posting_page("Doctoral Researcher in Fluid Mechanics", "11M8"),
    )

    walked = walk_without_waiting(site.url("/careers/"), ONE_HOP)

    assert walked.errors == []
    assert [posting.role for posting in walked.postings] == [
        "Doctoral Researcher in Fluid Mechanics"
    ]


def test_a_posting_page_that_no_longer_carries_a_posting_is_reported(site: Site) -> None:
    site.serve("/careers/", listing("posting/1"))
    site.serve("/careers/posting/1", "<html><body><h1>Doctoral Researcher</h1></body></html>")

    walked = walk_without_waiting(site.url("/careers/"), ONE_HOP)

    [error] = walked.errors
    assert error.url == site.url("/careers/posting/1")
    assert error.status is None
    assert error.reason == "the page was reached as a posting page but carries no Posting"


def test_two_links_that_redirect_to_one_page_yield_its_posting_once(site: Site) -> None:
    site.serve("/careers/", listing("posting/1", "posting/1-en"))
    site.redirect("/careers/posting/1", "/portal/doctoral-researcher")
    site.redirect("/careers/posting/1-en", "/portal/doctoral-researcher")
    site.serve("/portal/doctoral-researcher", posting_page("Doctoral Researcher", "11M8"))

    walked = walk_without_waiting(site.url("/careers/"), ONE_HOP)

    assert [posting.role for posting in walked.postings] == ["Doctoral Researcher"]


def test_a_filter_rule_on_a_field_the_posting_lacks_removes_it(site: Site) -> None:
    site.serve("/careers/", listing("posting/1"))
    site.serve("/careers/posting/1", posting_page("Doctoral Researcher", "11M8"))
    config = SourceConfig(
        strategy=Strategy.STRUCTURED_RECORD,
        hops=(Hop(follow="/posting/"),),
        filters=(FilterRule(field="identifier", pattern="M8$|^$"),),
    )

    walked = walk_without_waiting(site.url("/careers/"), config)

    assert walked.postings == []
    [rejected] = walked.rejected
    assert (
        rejected.reason == "it has no identifier, and this Source only keeps those matching M8$|^$"
    )


SHELL = "<html><body><div id='app'></div><script src='/bundle.js'></script></body></html>"


def test_a_listing_with_no_candidates_in_its_markup_is_retried_in_a_browser(site: Site) -> None:
    site.serve("/careers/", SHELL)
    site.serve("/careers/posting/1", posting_page("Doctoral Researcher", "11M8"))
    browser = Rendering(site)
    browser.fills_in("/careers/", listing("posting/1"))

    walked = walk_without_waiting(site.url("/careers/"), ONE_HOP, browser)

    assert [posting.role for posting in walked.postings] == ["Doctoral Researcher"]
    assert walked.errors == []
    assert browser.rendered == ["/careers/"]


def test_a_page_whose_markup_carries_candidates_is_never_rendered(site: Site) -> None:
    site.serve("/careers/", listing("posting/1"))
    site.serve("/careers/posting/1", posting_page("Doctoral Researcher", "11M8"))
    browser = Rendering(site)

    walk_without_waiting(site.url("/careers/"), ONE_HOP, browser)

    assert browser.rendered == []


def test_a_hop_in_browser_render_mode_is_rendered_without_a_static_fetch_first(site: Site) -> None:
    site.serve("/careers/", SHELL)
    site.serve("/careers/posting/1", posting_page("Doctoral Researcher", "11M8"))
    browser = Rendering(site)
    browser.fills_in("/careers/", listing("posting/1"))
    config = SourceConfig(
        strategy=Strategy.STRUCTURED_RECORD,
        hops=(Hop(follow="/posting/", render=Render.BROWSER),),
    )

    walked = walk_without_waiting(site.url("/careers/"), config, browser)

    assert [posting.role for posting in walked.postings] == ["Doctoral Researcher"]
    assert site.requested == ["/careers/posting/1"]
    assert browser.rendered == ["/careers/"]


def test_rendering_waits_until_the_links_the_hop_follows_have_appeared(site: Site) -> None:
    site.serve("/careers/", SHELL)
    site.serve("/careers/posting/1", posting_page("Doctoral Researcher", "11M8"))
    browser = Rendering(site)
    browser.fills_in("/careers/", listing("/imprint"), listing("/imprint", "posting/1"))
    config = SourceConfig(
        strategy=Strategy.STRUCTURED_RECORD,
        hops=(Hop(follow="/posting/", render=Render.BROWSER),),
    )

    walked = walk_without_waiting(site.url("/careers/"), config, browser)

    assert [posting.role for posting in walked.postings] == ["Doctoral Researcher"]


def test_posting_pages_in_browser_render_mode_are_rendered_until_a_posting_appears(
    site: Site,
) -> None:
    site.serve("/careers/", listing("posting/1"))
    site.serve("/careers/posting/1", SHELL)
    browser = Rendering(site)
    browser.fills_in(
        "/careers/posting/1",
        "<html><body>Loading</body></html>",
        posting_page("Doctoral Researcher", "11M8"),
    )
    config = SourceConfig(
        strategy=Strategy.STRUCTURED_RECORD, hops=(Hop(follow="/posting/"),), render=Render.BROWSER
    )

    walked = walk_without_waiting(site.url("/careers/"), config, browser)

    assert [posting.role for posting in walked.postings] == ["Doctoral Researcher"]
    assert site.requested == ["/careers/"]


def test_posting_pages_read_by_selectors_are_rendered_until_they_yield_a_posting(
    site: Site,
) -> None:
    site.serve("/careers/", listing("posting/1"))
    site.serve("/careers/posting/1", SHELL)
    browser = Rendering(site)
    browser.fills_in(
        "/careers/posting/1",
        "<html><body><h1 class='opening'></h1>Loading</body></html>",
        "<html><body><h1 class='opening'>Doctoral Researcher</h1>"
        "<p class='text'>You run the wave flume.</p></body></html>",
        "<html><body><h1 class='opening'>Gone again</h1><p class='text'>Late.</p></body></html>",
    )
    config = SourceConfig(
        strategy=Strategy.SELECTORS,
        selectors=Selectors(role="h1.opening", description=".text"),
        company="Nordlicht Institute",
        hops=(Hop(follow="/posting/"),),
        render=Render.BROWSER,
    )

    walked = walk_without_waiting(site.url("/careers/"), config, browser)

    assert [posting.role for posting in walked.postings] == ["Doctoral Researcher"]
    assert walked.errors == []


def test_a_listing_read_as_it_is_falls_back_to_the_browser_too(site: Site) -> None:
    site.serve("/careers/", SHELL)
    browser = Rendering(site)
    browser.fills_in("/careers/", posting_page("Doctoral Researcher", "11M8"))

    walked = walk_without_waiting(
        site.url("/careers/"), SourceConfig(strategy=Strategy.STRUCTURED_RECORD), browser
    )

    assert [posting.role for posting in walked.postings] == ["Doctoral Researcher"]
    assert walked.errors == []


def test_a_rendered_listing_that_never_shows_a_link_onward_says_so(site: Site) -> None:
    site.serve("/careers/", SHELL)
    browser = Rendering(site)
    browser.fills_in("/careers/", listing("/imprint"))

    walked = walk_without_waiting(site.url("/careers/"), ONE_HOP, browser)

    assert walked.postings == []
    [error] = walked.errors
    assert error.url == site.url("/careers/")
    assert error.reason == (
        "no link on the page matches /posting/, even once it was rendered in a browser, "
        "so the walk ends here"
    )


def test_a_rendered_posting_page_on_which_no_posting_appears_says_so(site: Site) -> None:
    site.serve("/careers/", SHELL)
    config = SourceConfig(strategy=Strategy.STRUCTURED_RECORD, render=Render.BROWSER)

    walked = walk_without_waiting(site.url("/careers/"), config, Rendering(site))

    [error] = walked.errors
    assert error.reason == "no Posting appeared on the page while it was rendered in a browser"


def test_a_browser_that_cannot_render_the_page_is_an_error_not_a_crash(site: Site) -> None:
    class Broken:
        def render(self, url: str, ready: Ready) -> Response:
            raise NetworkUnavailable(f"{url} could not be rendered: the browser crashed")

    site.serve("/careers/", SHELL)
    config = SourceConfig(
        strategy=Strategy.STRUCTURED_RECORD,
        hops=(Hop(follow="/posting/", render=Render.BROWSER),),
    )

    walked = walk(site.url("/careers/"), config, HttpNetwork(browser=Broken()))

    [error] = walked.errors
    assert error.fetch
    assert "the browser crashed" in error.reason


def test_a_fallback_the_browser_fails_at_is_not_kept_quiet(site: Site) -> None:
    class Broken:
        def render(self, url: str, ready: Ready) -> Response:
            raise NetworkUnavailable(f"{url} could not be rendered: the browser crashed")

    site.serve("/careers/", SHELL)
    network = HttpNetwork(browser=Broken())

    listing_read = walk(
        site.url("/careers/"), SourceConfig(strategy=Strategy.STRUCTURED_RECORD), network
    )
    hop_followed = walk(site.url("/careers/"), ONE_HOP, network)

    assert "the browser crashed" in listing_read.errors[0].reason
    assert "no link on the page matches /posting/" in hop_followed.errors[0].reason
    assert "the browser crashed" in hop_followed.errors[0].reason


def test_without_a_browser_a_page_in_browser_render_mode_says_there_is_none(site: Site) -> None:
    site.serve("/careers/", SHELL)
    config = SourceConfig(strategy=Strategy.STRUCTURED_RECORD, render=Render.BROWSER)

    walked = walk_without_waiting(site.url("/careers/"), config)

    [error] = walked.errors
    assert error.fetch
    assert "no browser" in error.reason


def test_rendering_is_paced_like_any_other_request(site: Site) -> None:
    site.serve("/careers/", SHELL)
    site.serve("/careers/posting/1", posting_page("Doctoral Researcher", "11M8"))
    browser = Rendering(site)
    browser.fills_in("/careers/", listing("posting/1"))
    config = SourceConfig(
        strategy=Strategy.STRUCTURED_RECORD, hops=(Hop(follow="/posting/"),), interval=5.0
    )
    stopwatch = Stopwatch()

    walk(
        site.url("/careers/"),
        config,
        HttpNetwork(browser=browser),
        sleep=stopwatch.sleep,
        timer=stopwatch.timer,
    )

    assert stopwatch.slept == [5.0, 5.0]


@functools.cache
def no_chromium() -> str | None:
    """Why the real browser cannot be used on this machine, or None when it can."""
    try:
        Chromium(patience=0).render("http://127.0.0.1:1/", lambda response: True)
    except NoBrowser as none:
        return str(none)
    except NetworkUnavailable:
        # Nothing listens there, which only a browser that started could find out.
        return None
    return None


@pytest.fixture
def chromium() -> Chromium:
    if reason := no_chromium():
        pytest.skip(reason)
    return Chromium(patience=10.0)


def filled_in_after(milliseconds: int, markup: str) -> str:
    """A page that is an empty shell until its script has run."""
    return (
        "<html><body><div id='app'></div><script>"
        f"setTimeout(() => {{ document.getElementById('app').innerHTML = {json.dumps(markup)} }}, "
        f"{milliseconds})</script></body></html>"
    )


def test_chromium_shows_a_walk_the_links_a_script_fills_in(site: Site, chromium: Chromium) -> None:
    site.serve("/careers/", filled_in_after(300, '<a href="posting/1">A position</a>'))
    site.serve("/careers/posting/1", posting_page("Doctoral Researcher", "11M8"))

    began = time.monotonic()
    walked = walk(site.url("/careers/"), ONE_HOP, HttpNetwork(browser=chromium))

    assert [posting.role for posting in walked.postings] == ["Doctoral Researcher"]
    assert walked.errors == []
    assert time.monotonic() - began < chromium.patience


def test_chromium_gives_up_on_a_page_that_never_shows_its_postings(site: Site) -> None:
    if reason := no_chromium():
        pytest.skip(reason)
    site.serve("/careers/", filled_in_after(100, "<p>No results</p>"))
    config = SourceConfig(
        strategy=Strategy.STRUCTURED_RECORD,
        hops=(Hop(follow="/posting/", render=Render.BROWSER),),
    )

    walked = walk(site.url("/careers/"), config, HttpNetwork(browser=Chromium(patience=1.0)))

    [error] = walked.errors
    assert "even once it was rendered in a browser" in error.reason


def test_chromium_reports_the_status_and_where_a_redirect_ended(
    site: Site, chromium: Chromium
) -> None:
    site.redirect("/careers", "/portal/openings/")
    site.serve("/portal/openings/", listing("posting/1"))

    shown = chromium.render(site.url("/careers"), lambda response: True)
    gone = chromium.render(site.url("/nowhere"), lambda response: False)

    assert (shown.url, shown.status) == (site.url("/portal/openings/"), 200)
    assert b"posting/1" in shown.body
    assert gone.status == 404


def test_chromium_says_who_is_asking(site: Site, chromium: Chromium) -> None:
    site.serve("/careers/", listing("posting/1"))

    chromium.render(site.url("/careers/"), lambda response: True)

    assert set(site.agents) == {"jobs/0.1 (a personal job-search tool; polls a few pages a day)"}


def test_chromium_cannot_reach_a_host_that_is_not_there(chromium: Chromium) -> None:
    with pytest.raises(NetworkUnavailable, match="could not be rendered"):
        chromium.render("http://127.0.0.1:1/careers", lambda response: True)


def test_chromium_follows_a_script_that_takes_the_page_elsewhere(
    site: Site, chromium: Chromium
) -> None:
    site.serve(
        "/careers", "<html><body><script>location.replace('/portal/')</script></body></html>"
    )
    site.serve("/portal/", filled_in_after(100, '<a href="posting/1">A position</a>'))

    shown = chromium.render(site.url("/careers"), lambda response: b"posting/1" in response.body)

    assert shown.url == site.url("/portal/")
    assert b"posting/1" in shown.body
