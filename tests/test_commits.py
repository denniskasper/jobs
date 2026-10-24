"""Every mutation of the data is committed: what is committed, and what is not."""

from pathlib import Path

from starlette.testclient import TestClient

from conftest import Commit, RememberedCommits, WriteApplication

FOLDER = "nordwind-robotics/2026-09-18-simulation-engineer"


def record_path(data_dir: Path, folder: str) -> Path:
    return data_dir / "applications" / folder / "application.yaml"


def test_creating_an_application_is_committed_naming_the_application(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    dashboard.post(
        "/applications", data={"company": "Nordwind Robotics", "role": "Simulation Engineer"}
    )

    assert committer.commits == [
        Commit(
            message=f"Create Application {FOLDER}",
            paths=[record_path(data_dir, FOLDER)],
        )
    ]


RECORD = """\
company: Nordwind Robotics
role: Simulation Engineer
date: 2026-09-18
status: todo
"""


def test_changing_a_status_is_committed_naming_the_application_and_the_change(
    dashboard: TestClient, write_application: WriteApplication, committer: RememberedCommits
) -> None:
    path = write_application(FOLDER, RECORD)

    dashboard.post(f"/applications/{FOLDER}/status", data={"status": "waiting"})

    assert committer.commits == [
        Commit(message=f"Change Status of {FOLDER} from todo to waiting", paths=[path])
    ]


def test_a_status_change_that_changes_nothing_is_not_committed(
    dashboard: TestClient, write_application: WriteApplication, committer: RememberedCommits
) -> None:
    write_application(FOLDER, RECORD)

    dashboard.post(f"/applications/{FOLDER}/status", data={"status": "todo"})

    assert committer.commits == []


def test_an_application_that_could_not_be_written_is_not_committed(
    dashboard: TestClient, committer: RememberedCommits
) -> None:
    refused = dashboard.post("/applications", data={"company": "Nordwind", "role": "x" * 300})

    assert refused.status_code == 422
    assert committer.commits == []


def test_a_refused_status_change_is_not_committed(
    dashboard: TestClient, write_application: WriteApplication, committer: RememberedCommits
) -> None:
    write_application(FOLDER, RECORD)

    dashboard.post(f"/applications/{FOLDER}/status", data={"status": "ghosted"})

    assert committer.commits == []


def test_commits_that_were_not_pushed_are_reported_on_the_dashboard(
    dashboard: TestClient, committer: RememberedCommits
) -> None:
    committer.push_failure = "the data repository could not be reached"

    page = dashboard.get("/")

    assert page.status_code == 200
    assert "Not pushed." in page.text
    assert "the data repository could not be reached" in page.text


def test_a_dashboard_whose_commits_were_all_pushed_says_nothing_about_pushing(
    dashboard: TestClient,
) -> None:
    assert "Not pushed." not in dashboard.get("/").text
