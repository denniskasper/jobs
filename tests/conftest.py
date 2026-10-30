"""Seam A: the running application, driven through its HTTP surface.

Every test gets a temporary data directory, a fixed clock, a network that refuses to be used and
a committer that only remembers what it was asked to commit, so the suite passes offline and never
writes outside its own directory.
"""

import html
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from jobs.commits import CommitFailed
from jobs.fetching import NetworkUnavailable, NoBrowser, Ready, Response
from jobs.models import ModelUnavailable
from jobs.web import create_app

NOW = datetime(2026, 9, 18, 9, 30, tzinfo=UTC)

WriteApplication = Callable[[str, str], Path]


def git(repository: Path, *arguments: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repository), *arguments], check=True, capture_output=True, text=True
    )
    return done.stdout


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    return root


class SettableClock:
    """Stands still at NOW until a test moves it."""

    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> SettableClock:
    return SettableClock()


@dataclass(frozen=True)
class Commit:
    message: str
    paths: list[Path]


@dataclass
class RememberedCommits:
    """A Committer that keeps what it was handed instead of running git, and that has committed
    and pushed everything unless a test says otherwise."""

    commits: list[Commit] = field(default_factory=list)
    commit_failure: str | None = None
    push_failure: str | None = None

    def commit(self, message: str, paths: Sequence[Path]) -> None:
        if self.commit_failure:
            raise CommitFailed(self.commit_failure)
        self.commits.append(Commit(message=message, paths=list(paths)))

    def not_pushed(self) -> str | None:
        return self.push_failure


@pytest.fixture
def committer() -> RememberedCommits:
    return RememberedCommits()


@dataclass
class Pages:
    """A Network that serves the pages a test put in it, and is offline for everything else. It
    has a browser only for the pages a test said what a browser makes of."""

    pages: dict[str, bytes | int] = field(default_factory=dict)
    fetched: list[str] = field(default_factory=list)
    shown: dict[str, bytes] = field(default_factory=dict)
    rendered: list[str] = field(default_factory=list)
    # Called with the URL in the middle of every fetch, for a test to look around from there.
    when_fetched: Callable[[str], None] | None = None

    def serve(self, url: str, page: str | int) -> None:
        """A page's markup, or the status the host answers with instead of one."""
        self.pages[url] = page if isinstance(page, int) else page.encode()

    def show(self, url: str, page: str) -> None:
        """The markup a browser ends up with once the page's script has run."""
        self.shown[url] = page.encode()

    def render(self, url: str, ready: Ready) -> Response:
        self.rendered.append(url)
        if url not in self.shown:
            raise NoBrowser(f"no browser is available to render {url}")
        return Response(url=url, status=200, body=self.shown[url])

    def fetch(self, url: str) -> Response:
        self.fetched.append(url)
        if self.when_fetched:
            self.when_fetched(url)
        if url not in self.pages:
            raise NetworkUnavailable(f"no network is available to fetch {url}")
        page = self.pages[url]
        if isinstance(page, int):
            return Response(url=url, status=page, body=b"")
        return Response(url=url, status=200, body=page)


@pytest.fixture
def network() -> Pages:
    return Pages()


@dataclass
class ScriptedModel:
    """A Model that gives the answers a test lined up, in order, and keeps what it was asked.
    With no answer left it is unavailable, so a test that expects no question fails loudly."""

    name: str = "scripted-model"
    answers: list[str] = field(default_factory=list)
    asked: list[str] = field(default_factory=list)

    def ask(self, prompt: str) -> str:
        self.asked.append(prompt)
        if not self.answers:
            raise ModelUnavailable("no model is available")
        return self.answers.pop(0)


@pytest.fixture
def model() -> ScriptedModel:
    return ScriptedModel()


