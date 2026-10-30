"""Deleting an Application that should never have existed, from its own page.

Every title, link, company and Posting here is invented.
"""

import html
import re
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

from conftest import RememberedCommits
from test_bringing_in import FOLDER, LINK, TEXT, create
from test_documents import RESUME, add_document

PAGE = f"/applications/{FOLDER}"
OTHER = "nordwind-robotics/2026-09-18-test-engineer"


def danger_zone(page: str) -> str | None:
    found = re.search(r'<section[^>]*id="danger".*?</section>', page, re.DOTALL)
    return found[0] if found else None


def confirmation(dashboard: TestClient, folder: str = FOLDER) -> str:
    """The page the delete button leads to."""
    zone = danger_zone(dashboard.get(f"/applications/{folder}").text)
    assert zone is not None
    (link,) = re.findall(r'<a[^>]*href="([^"]+)"[^>]*>Delete this Application', zone)
    page = dashboard.get(html.unescape(link))
    assert page.status_code == 200
    text: str = page.text
    return text


def what_goes(page: str) -> list[str]:
    found = re.search(r'<ul class="goes">(.*?)</ul>', page, re.DOTALL)
    assert found is not None
    return [html.unescape(each).strip() for each in re.findall(r"<li>(.*?)</li>", found[1])]


def confirm(dashboard: TestClient, folder: str = FOLDER) -> Any:
    page = confirmation(dashboard, folder)
    (action,) = re.findall(r'<form method="post" action="([^"]+)"', page)
    return dashboard.post(html.unescape(action), follow_redirects=False)


def test_the_application_page_has_a_danger_zone_at_its_bottom_and_the_dashboard_none(
    dashboard: TestClient,
) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer")

    page = dashboard.get(PAGE).text
    zone = danger_zone(page)

    assert zone is not None
    assert "Delete this Application" in zone
    assert page.index('id="danger"') > page.index('id="conversation"')
    assert "Delete" not in dashboard.get("/").text


def test_the_confirmation_lists_what_goes_and_deletes_nothing(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=LINK, text=TEXT)
    (data_dir / "applications" / FOLDER / "letter.md").write_text(
        "Dear Nordwind,\n\nI build models.\n", encoding="utf-8"
    )
    dashboard.post(PAGE, data={"notes": "Met them at a fair."})
    commits = len(committer.commits)

    page = confirmation(dashboard)

    assert what_goes(page) == ["The Posting", "The Letter", "The notes"]
    assert "Simulation Engineer" in page and "Nordwind Robotics" in page
    assert (data_dir / "applications" / FOLDER).is_dir()
    assert len(committer.commits) == commits


def test_the_confirmation_names_the_conversation_and_leaves_out_what_is_not_there(
    dashboard: TestClient, data_dir: Path
) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer")
    (data_dir / "applications" / FOLDER / "conversation.yaml").write_text(
        "messages:\n- from: applicant\n  text: Shorter, please.\n", encoding="utf-8"
    )

    assert what_goes(confirmation(dashboard)) == ["The Conversation"]


def test_backing_out_leads_back_to_the_application(dashboard: TestClient) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer")

    page = confirmation(dashboard)
    (back,) = re.findall(r'<a[^>]*href="([^"]+)"[^>]*>Keep it</a>', page)

    assert html.unescape(back).endswith(PAGE)


def test_confirming_removes_the_folder_in_one_commit_and_the_empty_company_folder(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=LINK, text=TEXT)
    commits = len(committer.commits)

    deleted = confirm(dashboard)

    assert deleted.status_code == 303
    assert not (data_dir / "applications" / "nordwind-robotics").exists()
    assert len(committer.commits) == commits + 1
    commit = committer.commits[-1]
    assert "Simulation Engineer" in commit.message and "Nordwind Robotics" in commit.message
    assert commit.paths == [data_dir / "applications" / FOLDER]


def test_the_company_folder_stays_while_it_holds_another_application(
    dashboard: TestClient, data_dir: Path
) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer")
    create(dashboard, company="Nordwind Robotics", role="Test Engineer")

    confirm(dashboard)

    assert not (data_dir / "applications" / FOLDER).exists()
    assert (data_dir / "applications" / OTHER).is_dir()


def test_the_dashboard_then_names_what_was_deleted_and_no_longer_lists_or_counts_it(
    dashboard: TestClient,
) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer")
    create(dashboard, company="Helios Antriebe", role="Test Engineer")

    landed = dashboard.get(confirm(dashboard).headers["location"]).text

    notice = re.search(r'<p class="note" role="status">(.*?)</p>', landed, re.DOTALL)
    assert notice is not None
    assert "Simulation Engineer" in notice[1] and "Nordwind Robotics" in notice[1]
    assert f'href="{PAGE}"' not in landed
    assert "One role still needs an application." in landed
    assert "Simulation Engineer" not in dashboard.get("/").text


def test_shared_documents_are_untouched(dashboard: TestClient, data_dir: Path) -> None:
    add_document(dashboard, "resume.pdf", RESUME)
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer")
    store = sorted((data_dir / "documents").rglob("*"))

    confirm(dashboard)

    assert sorted((data_dir / "documents").rglob("*")) == store
    assert all(path.read_bytes() == RESUME for path in (data_dir / "documents" / "store").iterdir())


def test_a_deletion_that_could_not_be_committed_says_so(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer")
    page = confirmation(dashboard)
    (action,) = re.findall(r'<form method="post" action="([^"]+)"', page)
    committer.commit_failure = "the disk is full"

    deleted = dashboard.post(html.unescape(action))

    assert deleted.status_code == 500
    assert "could not be committed" in deleted.text
    assert "the disk is full" in deleted.text
    assert not (data_dir / "applications" / FOLDER).exists()


def test_deleting_an_application_that_is_already_gone_says_it_is_gone(
    dashboard: TestClient, committer: RememberedCommits
) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer")
    page = confirmation(dashboard)
    (action,) = re.findall(r'<form method="post" action="([^"]+)"', page)
    dashboard.post(html.unescape(action))
    commits = len(committer.commits)

    again = dashboard.post(html.unescape(action))

    assert again.status_code == 404
    assert "gone" in again.text
    assert len(committer.commits) == commits


def test_the_same_link_brought_in_again_creates_a_new_application(
    dashboard: TestClient, data_dir: Path
) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=LINK)
    confirm(dashboard)

    created = create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=LINK)

    assert created.status_code == 303
    assert created.headers["location"].endswith(PAGE)
    assert (data_dir / "applications" / FOLDER / "posting.yaml").is_file()
