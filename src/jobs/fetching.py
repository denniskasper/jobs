"""The network boundary. Everything that would open a socket goes through a Network.

The hop walk lives here too: fetch a page, ask extraction which links lead onward, fetch again,
until the posting pages are reached and read. Extraction decides; this module only fetches.
"""

import http.client
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import Protocol
from urllib.parse import quote, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page as BrowserPage
from playwright.sync_api import sync_playwright

from jobs.extraction import (
    Extracted,
    Failed,
    FilterRule,
    Hop,
    Page,
    Posting,
    Render,
    SourceConfig,
    Strategy,
    discover_links,
    extract,
)


@dataclass(frozen=True)
class Response:
    url: str
    """Where the body was finally served from, after any redirects."""
    status: int
    body: bytes


Ready = Callable[[Response], bool]
"""Whether what a rendered page shows by now is what it was rendered for."""


class Network(Protocol):
    def fetch(self, url: str) -> Response: ...

    def render(self, url: str, ready: Ready) -> Response:
        """The page as a browser shows it: as soon as `ready` says so, or else as it stood when
        the wait for that ran out."""
        ...


class Browser(Protocol):
    def render(self, url: str, ready: Ready) -> Response: ...


class NetworkUnavailable(Exception):
    pass


class NoBrowser(NetworkUnavailable):
    """There is no browser to render a page in, whatever the page."""


USER_AGENT = "jobs/0.1 (a personal job-search tool; polls a few pages a day)"


# Everything that already means something in a URL, so that only what could not be sent is encoded.
_URL_CHARACTERS = ":/?#[]@!$&'()*+,;=%-._~"


class Offline:
    """A Network that refuses every request."""

    def fetch(self, url: str) -> Response:
        raise NetworkUnavailable(f"no network is available to fetch {url}")

    def render(self, url: str, ready: Ready) -> Response:
        raise NetworkUnavailable(f"no network is available to render {url}")


