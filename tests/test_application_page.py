"""The page of one Application: its Posting, and what the user writes down about this pursuit.

Every Application, Posting and address here is invented.
"""

import html
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from starlette.testclient import TestClient

from conftest import RememberedCommits, WriteApplication

FOLDER = "tuhh/2026-09-01-research-engineer"
RECORD = "company: TUHH\nrole: Research Engineer\ndate: 2026-09-01\nstatus: todo\n"


def test_every_application_row_links_to_its_page(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)
    write_application(
        "nordwind-robotics/2026-09-10-simulation-engineer",
        "company: Nordwind Robotics\nrole: Simulation Engineer\ndate: 2026-09-10\n"
        "status: waiting\n",
    )

    listed = dashboard.get("/").text

    rows = re.findall(r"<tr>\s*<td class=\"col-company\">.*?</tr>", listed, re.DOTALL)
    assert len(rows) == 2
    opened = []
    for row in rows:
        # On the company, which a narrow screen still shows; the stylesheet hides the role there.
        (link,) = re.findall(r'<td class="col-company"><a href="([^"]+)"', row)
        opened.append(dashboard.get(html.unescape(link)))
    assert [page.status_code for page in opened] == [200, 200]
    assert "Simulation Engineer" in opened[0].text
    assert "Nordwind Robotics" in opened[0].text
    assert "Research Engineer" in opened[1].text
    assert "TUHH" in opened[1].text


@pytest.mark.parametrize(
    "folder",
    [
        "tuhh/2026-09-01-gone",
        "tuhh",
        "tuhh/2026-09-01-research-engineer/deeper",
        "../outside/x",
        "..%2Foutside/x",
        "tuhh/..%2F..%2Foutside%2Fx",
    ],
)
def test_a_folder_that_names_no_application_is_not_found(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication, folder: str
) -> None:
    write_application(FOLDER, RECORD)
    outside = data_dir / "outside" / "x" / "application.yaml"
    outside.parent.mkdir(parents=True)
    outside.write_text(RECORD.replace("TUHH", "Elsewhere"), encoding="utf-8")

    missing = dashboard.get(f"/applications/{folder}")

    assert missing.status_code == 404
    assert "Elsewhere" not in missing.text


