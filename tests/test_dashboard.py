import hashlib
import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from conftest import WriteApplication


def test_an_application_written_by_hand_is_listed(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    write_application(
        "tuhh/2026-09-01-research-engineer-fluid-dynamics",
        """\
company: TUHH
role: Research Engineer, Fluid Dynamics
date: 2026-09-01
status: waiting
""",
    )

    page = dashboard.get("/")

    assert page.status_code == 200
    assert "TUHH" in page.text
    assert "Research Engineer, Fluid Dynamics" in page.text
    assert "2026-09-01" in page.text
    assert "Waiting" in page.text


def test_editing_the_file_by_hand_shows_on_reload(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    folder = "tuhh/2026-09-01-research-engineer-fluid-dynamics"
    record = "company: TUHH\nrole: Research Engineer\ndate: 2026-09-01\nstatus: {status}\n"
    write_application(folder, record.format(status="waiting"))
    assert "Waiting" in dashboard.get("/").text

    write_application(folder, record.format(status="interview"))

    page = dashboard.get("/")
    assert "Interview" in page.text
    assert "Waiting" not in page.text


def test_an_empty_data_directory_renders_an_empty_dashboard(dashboard: TestClient) -> None:
    page = dashboard.get("/")

    assert page.status_code == 200
    assert "No Applications yet" in page.text


def test_the_page_carries_the_stylesheet_and_todays_date(dashboard: TestClient) -> None:
    page = dashboard.get("/")

    assert "2026-09-18" in page.text
    assert dashboard.get("/static/dashboard.css").status_code == 200


@pytest.mark.parametrize(
    ("record", "complaint"),
    [
        ("company: [unclosed\n", "not valid YAML"),
        ("just a sentence\n", "not a mapping"),
        ("role: Engineer\ndate: 2026-09-01\nstatus: todo\n", "company"),
        ("company: ACME\nrole: Engineer\ndate: next week\nstatus: todo\n", "date"),
        ("company: ACME\nrole: Engineer\ndate: 2026-09-01\nstatus: sent\n", "status"),
    ],
)
def test_a_malformed_record_is_shown_as_a_problem_beside_the_healthy_ones(
    dashboard: TestClient, write_application: WriteApplication, record: str, complaint: str
) -> None:
    write_application(
        "tuhh/2026-09-01-research-engineer",
        "company: TUHH\nrole: Research Engineer\ndate: 2026-09-01\nstatus: todo\n",
    )
    write_application("acme/2026-09-02-engineer", record)

    page = dashboard.get("/")

    assert page.status_code == 200
    assert "TUHH" in page.text
    assert "acme/2026-09-02-engineer" in page.text
    assert complaint in page.text


def test_a_folder_with_no_record_is_shown_as_a_problem(
    dashboard: TestClient, data_dir: Path
) -> None:
    (data_dir / "applications" / "acme" / "2026-09-02-engineer").mkdir(parents=True)

    page = dashboard.get("/")

    assert page.status_code == 200
    assert "acme/2026-09-02-engineer" in page.text
    assert "has no application.yaml" in page.text


def test_a_record_placed_at_the_wrong_depth_is_shown_as_a_problem(
    dashboard: TestClient, data_dir: Path
) -> None:
    misplaced = data_dir / "applications" / "acme" / "application.yaml"
    misplaced.parent.mkdir(parents=True)
    misplaced.write_text("company: ACME\nrole: Engineer\ndate: 2026-09-02\nstatus: todo\n")

    page = dashboard.get("/")

    assert page.status_code == 200
    assert "not inside a company folder and a dated-role folder" in page.text


def test_the_stylesheet_is_linked_at_an_address_that_names_what_it_holds(
    dashboard: TestClient,
) -> None:
    """A browser keeps a stylesheet it has seen. Linked at an address that changes with what the
    file holds, a new one is fetched with the first page that wants it, and never an old one
    laid under new markup."""
    (address,) = re.findall(r'<link rel="stylesheet" href="([^"]+)">', dashboard.get("/").text)

    stylesheet = dashboard.get(address)

    assert stylesheet.status_code == 200
    assert hashlib.sha256(stylesheet.content).hexdigest()[:12] in address


def test_the_health_check_answers_without_reading_any_application(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    write_application("broken/2026-09-01-unreadable", "company: [unclosed\n")

    health = dashboard.get("/health")

    assert health.status_code == 200
    assert health.text == "ok"


def test_the_health_check_fails_when_the_data_directory_is_gone(
    dashboard: TestClient, data_dir: Path
) -> None:
    data_dir.rmdir()

    assert dashboard.get("/health").status_code == 503


@pytest.mark.parametrize("path", ["/", "/proposals", "/sources", "/documents"])
def test_every_page_links_an_icon_that_is_there(dashboard: TestClient, path: str) -> None:
    page = dashboard.get(path)

    linked = re.search(r'<link rel="icon"[^>]*href="([^"]+)"', page.text)
    assert linked, "the page links no icon"
    icon = dashboard.get(linked.group(1))
    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/svg+xml")
    # Asked for whatever a page links, by browsers and by tools that never read the page.
    asked_anyway = dashboard.get("/favicon.ico")
    assert asked_anyway.status_code == 200
    assert asked_anyway.content == icon.content