class HttpNetwork:
    """The real Network: a plain HTTP GET, redirects followed, and a Browser for the pages that
    need rendering. An answer the host gave is a Response whatever its status; only no answer at
    all is NetworkUnavailable."""

    def __init__(self, timeout: float = 30.0, browser: Browser | None = None) -> None:
        self.timeout = timeout
        self.browser = browser

    def render(self, url: str, ready: Ready) -> Response:
        _http_only(url)
        if self.browser is None:
            raise NoBrowser(f"{url} could not be rendered: no browser is set up")
        return self.browser.render(url, ready)

    def fetch(self, url: str) -> Response:
        _http_only(url)
        # Pages link to addresses as people write them, umlauts and spaces included.
        encoded = quote(url, safe=_URL_CHARACTERS)
        request = urllib.request.Request(encoded, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as answer:
                return Response(url=answer.url, status=answer.status, body=answer.read())
        except urllib.error.HTTPError as refusal:
            with refusal:
                return Response(url=refusal.url, status=refusal.code, body=refusal.read())
        except (OSError, http.client.HTTPException, ValueError) as failure:
            reason = failure.reason if isinstance(failure, urllib.error.URLError) else failure
            raise NetworkUnavailable(f"{url} could not be reached: {reason}") from failure


class Chromium:
    """The real Browser: Chromium without a window, started for one page and closed after it. A
    Source asks for a few pages a day, so what a start costs buys not having a process to look
    after."""

    def __init__(self, patience: float = 30.0, every: float = 0.25, timeout: float = 30.0) -> None:
        self.patience = patience
        """Seconds a page is given to show what it was rendered for, once it has loaded."""
        self.every = every
        """Seconds between one look at what the page shows and the next."""
        self.timeout = timeout
        """Seconds the host is given to answer with the page at all."""

    def render(self, url: str, ready: Ready) -> Response:
        started = False
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                started = True
                try:
                    return self._rendered(browser.new_page(user_agent=USER_AGENT), url, ready)
                finally:
                    browser.close()
        except PlaywrightError as failure:
            if started:
                raise NetworkUnavailable(
                    f"{url} could not be rendered: {_first_line(failure)}"
                ) from failure
            raise NoBrowser(
                f"{url} could not be rendered: Chromium did not start: {_first_line(failure)}"
            ) from failure

    def _rendered(self, page: BrowserPage, url: str, ready: Ready) -> Response:
        answer = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
        if answer is None:
            raise NetworkUnavailable(f"{url} could not be rendered: the host gave no answer")
        deadline = time.monotonic() + self.patience
        while True:
            try:
                shown = Response(url=page.url, status=answer.status, body=page.content().encode())
            except PlaywrightError:
                # A script that is just taking the page elsewhere leaves nothing to look at.
                if time.monotonic() >= deadline:
                    raise
            else:
                # Only the page itself is waited on: an answer that is not the page fills
                # nothing in.
                if answer.status != HTTPStatus.OK or ready(shown) or time.monotonic() >= deadline:
                    return shown
            page.wait_for_timeout(self.every * 1000)


def _first_line(failure: Exception) -> str:
    """What went wrong, without the call log Playwright appends to it."""
    return str(failure).strip().partition("\n")[0]


def _http_only(url: str) -> None:
    # urllib and a browser would as readily open a file; a page must not be able to link to one.
    if urlsplit(url).scheme not in ("http", "https"):
        raise NetworkUnavailable(f"{url} could not be reached: it is not an http address")


@dataclass(frozen=True)
class Rejected:
    """A Posting a Source's filter rule removed, and why, in words meant for the user."""

    posting: Posting
    reason: str


@dataclass(frozen=True)
class WalkError:
    """One page of a walk that brought nothing back, and why."""

    url: str
    status: int | None
    """What the host answered, or None when the trouble was not the host's answer."""
    reason: str
    fetch: bool = False
    """Whether the page could not be fetched at all, rather than fetched and not read."""
    unread: bool = False
    """Whether the page was fetched and the Source's selectors could not read it. A redesign
    looks like this, where a structured record that cannot be read is one page's trouble."""


@dataclass(frozen=True)
class Walked:
    """What one walk of a Source brought back. Errors do not empty it: every page that could be
    reached and read is in it."""

    postings: list[Posting]
    rejected: list[Rejected]
    errors: list[WalkError]


def walk(
    url: str,
    config: SourceConfig,
    network: Network,
    *,
    sleep: Callable[[float], None] = time.sleep,
    timer: Callable[[], float] = time.monotonic,
) -> Walked:
    """From a Source's registered URL through its hop chain to the posting pages, never asking
    more often than the config's interval allows."""
    return _Walk(config, Paced(network, config.interval, sleep, timer)).run(url)


class Paced:
    """A Network that waits out what is left of the interval since its last request began."""

    def __init__(
        self,
        network: Network,
        interval: float,
        sleep: Callable[[float], None],
        timer: Callable[[], float],
    ) -> None:
        self.network = network
        self.interval = interval
        self.sleep = sleep
        self.timer = timer
        self.last: float | None = None

    def fetch(self, url: str) -> Response:
        self._wait()
        return self.network.fetch(url)

    def render(self, url: str, ready: Ready) -> Response:
        self._wait()
        return self.network.render(url, ready)

    def _wait(self) -> None:
        if self.last is not None and (wait := self.last + self.interval - self.timer()) > 0:
            self.sleep(wait)
        self.last = self.timer()


_EVEN_RENDERED = ", even once it was rendered in a browser"


@dataclass(frozen=True)
class _Retried:
    """What rendering a page came to, after its markup had yielded nothing."""

    page: Page | None = None
    tried: str = ""
    """What the attempt adds to the reason, should the page still yield nothing."""
    failed: bool = False
    """Whether there was a browser and it could not show the page."""


class _Walk:
    """One walk in progress: what it has brought back so far, and whether the host has said stop."""

    def __init__(self, config: SourceConfig, network: Network) -> None:
        self.config = config
        self.network = network
        self.walked = Walked(postings=[], rejected=[], errors=[])
        self.rate_limited = False
        self.read: set[str] = set()

    def run(self, url: str) -> Walked:
        urls = [url]
        for hop in self.config.hops:
            # Two pages of one hop may lead to the same place; it is visited once.
            urls = list(dict.fromkeys(link for found in urls for link in self._onward(found, hop)))
        for found in urls:
            self._read(found)
        return self.walked

    def _onward(self, url: str, hop: Hop) -> list[str]:
        def appeared(response: Response) -> bool:
            return bool(discover_links(page_of(response), hop))

        page = self._fetched(url, hop.render, appeared)
        if page is None:
            return []
        links = discover_links(page, hop)
        tried = _EVEN_RENDERED if hop.render is Render.BROWSER else ""
        if not links and hop.render is Render.STATIC:
            # A listing that fills in its postings with a script has none in its markup.
            again = self._rendered_instead(url, appeared)
            links, tried = discover_links(again.page, hop) if again.page else [], again.tried
        if not links:
            self._error(
                url, None, f"no link on the page matches {hop.follow}{tried}, so the walk ends here"
            )
        return links

    def _read(self, url: str) -> None:
        def appeared(response: Response) -> bool:
            return bool(_postings(extract(page_of(response), self.config)))

        page = self._fetched(url, self.config.render, appeared)
        if page is None or page.url in self.read:
            # Two links may redirect to one page; its Postings are read once.
            return
        self.read.add(page.url)
        result = extract(page, self.config)
        rendered = self.config.render is Render.BROWSER
        selectors = self.config.selectors if self.config.strategy is Strategy.SELECTORS else None
        # A page read item by item that has none is an employer advertising nothing: its
        # selectors were proved on the markup as it is served, so no browser would show more.
        nothing_open = isinstance(result, Extracted) and bool(selectors and selectors.item)
        if not _postings(result) and not rendered and not self.config.hops and not nothing_open:
            # The registered URL read as it is is the listing, and may fill itself in with a
            # script as any other. Posting pages at the end of a chain are not retried one by one.
            again = self._rendered_instead(url, appeared)
            if again.page and _postings(shown := extract(again.page, self.config)):
                page, result = again.page, shown
            elif again.failed:
                self._error(url, None, f"the page carries no Posting{again.tried}")
        if isinstance(result, Failed):
            unread = self.config.strategy is Strategy.SELECTORS
            self._error(page.url, None, result.reason, unread=unread)
            return
        if not result.postings and rendered:
            self._error(
                page.url, None, "no Posting appeared on the page while it was rendered in a browser"
            )
        elif not result.postings and self.config.reads_posting_pages:
            self._error(
                page.url, None, "the page was reached as a posting page but carries no Posting"
            )
        for posting in result.postings:
            reason = _rejection(posting, self.config.filters)
            if reason is None:
                self.walked.postings.append(posting)
            else:
                self.walked.rejected.append(Rejected(posting=posting, reason=reason))

    def _fetched(self, url: str, render: Render, appeared: Ready) -> Page | None:
        """The page at a URL, or None with the reason added to the walk's errors."""
        if self.rate_limited:
            return None
        try:
            if render is Render.BROWSER:
                response = self.network.render(url, appeared)
            else:
                response = self.network.fetch(url)
        except NetworkUnavailable as unavailable:
            self._error(url, None, str(unavailable), fetch=True)
            return None
        if response.status == HTTPStatus.OK:
            return page_of(response)
        self._refused(response)
        self._error(url, response.status, _refusal(response.status), fetch=True)
        return None

    def _rendered_instead(self, url: str, appeared: Ready) -> _Retried:
        """A page whose markup yielded nothing, as a browser shows it. Without a browser nothing
        was tried."""
        if self.rate_limited:
            return _Retried()
        try:
            response = self.network.render(url, appeared)
        except NoBrowser:
            return _Retried()
        except NetworkUnavailable as unavailable:
            return _Retried(
                tried=f", and rendering it in a browser failed ({unavailable})", failed=True
            )
        if response.status == HTTPStatus.OK:
            return _Retried(page=page_of(response), tried=_EVEN_RENDERED)
        self._refused(response)
        return _Retried(tried=f", and asked by a browser {_refusal(response.status)}", failed=True)

    def _refused(self, response: Response) -> None:
        if response.status == HTTPStatus.TOO_MANY_REQUESTS:
            # A host that says slow down is not asked again until the next walk.
            self.rate_limited = True

    def _error(
        self,
        url: str,
        status: int | None,
        reason: str,
        *,
        fetch: bool = False,
        unread: bool = False,
    ) -> None:
        self.walked.errors.append(
            WalkError(url=url, status=status, reason=reason, fetch=fetch, unread=unread)
        )


def page_of(response: Response) -> Page:
    return Page(url=response.url, body=response.body)


def _postings(result: Extracted | Failed) -> list[Posting]:
    return [] if isinstance(result, Failed) else result.postings


def _refusal(status: int) -> str:
    """What an answer other than the page means, in words meant for the user."""
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        return "the host is applying a rate limit (429), so the rest of the walk was left unfetched"
    if status in (HTTPStatus.NOT_FOUND, HTTPStatus.GONE):
        return f"the page is no longer there ({status})"
    try:
        return f"the host answered {status} {HTTPStatus(status).phrase}"
    except ValueError:
        return f"the host answered {status}"


def _rejection(posting: Posting, rules: tuple[FilterRule, ...]) -> str | None:
    """Why a Source's filter rules remove a Posting, or None when they keep it."""
    for rule in rules:
        value = getattr(posting, rule.field)
        if value is None:
            return (
                f"it has no {rule.field}, and this Source only keeps those matching {rule.pattern}"
            )
        if not re.search(rule.pattern, value):
            return f"its {rule.field} does not match {rule.pattern}, which this Source only keeps"
    return None
