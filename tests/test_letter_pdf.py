"""The Letter as a PDF: its body set in a template with the employer's address and the date around
it, handed to a printer as one HTML page when the Application's page asks for it, and kept nowhere.

Every Application, address, body and template here is invented.
"""

import html
import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from conftest import (
    PDF,
    RememberedCommits,
    RememberedPages,
    ScriptedModel,
    SettableClock,
    WriteApplication,
    fetch_next_answer,
)
from jobs.applications import Application, Status
from jobs.printing import ChromiumPrinter, NotPrinted, letter_page
from test_fetching import no_chromium
from test_letter_by_hand import save_by_hand
from test_letter_revision import (
    ANSWER,
    BODY,
    FOLDER,
    PAGE,
    RECORD,
    accept,
    ask_for_a_first_version,
    letter_path,
    part,
    write_brief,
)


def pdf_on(page: str) -> str | None:
    """Where the page gets the Letter as a PDF from, or None when it shows none."""
    found = re.search(r'<iframe[^>]*class="letter-pdf"[^>]*src="([^"]+)"', page)
    return html.unescape(found[1]) if found else None


def test_a_letter_is_shown_as_a_pdf_the_app_serves(
    dashboard: TestClient, write_application: WriteApplication, printer: RememberedPages
) -> None:
    write_application(FOLDER, RECORD)
    shown = save_by_hand(dashboard, BODY)

    address = pdf_on(shown)
    assert address is not None
    # Showing the page builds nothing: the PDF is built when it is asked for.
    assert printer.printed == []

    served = dashboard.get(address)

    assert served.status_code == 200
    assert served.headers["content-type"] == "application/pdf"
    assert served.content == PDF
    assert len(printer.printed) == 1
    # The next one asked for is built anew: the Letter and the day change under one address.
    assert served.headers["cache-control"] == "no-store"
    # The words are within reach beside it, should the PDF not show.
    assert "towing tanks are where I learned" in words(
        re.findall(r'<details class="as-text">(.*?)</details>', shown, re.DOTALL)[0]
    )


def printed_for(dashboard: TestClient, printer: RememberedPages) -> str:
    """Ask for the PDF the Application's page shows, and give back the page the printer was
    handed for it."""
    address = pdf_on(dashboard.get(PAGE).text)
    assert address is not None
    assert dashboard.get(address).status_code == 200
    return printer.printed[-1]


