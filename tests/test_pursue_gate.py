"""The Pursue Gate: ticking a proposed Posting creates an Application, through the HTTP surface.

Every Posting here is invented, as is every host.
"""

import re
from pathlib import Path

from starlette.testclient import TestClient

from conftest import NOW, Pages, RememberedCommits, SettableClock, WriteApplication, register
from test_documents import add_document
from test_polling import LISTING, POSTING, listing_of, page_of, record, write_source
from test_proposals import DAY, on_disk, write_posting, write_settings
from test_sources import posting_page

FOLDER = "hafenstadt-institute-of-technology/2026-09-18-doctoral-researcher"
TICKED = {"posting": "hafenstadt/doctoral-researcher"}


def test_ticking_a_proposal_creates_an_application_and_takes_it_off_the_list(
    dashboard: TestClient, data_dir: Path
) -> None:
    write_posting(data_dir, "doctoral-researcher")
    dashboard.get("/proposals")

    pursued = dashboard.post("/proposals", data=TICKED)

    assert pursued.status_code == 200
    assert "Nothing is proposed." in pursued.text
    record = on_disk(data_dir / "applications" / FOLDER / "application.yaml")
    assert record["company"] == "Hafenstadt Institute of Technology"
    assert record["role"] == "Doctoral researcher"
    assert record["status"] == "todo"
    listed = dashboard.get("/")
    assert "Hafenstadt Institute of Technology" in listed.text
    assert "Doctoral researcher" in listed.text
    assert "To do" in listed.text


def test_the_posting_is_stored_with_the_application_and_outlives_the_one_that_was_kept(
    dashboard: TestClient, data_dir: Path
) -> None:
    kept = write_posting(data_dir, "doctoral-researcher", raw={"employmentType": "Full time"})
    dashboard.get("/proposals")

    dashboard.post("/proposals", data=TICKED)
    kept.unlink()

    stored = data_dir / "applications" / FOLDER / "posting.yaml"
    assert not stored.is_symlink()
    posting = on_disk(stored)
    assert posting["url"] == "https://portal.example/jobs/posting/doctoral-researcher"
    assert posting["description"] == "Research position in experimental fluid dynamics."
    assert posting["raw"] == {"employmentType": "Full time"}


