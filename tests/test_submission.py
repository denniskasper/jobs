"""Marking an Application sent: a Submission with its Channel, where it went and the Manifest of
the Packet as it was built, and the Status from `todo` to `waiting`, all in one commit.

Every company, role, applicant, address and file here is invented.
"""

import hashlib
import html
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from starlette.testclient import TestClient

from conftest import RememberedCommits, SettableClock, WriteApplication, pdf_of
from test_letter_revision import BODY, FOLDER, PAGE
from test_packet import CERTIFICATE, build, ready, upload

POSTING_URL = "https://jobs.example.org/research-engineer"
# Built rather than written out: email addresses belong in the corpus.
APPLICATIONS_DESK = "@".join(["bewerbung", "tuhh.example.org"])
HR_DESK = "@".join(["hr", "tuhh.example.org"])
SHIPYARD = "@".join(["jobs", "werft.example.org"])


@pytest.fixture(name="ready")
def ready_fixture(
    dashboard: TestClient, write_application: WriteApplication, data_dir: Path
) -> Path:
    """An Application that is `todo`, has a Letter and holds a certificate, a resume and a
    Zeugnis, from an applicant whose surname is known."""
    return ready(dashboard, write_application, data_dir)


def mark_form(page: str) -> tuple[str, str]:
    """Where the form that marks the Application sent posts to, and the where it starts with."""
    (form,) = re.findall(r"<form[^>]*>(?:(?!</form>).)*>Mark sent<", page, re.DOTALL)
    (action,) = re.findall(r'action="([^"]+)"', form)
    (where,) = re.findall(r'<input[^>]*name="where"[^>]*value="([^"]*)"', form)
    return html.unescape(action), html.unescape(where)


def mark_sent(
    dashboard: TestClient, channel: str = "portal", where: str | None = None, status: int = 200
) -> str:
    """Mark the Application sent from the form its page offers, where it went left as the form
    has it unless `where` says otherwise."""
    action, offered = mark_form(dashboard.get(PAGE).text)
    marked = dashboard.post(
        action, data={"channel": channel, "where": offered if where is None else where}
    )
    assert marked.status_code == status, marked.text
    text: str = marked.text
    return text


