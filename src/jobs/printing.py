"""The Letter as the page that is printed, and the boundary that prints it.

A PDF is built from the body of a Letter and a template (decision 48): the body is turned from
Markdown into HTML here, and the template sets around it what the model never writes: who the
Letter is from, who it goes to, the date and the signature. The engine ships a template and a
stylesheet that name no person; ones in the data directory take their place. The finished page is
handed to a Printer, which gives back the bytes of a PDF. Nothing here writes anything: a PDF is
built when it is asked for and kept nowhere (decision 51).
"""

from datetime import date
from pathlib import Path
from typing import Protocol

import jinja2
from markdown_it import MarkdownIt
from markupsafe import Markup
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from jobs.applications import Application
from jobs.records import Malformed

HERE = Path(__file__).parent

# Where a template and a stylesheet of the user's own are looked for in the data directory, and
# where the engine keeps the ones it ships.
FOLDER_NAME = "letter"
TEMPLATE_NAME = "template.html"
STYLESHEET_NAME = "style.css"

# A body is prose a model may have written after reading a Posting, which strangers write. What
# it holds is set as text: markup in it is shown and not taken up, and no picture is fetched.
_MARKDOWN = MarkdownIt("commonmark", {"html": False}).disable("image")

_TEMPLATES = jinja2.Environment(autoescape=True, undefined=jinja2.StrictUndefined)


class Printer(Protocol):
    def print(self, page: str) -> bytes:
        """The PDF of a finished HTML page. Raises NotPrinted."""
        ...


class NotPrinted(Exception):
    """A page could not be made into a PDF, and why."""


def letter_page(data_dir: Path, application: Application, body: str, on: date) -> str:
    """The HTML page that is printed for a Letter of `application` with this body, built on the
    day `on`. Raises Malformed when the template in the data directory cannot be used."""
    template = _own_or_shipped(data_dir, TEMPLATE_NAME)
    try:
        return _TEMPLATES.from_string(template).render(
            # The user's own or the engine's, and CSS is not HTML: `>` in a selector stays one.
            stylesheet=Markup(_own_or_shipped(data_dir, STYLESHEET_NAME)),
            address=application.address.splitlines(),
            date=on,
            body=Markup(_MARKDOWN.render(body)),
        )
    except Exception as error:
        # Deliberately everything: a template is written by hand, and whatever one raises while
        # it is rendered is a reason to show, not a failure of the service.
        raise Malformed(f"{FOLDER_NAME}/{TEMPLATE_NAME} could not be used: {error}") from error


def _own_or_shipped(data_dir: Path, name: str) -> str:
    """The file of this name the data directory holds for Letters, or else the engine's. Raises
    Malformed."""
    own = data_dir / FOLDER_NAME / name
    try:
        return (own if own.is_file() else HERE / FOLDER_NAME / name).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise Malformed(f"{FOLDER_NAME}/{name} could not be read: {error}") from error


class ChromiumPrinter:
    """The real Printer: the Chromium that renders listings, started for one page and closed after
    it. The page is set as it is handed over and no script in it is run."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout
        """Seconds the page is given to load what its stylesheet asks for."""

    def print(self, page: str) -> bytes:
        started = False
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                started = True
                try:
                    sheet = browser.new_page(java_script_enabled=False)
                    sheet.set_content(page, wait_until="load", timeout=self.timeout * 1000)
                    # The stylesheet says how large a page is and what margins it has.
                    return sheet.pdf(format="A4", prefer_css_page_size=True, print_background=True)
                finally:
                    browser.close()
        except PlaywrightError as failure:
            reason = str(failure).strip().partition("\n")[0]
            if started:
                raise NotPrinted(f"The PDF could not be built: {reason}") from failure
            raise NotPrinted(
                f"The PDF could not be built: Chromium did not start: {reason}"
            ) from failure