def test_a_pursued_posting_is_marked_and_not_proposed_again(
    dashboard: TestClient, data_dir: Path, clock: SettableClock, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt")
    network.serve(LISTING, listing_of(101))
    network.serve(POSTING.format(101), page_of(record("Doctoral researcher", "101")))
    dashboard.post("/sources/hafenstadt/poll")
    [path] = (data_dir / "postings" / "hafenstadt").glob("*.yaml")
    dashboard.get("/proposals")
    dashboard.post("/proposals", data={"posting": f"hafenstadt/{path.stem}"})

    clock.now = NOW + DAY
    dashboard.post("/sources/hafenstadt/poll")
    page = dashboard.get("/proposals")

    assert on_disk(path)["pursued"] == NOW
    assert "Nothing is proposed." in page.text
    left_out = dashboard.get("/proposals/left-out")
    assert "Doctoral researcher" in left_out.text
    assert "Pursued on 2026-09-18" in left_out.text


def test_the_shared_documents_are_linked_into_the_new_application(
    dashboard: TestClient, data_dir: Path
) -> None:
    add_document(dashboard, "resume.pdf", b"%PDF-1.7 an invented resume")
    write_posting(data_dir, "doctoral-researcher")
    dashboard.get("/proposals")

    dashboard.post("/proposals", data=TICKED)

    resume = data_dir / "applications" / FOLDER / "resume.pdf"
    assert resume.is_symlink()
    assert resume.read_bytes() == b"%PDF-1.7 an invented resume"
    assert resume.resolve().parent == (data_dir / "documents" / "store").resolve()


def test_the_application_and_the_mark_on_the_posting_are_one_commit(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    add_document(dashboard, "resume.pdf", b"%PDF-1.7 an invented resume")
    kept = write_posting(data_dir, "doctoral-researcher")
    dashboard.get("/proposals")
    committer.commits.clear()

    dashboard.post("/proposals", data=TICKED)

    [commit] = committer.commits
    folder = data_dir / "applications" / FOLDER
    assert commit.message == "Pursue Doctoral researcher at Hafenstadt Institute of Technology"
    assert sorted(commit.paths) == sorted(
        [folder / "application.yaml", folder / "posting.yaml", folder / "resume.pdf", kept]
    )


def test_everything_that_is_ticked_is_pursued(dashboard: TestClient, data_dir: Path) -> None:
    write_posting(data_dir, "doctoral-researcher")
    write_posting(data_dir, "laboratory-engineer")
    write_posting(data_dir, "technician")
    dashboard.get("/proposals")

    pursued = dashboard.post(
        "/proposals",
        data={"posting": ["hafenstadt/doctoral-researcher", "hafenstadt/laboratory-engineer"]},
    )

    assert "Technician" in pursued.text
    assert "Laboratory engineer" not in pursued.text
    company = data_dir / "applications" / "hafenstadt-institute-of-technology"
    assert sorted(each.name for each in company.iterdir()) == [
        "2026-09-18-doctoral-researcher",
        "2026-09-18-laboratory-engineer",
    ]


def test_pursuing_with_nothing_ticked_is_refused(dashboard: TestClient, data_dir: Path) -> None:
    write_posting(data_dir, "doctoral-researcher")
    dashboard.get("/proposals")

    refused = dashboard.post("/proposals")

    assert refused.status_code == 422
    assert "Nothing was ticked" in refused.text
    assert "Doctoral researcher" in refused.text
    assert not (data_dir / "applications").exists()


def test_a_posting_that_is_not_proposed_cannot_be_pursued(
    dashboard: TestClient, data_dir: Path
) -> None:
    write_posting(data_dir, "doctoral-researcher", dismissed=NOW)

    refused = dashboard.post("/proposals", data=TICKED)

    assert refused.status_code == 422
    assert "No Posting hafenstadt/doctoral-researcher is proposed" in refused.text
    assert not (data_dir / "applications").exists()


def test_ticking_the_same_posting_twice_creates_one_application(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    write_posting(data_dir, "doctoral-researcher")
    dashboard.get("/proposals")
    dashboard.post("/proposals", data=TICKED)
    committer.commits.clear()

    again = dashboard.post("/proposals", data=TICKED)

    assert "No Posting hafenstadt/doctoral-researcher is proposed" in again.text
    assert committer.commits == []


def test_a_posting_whose_application_already_exists_stays_proposed(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    write_application(FOLDER, "company: Hafenstadt Institute of Technology\n")
    kept = write_posting(data_dir, "doctoral-researcher")
    dashboard.get("/proposals")

    refused = dashboard.post("/proposals", data=TICKED)

    assert refused.status_code == 422
    assert f"{FOLDER} already exists" in refused.text
    assert 'value="hafenstadt/doctoral-researcher"' in refused.text
    assert "pursued" not in on_disk(kept)
    assert not (data_dir / "applications" / FOLDER / "posting.yaml").exists()


def test_a_url_goes_in_and_an_application_folder_comes_out(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    add_document(dashboard, "resume.pdf", b"%PDF-1.7 an invented resume")
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    register(dashboard, POSTING.format(101), label="Hafenstadt")
    dashboard.post("/sources/hafenstadt/poll")
    proposed = dashboard.get("/proposals")
    [ticked] = re.findall(r'name="posting" value="([^"]+)"', proposed.text)
    network.fetched.clear()

    dashboard.post("/proposals", data={"posting": ticked})

    [folder] = (data_dir / "applications").glob("*/*")
    assert folder.name == "2026-09-18-doctoral-researcher"
    assert sorted(each.name for each in folder.iterdir()) == [
        "application.yaml",
        "posting.yaml",
        "resume.pdf",
    ]
    assert (folder / "resume.pdf").is_symlink()
    assert "Doctoral researcher" in dashboard.get("/").text
    # The Pursue Gate reaches nobody: it is made of what is on disk already.
    assert network.fetched == []


def test_a_posting_the_cap_holds_back_was_never_shown_and_cannot_be_pursued(
    dashboard: TestClient, data_dir: Path
) -> None:
    write_settings(data_dir, "cap: 0\n")
    kept = write_posting(data_dir, "doctoral-researcher")

    refused = dashboard.post("/proposals", data=TICKED)

    assert refused.status_code == 422
    assert "No Posting hafenstadt/doctoral-researcher is proposed" in refused.text
    assert "pursued" not in on_disk(kept)
    assert not (data_dir / "applications").exists()