def test_a_record_that_cannot_be_read_is_not_opened(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    write_application(FOLDER, "company: [unclosed\n")

    refused = dashboard.get(f"/applications/{FOLDER}")

    assert refused.status_code == 422
    assert "not valid YAML" in refused.text.split("Not done.")[1].split("</p>")[0]


def write_posting(data_dir: Path, text: str) -> Path:
    """Place the copy of a Posting in the Application folder, as the Pursue Gate does."""
    path = data_dir / "applications" / FOLDER / "posting.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_page_shows_what_was_kept_of_the_posting_as_text(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)
    write_posting(
        data_dir,
        """\
company: TUHH
role: Research Engineer
location: Hafenstadt
url: https://portal.example/jobs/posting/research-engineer
deadline: 2026-10-15
remuneration:
  currency: EUR
  low: '52000'
  high: '61000'
  per: YEAR
description: |-
  You build <b>test rigs</b> for towing tanks.

  You bring a degree in mechanical engineering.
raw:
  employmentType: Full time
""",
    )

    page = dashboard.get(f"/applications/{FOLDER}").text

    assert "You build &lt;b&gt;test rigs&lt;/b&gt; for towing tanks.\n\nYou bring a degree" in page
    assert "Hafenstadt" in page
    assert "2026-10-15" in page
    assert "EUR 52000 to 61000 per year" in page
    assert 'href="https://portal.example/jobs/posting/research-engineer"' in page
    assert "holds no Posting" not in page


def test_an_application_without_a_posting_says_so(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)

    page = dashboard.get(f"/applications/{FOLDER}")

    assert page.status_code == 200
    assert "This Application holds no Posting." in page.text
    assert 'class="posting-text"' not in page.text


def test_a_posting_that_cannot_be_read_says_why_and_the_page_still_opens(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)
    write_posting(data_dir, "company: TUHH\nrole: [unclosed\n")

    page = dashboard.get(f"/applications/{FOLDER}")

    assert page.status_code == 200
    assert "not valid YAML" in page.text
    assert "TUHH" in page.text


def read_record(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "applications" / FOLDER / "application.yaml"
    record = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(record, dict)
    return record


def field_on(page: str, name: str) -> str:
    """What the page's text box of that name holds."""
    (held,) = re.findall(rf'<textarea[^>]*name="{name}"[^>]*>(.*?)</textarea>', page, re.DOTALL)
    return html.unescape(held)


def save_form(page: str) -> str:
    """Where the form that holds the Notes posts to."""
    (action,) = re.findall(
        r'<form[^>]*action="([^"]+)"[^>]*>(?:(?!</form>).)*name="notes"', page, re.DOTALL
    )
    return html.unescape(action)


def test_notes_are_saved_and_there_when_the_page_is_opened_again(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)
    opened = dashboard.get(f"/applications/{FOLDER}").text
    assert field_on(opened, "notes") == ""

    saved = dashboard.post(
        save_form(opened), data={"notes": "Mention the towing tank.\r\n\r\nAsk about remote days."}
    )

    assert saved.status_code == 200
    again = dashboard.get(f"/applications/{FOLDER}").text
    assert field_on(again, "notes") == "Mention the towing tank.\n\nAsk about remote days."
    assert read_record(data_dir)["notes"] == "Mention the towing tank.\n\nAsk about remote days."


def test_the_employers_address_is_saved_with_its_line_breaks(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    path = write_application(FOLDER, RECORD)
    typed = (
        "TUHH, Institut für Beispielwesen\r\nz. Hd. Frau Dr. Erika Musterfrau\r\n\r\n"
        "Am Beispielkai 7\r\n21073 Hafenstadt"
    )

    dashboard.post(f"/applications/{FOLDER}", data={"address": typed})

    kept = (
        "TUHH, Institut für Beispielwesen\nz. Hd. Frau Dr. Erika Musterfrau\n\n"
        "Am Beispielkai 7\n21073 Hafenstadt"
    )
    assert read_record(data_dir)["address"] == kept
    assert field_on(dashboard.get(f"/applications/{FOLDER}").text, "address") == kept
    # Line for line in the file too, for whoever edits it by hand.
    assert "  Am Beispielkai 7\n  21073 Hafenstadt\n" in path.read_text(encoding="utf-8")


def test_saving_commits_the_application_record_and_only_that(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    committer: RememberedCommits,
) -> None:
    path = write_application(FOLDER, RECORD)
    write_posting(data_dir, "company: TUHH\nrole: Research Engineer\n")

    dashboard.post(f"/applications/{FOLDER}", data={"notes": "Mention the towing tank."})
    dashboard.post(f"/applications/{FOLDER}", data={"address": "Am Beispielkai 7"})

    assert [commit.paths for commit in committer.commits] == [[path], [path]]
    assert [commit.message for commit in committer.commits] == [
        f"Change Notes of {FOLDER}",
        f"Change address of {FOLDER}",
    ]


def test_saving_one_field_leaves_the_other_and_the_rest_of_the_record(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD + "salary: 62000\nnotes: Mention the towing tank.\n")

    dashboard.post(f"/applications/{FOLDER}", data={"address": "Am Beispielkai 7"})

    record = read_record(data_dir)
    assert record["notes"] == "Mention the towing tank."
    assert record["address"] == "Am Beispielkai 7"
    assert record["salary"] == 62000
    assert record["company"] == "TUHH"


def test_saving_what_is_already_there_writes_and_commits_nothing(
    dashboard: TestClient, write_application: WriteApplication, committer: RememberedCommits
) -> None:
    written = RECORD + "notes: Mention the towing tank.\n"
    path = write_application(FOLDER, written)

    saved = dashboard.post(
        f"/applications/{FOLDER}", data={"notes": "Mention the towing tank.", "address": ""}
    )

    assert saved.status_code == 200
    assert path.read_text(encoding="utf-8") == written
    assert committer.commits == []


def test_emptying_a_field_takes_it_out_of_the_record(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD + "notes: Mention the towing tank.\n")

    dashboard.post(f"/applications/{FOLDER}", data={"notes": "  \r\n"})

    assert "notes" not in read_record(data_dir)
    assert field_on(dashboard.get(f"/applications/{FOLDER}").text, "notes") == ""


def test_a_record_without_the_two_fields_is_listed_and_shown_as_before(
    dashboard: TestClient, write_application: WriteApplication, committer: RememberedCommits
) -> None:
    path = write_application(FOLDER, RECORD)

    listed = dashboard.get("/")
    opened = dashboard.get(f"/applications/{FOLDER}")

    assert "TUHH" in listed.text
    assert "could not be read" not in listed.text
    assert opened.status_code == 200
    assert field_on(opened.text, "notes") == ""
    assert field_on(opened.text, "address") == ""
    assert path.read_text(encoding="utf-8") == RECORD
    assert committer.commits == []


def test_what_was_written_down_outlives_a_change_of_status(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)
    dashboard.post(
        f"/applications/{FOLDER}", data={"address": "Am Beispielkai 7\n21073 Hafenstadt"}
    )

    dashboard.post(f"/applications/{FOLDER}/status", data={"status": "waiting"})

    assert read_record(data_dir)["address"] == "Am Beispielkai 7\n21073 Hafenstadt"
    # Notes and the address are not the Letter: they stay editable in any Status.
    saved = dashboard.post(f"/applications/{FOLDER}", data={"notes": "Sent on Friday."})
    assert saved.status_code == 200
    assert read_record(data_dir)["notes"] == "Sent on Friday."


def test_saving_to_a_folder_that_names_no_application_is_not_found(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)

    missing = dashboard.post("/applications/tuhh/2026-09-01-gone", data={"notes": "Anything."})

    assert missing.status_code == 404
    assert not (data_dir / "applications" / "tuhh" / "2026-09-01-gone").exists()


def test_a_record_that_needs_fixing_by_hand_is_not_rewritten_by_saving(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    written = RECORD + "notes:\n  - a list\n"
    path = write_application(FOLDER, written)

    refused = dashboard.post(f"/applications/{FOLDER}", data={"notes": "Mention the towing tank."})

    assert refused.status_code == 422
    assert "notes is not text" in refused.text
    assert path.read_text(encoding="utf-8") == written


def test_a_save_that_could_not_be_committed_says_so_and_keeps_what_was_written(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    committer: RememberedCommits,
) -> None:
    write_application(FOLDER, RECORD)
    committer.commit_failure = "the disk is full"

    saved = dashboard.post(f"/applications/{FOLDER}", data={"notes": "Mention the towing tank."})

    assert saved.status_code == 500
    assert "could not be committed: the disk is full" in saved.text
    assert field_on(saved.text, "notes") == "Mention the towing tank."
    assert read_record(data_dir)["notes"] == "Mention the towing tank."


def test_the_page_has_its_five_parts(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)

    page = dashboard.get(f"/applications/{FOLDER}").text

    titles = re.findall(r'<h2 class="application-title"[^>]*>([^<]+)</h2>', page)
    assert titles == ["Posting", "Documents", "Packet", "Letter", "Conversation"]


def test_what_was_written_down_stays_line_for_line_in_the_file_through_later_writes(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    path = write_application(FOLDER, RECORD)
    dashboard.post(
        f"/applications/{FOLDER}", data={"address": "Am Beispielkai 7\n21073 Hafenstadt"}
    )

    dashboard.post(f"/applications/{FOLDER}", data={"notes": "Mention the towing tank."})
    dashboard.post(f"/applications/{FOLDER}/status", data={"status": "waiting"})

    written = path.read_text(encoding="utf-8")
    assert "address: |-\n  Am Beispielkai 7\n  21073 Hafenstadt\n" in written
    assert "notes: Mention the towing tank.\n" in written