def pdf_of(*pages: str) -> bytes:
    """A PDF with one A4 page per text, each page carrying its text in a single line, which a PDF
    library reads back."""
    font = 3
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [%s] /Count %d >>"
        % (b" ".join(b"%d 0 R" % (4 + 2 * at) for at in range(len(pages))), len(pages)),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for at, text in enumerate(pages):
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = b"BT /F1 12 Tf 72 770 Td (%s) Tj ET" % escaped.encode("latin-1")
        objects += [
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>" % (font, 5 + 2 * at),
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        ]
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    table = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        table,
    )
    return bytes(out)


# What the printer of the fixtures gives back for every page: a PDF of one page, whose text tells
# it from anything else.
PDF = pdf_of("Printed by the fixtures")


@dataclass
class RememberedPages:
    """A Printer that keeps the pages it was handed instead of starting a browser, and gives back
    the same bytes for each."""

    printed: list[str] = field(default_factory=list)

    def print(self, page: str) -> bytes:
        self.printed.append(page)
        return PDF


@pytest.fixture
def printer() -> RememberedPages:
    return RememberedPages()


@pytest.fixture
def dashboard(
    data_dir: Path,
    clock: SettableClock,
    network: Pages,
    committer: RememberedCommits,
    model: ScriptedModel,
    printer: RememberedPages,
) -> TestClient:
    return TestClient(
        create_app(
            data_dir=data_dir,
            clock=clock,
            network=network,
            committer=committer,
            model=model,
            printer=printer,
            sleep=lambda seconds: None,
        )
    )


@pytest.fixture
def without_a_printer(
    data_dir: Path,
    clock: SettableClock,
    network: Pages,
    committer: RememberedCommits,
    model: ScriptedModel,
) -> TestClient:
    """The service as it is on a machine with no browser to print with."""
    return TestClient(
        create_app(
            data_dir=data_dir,
            clock=clock,
            network=network,
            committer=committer,
            model=model,
            sleep=lambda seconds: None,
        )
    )


def run_next_registration(dashboard: TestClient) -> None:
    """Explore the Registration that has waited longest, which nothing does by itself here: the
    fixtures pass no runner, so a test looks at the pages before and after at its own choosing."""
    cast(Starlette, dashboard.app).state.registrations.run_next()


def fetch_next_answer(dashboard: TestClient) -> None:
    """Fetch the answer that has been asked for longest, which nothing does by itself here: with
    no runner an answer about a Letter waits, as a Registration does."""
    cast(Starlette, dashboard.app).state.letters.answer_next()


def register(dashboard: TestClient, url: str, label: str = "", rule: str = "") -> str:
    """Register a Source the way the user does, and give back the page they end up looking at:
    the new Source's page, or the Sources page with a refusal. The form is handed in, the
    Registration explored, and the Sources page looked at, from where a Registration that ended
    in a Source links to it. No other test hands in the Register form to get a Source, so how a
    Registration comes to its end can change without any scenario changing."""
    handed_in = dashboard.post(
        "/sources", data={"url": url, "label": label, "rule": rule}, follow_redirects=False
    )
    if handed_in.status_code != 303:
        # Refused on the form at once.
        refused: str = handed_in.text
        return refused
    run_next_registration(dashboard)
    page: str = dashboard.get("/sources").text
    registered = re.search(r'href="([^"]+)">Check its config<', page)
    if registered:
        page = dashboard.get(registered[1]).text
    return page


def try_again(dashboard: TestClient, page: str) -> str:
    """Follow "Try again" on the one refused Registration a Sources page shows, and give back
    the page with the form."""
    (link,) = re.findall(r'href="([^"]+)">Try again<', page)
    form: str = dashboard.get(html.unescape(link)).text
    return form


@pytest.fixture
def write_application(data_dir: Path) -> WriteApplication:
    """Place an Application record on disk the way a human would: by writing the file."""

    def write(folder: str, record: str) -> Path:
        path = data_dir / "applications" / folder / "application.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(record, encoding="utf-8")
        return path

    return write
