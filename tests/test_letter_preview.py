"""A preview of a waiting Revision: the PDF of the Letter it would make, one switch away from its
difference, built from the waiting body when it is asked for and kept nowhere.

Every Application, address, body and answer here is invented.
"""

import html
import re
from pathlib import Path

from starlette.testclient import TestClient

from conftest import (
    PDF,
    RememberedCommits,
    RememberedPages,
    ScriptedModel,
    WriteApplication,
    fetch_next_answer,
)
from test_letter_pdf import everything_in, pdf_on
from test_letter_revision import (
    BODY,
    FOLDER,
    PAGE,
    RECORD,
    accept,
    difference_on,
    letter_path,
    part,
)
from test_letter_steering import REVISED, a_first_revision_waits, reject, say

# Where the preview is asked for, whether or not the page offers it.
PREVIEW_PDF = f"{PAGE}/letter/revision.pdf"


def views_on(page: str) -> list[tuple[str, str, bool]]:
    """The views of a waiting Revision a page offers, in order: what each is called, where it
    leads, and whether it is the one shown."""
    return [
        (label.strip(), html.unescape(address), 'aria-current="true"' in attributes)
        for attributes, address, label in re.findall(
            r'<a class="tab[^"]*"([^>]*)href="([^"]+)"[^>]*>([^<]+)</a>',
            "".join(re.findall(r'<nav class="tabs views".*?</nav>', page, re.DOTALL)),
        )
    ]


def preview_of(dashboard: TestClient) -> str:
    """Switch from the difference the Application's page shows to the preview, and give back
    the page that leads to."""
    (_, (label, address, shown)) = views_on(dashboard.get(PAGE).text)
    assert (label, shown) == ("Preview", False)
    switched: str = dashboard.get(address).text
    return switched


def printed_for_the_preview(dashboard: TestClient, printer: RememberedPages) -> str:
    """Switch to the preview, ask for the PDF it shows, and give back the page the printer was
    handed for it."""
    address = pdf_on(preview_of(dashboard))
    assert address is not None
    assert dashboard.get(address).status_code == 200
    return printer.printed[-1]


def test_a_waiting_revision_is_offered_as_a_difference_and_as_a_preview_the_difference_first(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    printer: RememberedPages,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)

    first = dashboard.get(PAGE).text

    assert [(label, shown) for label, _, shown in views_on(first)] == [
        ("Difference", True),
        ("Preview", False),
    ]
    assert difference_on(first) is not None
    assert pdf_on(first) is None

    switched = preview_of(dashboard)

    assert [(label, shown) for label, _, shown in views_on(switched)] == [
        ("Difference", False),
        ("Preview", True),
    ]
    assert difference_on(switched) is None
    address = pdf_on(switched)
    assert address is not None
    # Showing the page builds nothing: the preview is built when it is asked for.
    assert printer.printed == []

    served = dashboard.get(address)

    assert served.status_code == 200
    assert served.headers["content-type"] == "application/pdf"
    assert served.headers["cache-control"] == "no-store"
    assert served.content == PDF
    # Told from the Letter's own by name, should it be saved.
    assert 'filename="revision.pdf"' in served.headers["content-disposition"]
    assert len(printer.printed) == 1
    # And back again, to the page as it is first shown.
    ((_, back, _), _) = views_on(switched)
    assert difference_on(dashboard.get(back).text) is not None