def record_of(folder: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((folder / "application.yaml").read_text("utf-8"))
    return loaded


def with_posting(folder: Path) -> None:
    (folder / "posting.yaml").write_text(
        f"company: TUHH\nrole: Research Engineer\nurl: {POSTING_URL}\n", encoding="utf-8"
    )


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def plain(page: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())


def test_marking_sent_records_a_submission_and_moves_todo_to_waiting_in_one_commit(
    dashboard: TestClient, ready: Path, data_dir: Path, committer: RememberedCommits
) -> None:
    with_posting(ready)
    build(dashboard)
    committer.commits.clear()

    mark_sent(dashboard)

    record = record_of(ready)
    assert record["status"] == "waiting"
    assert record["history"][-1] == {"status": "waiting", "date": datetime(2026, 9, 18).date()}
    (submission,) = record["submissions"]
    assert submission["at"] == datetime(2026, 9, 18, 9, 30, tzinfo=UTC)
    assert submission["channel"] == "portal"
    assert submission["where"] == POSTING_URL
    packet = ready / "packet"
    assert submission["manifest"] == {
        name: sha256((packet / name).read_bytes())
        for name in (
            "Mustermann_Anschreiben.pdf",
            "Mustermann_Lebenslauf.pdf",
            "Mustermann_Anlagen.pdf",
        )
    }
    (commit,) = committer.commits
    assert FOLDER in commit.message
    assert ready / "application.yaml" in commit.paths
    # The built files are kept in the store by their hashes, and committed with the rest.
    store = data_dir / "documents" / "store"
    for name, hashed in submission["manifest"].items():
        stored = store / f"{hashed}.pdf"
        assert stored.read_bytes() == (packet / name).read_bytes()
        assert stored in commit.paths


def test_the_manifest_holds_the_built_files_not_the_documents(
    dashboard: TestClient, ready: Path
) -> None:
    build(dashboard, "each")

    mark_sent(dashboard, where="https://portal.example.org/apply")

    (submission,) = record_of(ready)["submissions"]
    built = ready / "packet"
    assert submission["manifest"] == {
        each.name: sha256(each.read_bytes()) for each in built.iterdir() if each.name[0] != "."
    }
    assert submission["manifest"]["certificate.pdf"] == sha256(CERTIFICATE)
    assert submission["where"] == "https://portal.example.org/apply"


def test_where_starts_as_the_postings_url_and_takes_an_email_address(
    dashboard: TestClient, ready: Path
) -> None:
    with_posting(ready)
    build(dashboard)

    assert mark_form(dashboard.get(PAGE).text)[1] == POSTING_URL
    mark_sent(dashboard, channel="email", where=APPLICATIONS_DESK)

    (submission,) = record_of(ready)["submissions"]
    assert submission["channel"] == "email"
    assert submission["where"] == APPLICATIONS_DESK


def test_without_a_posting_where_starts_empty_and_has_to_be_said(
    dashboard: TestClient, ready: Path, committer: RememberedCommits
) -> None:
    build(dashboard)
    assert mark_form(dashboard.get(PAGE).text)[1] == ""
    committer.commits.clear()

    refused = mark_sent(dashboard, where="  ", status=422)

    assert "where it went" in plain(refused)
    assert record_of(ready)["status"] == "todo"
    assert committer.commits == []


def test_an_email_has_to_go_to_an_address(dashboard: TestClient, ready: Path) -> None:
    build(dashboard)

    refused = mark_sent(dashboard, channel="email", where="the HR department", status=422)

    assert "email address" in plain(refused)
    assert "submissions" not in record_of(ready)


def test_marking_sent_is_refused_without_a_packet(
    dashboard: TestClient, ready: Path, committer: RememberedCommits
) -> None:
    committer.commits.clear()

    refused = dashboard.post(f"{PAGE}/sent", data={"channel": "portal", "where": POSTING_URL})

    assert refused.status_code == 422
    assert "No Packet has been built" in plain(refused.text)
    assert record_of(ready)["status"] == "todo"
    assert committer.commits == []


def test_marking_sent_is_refused_once_the_letter_changed_after_building(
    dashboard: TestClient, ready: Path, committer: RememberedCommits
) -> None:
    build(dashboard)
    dashboard.post(f"{PAGE}/letter/edit", data={"body": BODY + "\n\nOne more line."})
    committer.commits.clear()

    refused = mark_sent(dashboard, where=POSTING_URL, status=422)

    assert "build it again" in plain(refused)
    assert record_of(ready)["status"] == "todo"
    assert committer.commits == []


def test_marking_sent_is_refused_once_the_documents_changed_after_building(
    dashboard: TestClient, ready: Path
) -> None:
    build(dashboard)
    upload(dashboard, "form.pdf", CERTIFICATE)

    refused = mark_sent(dashboard, where=POSTING_URL, status=422)

    assert "build it again" in plain(refused)
    assert "submissions" not in record_of(ready)


def test_marking_sent_is_refused_once_a_shared_document_has_a_newer_version(
    dashboard: TestClient, ready: Path
) -> None:
    shared = dashboard.post("/documents", files={"file": ("photo.pdf", CERTIFICATE)})
    assert shared.status_code == 200, shared.text
    added = dashboard.post(f"{PAGE}/documents/add", data={"name": "photo.pdf"})
    assert added.status_code == 200, added.text
    build(dashboard)
    newer = dashboard.post("/documents", files={"file": ("photo.pdf", pdf_of("A newer photo"))})
    assert newer.status_code == 200, newer.text

    refused = mark_sent(dashboard, where=POSTING_URL, status=422)

    assert "build it again" in plain(refused)
    assert record_of(ready)["status"] == "todo"


def test_a_sent_application_shows_its_submissions_and_their_files(
    dashboard: TestClient, ready: Path
) -> None:
    with_posting(ready)
    build(dashboard)
    mark_sent(dashboard)

    page = dashboard.get(PAGE).text

    (section,) = re.findall(r'<section[^>]*id="submissions".*?</section>', page, re.DOTALL)
    said = plain(section)
    assert "2026-09-18 09:30" in said
    assert "Portal" in said
    assert POSTING_URL in said
    for name in (
        "Mustermann_Anschreiben.pdf",
        "Mustermann_Lebenslauf.pdf",
        "Mustermann_Anlagen.pdf",
    ):
        assert name in said
    # Each file as it was sent, from the store.
    (letter,) = re.findall(r'href="([^"]+Mustermann_Anschreiben.pdf)"', section)
    sent = dashboard.get(html.unescape(letter))
    assert sent.status_code == 200
    assert sent.content == (ready / "packet" / "Mustermann_Anschreiben.pdf").read_bytes()
    # And no second Submission while it is waiting.
    assert "Mark sent" not in page


def test_the_sent_date_is_the_day_of_the_first_submission(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    write_application(
        "werft/2026-06-01-engineer",
        "company: Werft Nord\nrole: Engineer\ndate: 2026-06-01\nstatus: waiting\n"
        "history:\n- status: todo\n  date: 2026-06-01\n- status: waiting\n  date: 2026-07-20\n"
        "submissions:\n- at: 2026-07-03 10:00:00+00:00\n  channel: email\n"
        "  where: " + SHIPYARD + "\n  manifest:\n    Bewerbung.pdf: " + "a" * 64 + "\n",
    )

    page = dashboard.get("/").text

    assert "Werft Nord, Engineer: sent 2026-07-03, Waiting" in html.unescape(page)


def test_setting_waiting_by_hand_still_works_and_records_no_submission(
    dashboard: TestClient, ready: Path
) -> None:
    changed = dashboard.post(f"{PAGE}/status", data={"status": "waiting"})

    assert changed.status_code == 200
    record = record_of(ready)
    assert record["status"] == "waiting"
    assert "submissions" not in record
    assert "sent 2026-09-18" in html.unescape(dashboard.get("/").text)


def test_a_second_submission_after_todo_again_leaves_the_first_as_it_was(
    dashboard: TestClient, ready: Path, clock: SettableClock
) -> None:
    with_posting(ready)
    build(dashboard)
    mark_sent(dashboard)
    (first,) = record_of(ready)["submissions"]

    clock.now = datetime(2026, 10, 2, 14, 0, tzinfo=UTC)
    dashboard.post(f"{PAGE}/status", data={"status": "todo"})
    build(dashboard, "everything")
    mark_sent(dashboard, channel="email", where=HR_DESK)

    record = record_of(ready)
    assert record["status"] == "waiting"
    assert record["submissions"][0] == first
    second = record["submissions"][1]
    assert second["at"] == datetime(2026, 10, 2, 14, 0, tzinfo=UTC)
    assert list(second["manifest"]) == ["Mustermann_Bewerbung.pdf"]
    # The first one's files are still there to be had, though the folder was built anew.
    section = re.findall(
        r'<section[^>]*id="submissions".*?</section>', dashboard.get(PAGE).text, re.DOTALL
    )
    assert "Mustermann_Lebenslauf.pdf" in section[0]
    assert "sent 2026-09-18" in html.unescape(dashboard.get("/").text)


def test_an_application_with_a_submission_counts_as_sent_and_cannot_be_deleted(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    write_application(
        "werft/2026-06-01-engineer",
        "company: Werft Nord\nrole: Engineer\ndate: 2026-06-01\nstatus: todo\n"
        "submissions:\n- at: 2026-07-03 10:00:00+00:00\n  channel: portal\n"
        "  where: https://werft.example.org\n  manifest:\n    Bewerbung.pdf: " + "a" * 64 + "\n",
    )

    refused = dashboard.post("/applications/werft/2026-06-01-engineer/delete")

    assert "was sent, so it cannot be deleted" in plain(refused.text)