def words(markup: str) -> str:
    """What a reader gets of some markup, on one line."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", markup)).split())


def test_the_printed_page_carries_the_body_as_html(
    dashboard: TestClient, write_application: WriteApplication, printer: RememberedPages
) -> None:
    write_application(FOLDER, RECORD)
    save_by_hand(dashboard, BODY + "\n\nI *measure* first & believe after.")

    page = printed_for(dashboard, printer)

    assert "<p><strong>Application as Research Engineer</strong></p>" in page
    assert "<p>Dear Dr. Musterfrau,</p>" in page
    assert "<p>I <em>measure</em> first &amp; believe after.</p>" in page
    # What Markdown marks is not left standing.
    assert "**" not in page


def test_markup_in_a_body_is_printed_as_text_and_fetches_nothing(
    dashboard: TestClient, write_application: WriteApplication, printer: RememberedPages
) -> None:
    write_application(FOLDER, RECORD)
    save_by_hand(
        dashboard,
        'Dear all,\n\n<script src="https://tracker.example.org/x.js"></script>\n\n'
        "![seen](https://tracker.example.org/seen.png)",
    )

    page = printed_for(dashboard, printer)

    assert "<script" not in page
    assert "<img" not in page
    assert "&lt;script" in page


def test_the_printed_page_carries_the_employers_address_line_for_line(
    dashboard: TestClient, write_application: WriteApplication, printer: RememberedPages
) -> None:
    write_application(FOLDER, RECORD)
    dashboard.post(
        PAGE, data={"address": "Hafenstadt Institute\r\nDr. Erika Musterfrau\r\nAm Beispielkai 7"}
    )
    save_by_hand(dashboard, BODY)

    page = printed_for(dashboard, printer)

    (block,) = re.findall(r"<address[^>]*>(.*?)</address>", page, re.DOTALL)
    assert [words(line) for line in block.split("<br>") if line.strip()] == [
        "Hafenstadt Institute",
        "Dr. Erika Musterfrau",
        "Am Beispielkai 7",
    ]


def test_without_an_address_in_the_record_the_printed_page_has_no_block_for_one(
    dashboard: TestClient, write_application: WriteApplication, printer: RememberedPages
) -> None:
    write_application(FOLDER, RECORD)
    save_by_hand(dashboard, BODY)

    assert "<address" not in printed_for(dashboard, printer)


def test_the_printed_page_carries_the_day_it_is_built(
    dashboard: TestClient,
    write_application: WriteApplication,
    printer: RememberedPages,
    clock: SettableClock,
) -> None:
    write_application(FOLDER, RECORD)
    save_by_hand(dashboard, BODY)

    assert "2026-09-18" in words(printed_for(dashboard, printer))

    # Not the day the Letter was written: the next day's PDF says the next day.
    clock.now = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)
    built_later = words(printed_for(dashboard, printer))
    assert "2026-09-19" in built_later
    assert "2026-09-18" not in built_later


def test_the_engines_template_is_used_when_the_data_directory_has_none_and_names_nobody(
    dashboard: TestClient, write_application: WriteApplication, printer: RememberedPages
) -> None:
    write_application(FOLDER, RECORD)
    save_by_hand(dashboard, "Dear all,\n\nRegards")

    page = printed_for(dashboard, printer)

    # A whole page with a stylesheet of its own, and in it nothing but the body and the date.
    assert page.lstrip().startswith("<!doctype html>")
    assert "@page" in page
    (shown,) = re.findall(r"<body>(.*)</body>", page, re.DOTALL)
    assert words(shown) == "2026-09-18 Dear all, Regards"


def test_a_template_and_a_stylesheet_in_the_data_directory_replace_the_engines(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    printer: RememberedPages,
) -> None:
    write_application(FOLDER, RECORD)
    dashboard.post(PAGE, data={"address": "Hafenstadt Institute\r\nAm Beispielkai 7"})
    save_by_hand(dashboard, BODY)
    own = data_dir / "letter"
    own.mkdir()
    (own / "template.html").write_text(
        "<html><head><style>{{ stylesheet }}</style></head><body>"
        "<p class='sender'>Max Mustermann, Musterweg 1</p>"
        "<p class='to'>{{ address|join(' / ') }}</p>"
        "<p class='day'>{{ date.strftime('%d.%m.%Y') }}</p>"
        "{{ body }}<p class='signature'>Max Mustermann</p></body></html>",
        encoding="utf-8",
    )
    (own / "style.css").write_text("body > p { font-family: Musterschrift; }", encoding="utf-8")

    page = printed_for(dashboard, printer)

    assert "<p class='sender'>Max Mustermann, Musterweg 1</p>" in page
    assert "<p class='to'>Hafenstadt Institute / Am Beispielkai 7</p>" in page
    assert "<p class='day'>18.09.2026</p>" in page
    assert "<p>Dear Dr. Musterfrau,</p>" in page
    assert "<p class='signature'>Max Mustermann</p>" in page
    # The stylesheet as it was written, and the engine's nowhere.
    assert "<style>body > p { font-family: Musterschrift; }</style>" in page
    assert "@page" not in page


def test_a_stylesheet_alone_in_the_data_directory_is_set_in_the_engines_template(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    printer: RememberedPages,
) -> None:
    write_application(FOLDER, RECORD)
    save_by_hand(dashboard, BODY)
    (data_dir / "letter").mkdir()
    (data_dir / "letter" / "style.css").write_text("p { color: teal; }", encoding="utf-8")

    page = printed_for(dashboard, printer)

    assert "<style>p { color: teal; }</style>" in page
    assert "@page" not in page
    assert "<p>Dear Dr. Musterfrau,</p>" in page


def test_a_template_that_cannot_be_used_builds_no_pdf_and_says_why(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    printer: RememberedPages,
) -> None:
    write_application(FOLDER, RECORD)
    save_by_hand(dashboard, BODY)
    (data_dir / "letter").mkdir()
    # One that cannot be read as a template, one that names what it is not handed, and one that
    # fails while it is rendered.
    for unusable in ("{% if %}", "{{ sender }}", "{{ date.strftime(1) }}"):
        (data_dir / "letter" / "template.html").write_text(unusable, encoding="utf-8")

        refused = dashboard.get(f"{PAGE}/letter.pdf")

        assert refused.status_code == 422
        assert "letter/template.html could not be used" in refused.text
    assert printer.printed == []


def test_a_letter_that_cannot_be_read_builds_no_pdf_and_says_why(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    printer: RememberedPages,
) -> None:
    write_application(FOLDER, RECORD)
    letter_path(data_dir).write_bytes(b"\xff\xfe not text")

    refused = dashboard.get(f"{PAGE}/letter.pdf")

    assert refused.status_code == 422
    assert "letter.md could not be read" in refused.text
    assert printer.printed == []


def everything_in(data_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(data_dir).as_posix(): path.read_bytes()
        for path in sorted(data_dir.rglob("*"))
        if path.is_file()
    }


def test_building_a_pdf_writes_nothing_and_commits_nothing(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    printer: RememberedPages,
    committer: RememberedCommits,
) -> None:
    write_application(FOLDER, RECORD)
    save_by_hand(dashboard, BODY)
    before, commits = everything_in(data_dir), list(committer.commits)

    printed_for(dashboard, printer)

    assert everything_in(data_dir) == before
    assert committer.commits == commits


def test_an_application_without_a_letter_shows_no_pdf_and_asks_the_printer_nothing(
    dashboard: TestClient, write_application: WriteApplication, printer: RememberedPages
) -> None:
    write_application(FOLDER, RECORD)

    assert pdf_on(dashboard.get(PAGE).text) is None
    asked_anyway = dashboard.get(f"{PAGE}/letter.pdf")

    assert asked_anyway.status_code == 404
    assert "has no Letter" in asked_anyway.text
    assert printer.printed == []


def test_no_pdf_is_built_of_an_application_that_is_not_there(
    dashboard: TestClient, printer: RememberedPages
) -> None:
    assert dashboard.get(f"{PAGE}/letter.pdf").status_code == 404
    assert printer.printed == []


def test_a_printer_that_fails_is_answered_with_its_reason(
    dashboard: TestClient,
    write_application: WriteApplication,
    printer: RememberedPages,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_application(FOLDER, RECORD)
    save_by_hand(dashboard, BODY)

    def fail(page: str) -> bytes:
        raise NotPrinted("The PDF could not be built: Chromium did not start")

    monkeypatch.setattr(printer, "print", fail)

    failed = dashboard.get(f"{PAGE}/letter.pdf")

    assert failed.status_code == 503
    assert failed.text == "The PDF could not be built: Chromium did not start"


def test_without_a_printer_the_page_says_so_and_a_letter_is_asked_for_accepted_and_edited(
    without_a_printer: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    dashboard = without_a_printer
    write_application(FOLDER, RECORD)
    write_brief(data_dir)

    ask_for_a_first_version(dashboard)
    model.answers.append(ANSWER)
    fetch_next_answer(dashboard)
    accepted = accept(dashboard, dashboard.get(PAGE).text)

    assert letter_path(data_dir).read_text(encoding="utf-8") == BODY + "\n"
    assert pdf_on(accepted) is None
    assert "No PDF can be built" in part(accepted, "letter")
    # The Letter is still there to read.
    assert "towing tanks are where I learned" in part(accepted, "letter")

    edited = save_by_hand(dashboard, BODY + "\n\nPS: I measure.")

    assert letter_path(data_dir).read_text(encoding="utf-8") == BODY + "\n\nPS: I measure.\n"
    assert "No PDF can be built" in part(edited, "letter")
    refused = dashboard.get(f"{PAGE}/letter.pdf")
    assert refused.status_code == 503
    assert "No PDF can be built" in refused.text


def test_with_a_printer_the_page_does_not_say_that_none_can_be_built(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)

    assert "No PDF can be built" not in save_by_hand(dashboard, BODY)


@pytest.fixture
def chromium_printer() -> ChromiumPrinter:
    if reason := no_chromium():
        pytest.skip(reason)
    return ChromiumPrinter()


def pages_of(pdf: bytes) -> int:
    """How many pages a PDF has, by the page objects it holds."""
    return len(re.findall(rb"/Type\s*/Page\b", pdf))


def test_the_real_printer_makes_a_pdf_of_a_page(
    data_dir: Path, chromium_printer: ChromiumPrinter
) -> None:
    application = Application(
        folder=FOLDER,
        company="TUHH",
        role="Research Engineer",
        date=date(2026, 9, 1),
        status=Status.TODO,
        address="Hafenstadt Institute\nAm Beispielkai 7",
    )

    pdf = chromium_printer.print(letter_page(data_dir, application, BODY, date(2026, 9, 18)))

    assert pdf.startswith(b"%PDF-")
    assert pages_of(pdf) == 1


def test_the_real_printer_makes_two_pages_of_a_long_letter(
    data_dir: Path, chromium_printer: ChromiumPrinter
) -> None:
    application = Application(
        folder=FOLDER,
        company="TUHH",
        role="Research Engineer",
        date=date(2026, 9, 1),
        status=Status.TODO,
    )
    long = "\n\n".join([BODY] + ["I measure before I believe, and then I measure again. " * 6] * 14)

    pdf = chromium_printer.print(letter_page(data_dir, application, long, date(2026, 9, 18)))

    assert pages_of(pdf) == 2


def test_the_real_printer_runs_no_script_of_the_page_it_prints(
    chromium_printer: ChromiumPrinter,
) -> None:
    short = "<html><body><p>One page.</p></body></html>"
    grows = short.replace(
        "</body>",
        "<script>document.body.innerHTML += '<p style=\\'break-before: page\\'>Two.</p>'"
        "</script></body>",
    )

    assert pages_of(chromium_printer.print(grows)) == pages_of(chromium_printer.print(short)) == 1