def test_the_page_printed_for_the_preview_carries_the_waiting_body_and_not_the_letter(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    printer: RememberedPages,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    accept(dashboard, dashboard.get(PAGE).text)
    model.answers.append(REVISED)
    say(dashboard, "Shorter, and do not say believe.")
    fetch_next_answer(dashboard)

    page = printed_for_the_preview(dashboard, printer)

    assert "<p>towing tanks are where I learned to measure.</p>" in page
    assert "before I believe" not in page
    # The Letter as it stands is still the one its own PDF is built from.
    assert dashboard.get(f"{PAGE}/letter.pdf").status_code == 200
    assert "before I believe" in printer.printed[-1]


def test_the_preview_is_set_as_the_letters_own_pdf_is_with_the_address_and_the_day(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    printer: RememberedPages,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    dashboard.post(PAGE, data={"address": "Hafenstadt Institute\r\nAm Beispielkai 7"})
    (data_dir / "letter").mkdir()
    (data_dir / "letter" / "template.html").write_text(
        "<p class='to'>{{ address|join(' / ') }}</p><p class='day'>{{ date.isoformat() }}</p>"
        "{{ body }}",
        encoding="utf-8",
    )

    assert dashboard.get(PREVIEW_PDF).status_code == 200

    page = printer.printed[-1]
    assert "<p class='to'>Hafenstadt Institute / Am Beispielkai 7</p>" in page
    assert "<p class='day'>2026-09-18</p>" in page
    assert "<p>Dear Dr. Musterfrau,</p>" in page


def test_after_a_message_revises_the_revision_the_preview_is_of_the_latest_body(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    printer: RememberedPages,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    assert dashboard.get(PREVIEW_PDF).status_code == 200
    assert "before I believe" in printer.printed[-1]
    model.answers.append(REVISED)
    say(dashboard, "Shorter, and do not say believe.")
    fetch_next_answer(dashboard)

    page = printed_for_the_preview(dashboard, printer)

    assert "<p>towing tanks are where I learned to measure.</p>" in page
    assert "before I believe" not in page


def test_building_the_preview_writes_nothing_and_commits_nothing(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    printer: RememberedPages,
    committer: RememberedCommits,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    before, commits = everything_in(data_dir), list(committer.commits)

    printed_for_the_preview(dashboard, printer)

    assert everything_in(data_dir) == before
    assert committer.commits == commits
    assert not letter_path(data_dir).exists()


def test_with_no_revision_waiting_there_is_no_preview_and_asking_for_one_is_not_found(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    printer: RememberedPages,
) -> None:
    # Of an Application that is not there, of one nothing was asked about, and of one whose
    # Revision was accepted.
    assert dashboard.get(PREVIEW_PDF).status_code == 404
    write_application(FOLDER, RECORD)
    assert dashboard.get(PREVIEW_PDF).status_code == 404
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    accept(dashboard, dashboard.get(PAGE).text)

    # Nor does the address of the preview bring one back.
    for address in (PAGE, f"{PAGE}?view=preview"):
        shown = dashboard.get(address).text
        assert views_on(shown) == []
        assert (pdf_on(shown) or "").endswith(f"{PAGE}/letter.pdf")
    asked_anyway = dashboard.get(PREVIEW_PDF)

    assert asked_anyway.status_code == 404
    assert "has no Revision waiting" in asked_anyway.text
    assert printer.printed == []


def test_without_a_printer_the_preview_says_that_no_pdf_can_be_built_and_the_difference_shows(
    without_a_printer: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    dashboard = without_a_printer
    a_first_revision_waits(dashboard, data_dir, write_application, model)

    first = dashboard.get(PAGE).text

    assert difference_on(first) is not None
    # Said where a PDF was asked for, not over a difference that needs none.
    assert "No PDF can be built" not in part(first, "letter")

    switched = preview_of(dashboard)

    assert pdf_on(switched) is None
    assert "No PDF can be built" in part(switched, "letter")
    assert difference_on(switched) == difference_on(first)
    refused = dashboard.get(PREVIEW_PDF)
    assert refused.status_code == 503
    assert "No PDF can be built" in refused.text


def test_a_revision_is_accepted_from_the_preview(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)

    accepted = accept(dashboard, preview_of(dashboard))

    assert letter_path(data_dir).read_text(encoding="utf-8") == BODY + "\n"
    # The Revision has ended, and with it the switch: what shows is the Letter's own PDF.
    assert views_on(accepted) == []
    assert (pdf_on(accepted) or "").endswith(f"{PAGE}/letter.pdf")


def test_a_revision_is_rejected_from_the_preview(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)

    rejected = reject(dashboard, preview_of(dashboard))

    assert not letter_path(data_dir).exists()
    assert views_on(rejected) == []
    assert pdf_on(rejected) is None
    assert "Revision: Rejected" in part(rejected, "conversation")
    assert dashboard.get(PREVIEW_PDF).status_code == 404
