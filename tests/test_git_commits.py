"""The Committer that runs git, against a real repository in a temporary directory."""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from conftest import NOW, SettableClock, git
from jobs.commits import GitCommitter, NotARepository
from jobs.fetching import Offline
from jobs.models import NoModel
from jobs.store import bring_up_to_date
from jobs.web import create_app
from test_deletion import confirm
from test_proposals import write_posting

FOLDER = "nordwind-robotics/2026-09-18-simulation-engineer"
PURSUED = "hafenstadt-institute-of-technology/2026-09-18-doctoral-researcher"


@pytest.fixture
def repository(data_dir: Path) -> Path:
    """The data directory as a repository with one commit made by a human."""
    git(data_dir, "init", "--quiet", "--initial-branch=main")
    git(data_dir, "config", "user.name", "A Human")
    git(data_dir, "config", "user.email", "human@localhost")
    (data_dir / "README.md").write_text("The data.\n", encoding="utf-8")
    git(data_dir, "add", "README.md")
    git(data_dir, "commit", "--quiet", "--message", "Start the data repository")
    return data_dir


@pytest.fixture
def dashboard(repository: Path, clock: SettableClock) -> TestClient:
    return TestClient(
        create_app(
            data_dir=repository,
            clock=clock,
            network=Offline(),
            model=NoModel(),
            committer=GitCommitter(repository),
        )
    )


def create_and_move_to_waiting(dashboard: TestClient) -> None:
    dashboard.post(
        "/applications", data={"company": "Nordwind Robotics", "role": "Simulation Engineer"}
    )
    dashboard.post(f"/applications/{FOLDER}/status", data={"status": "waiting"})


def test_creating_an_application_and_changing_its_status_leaves_two_commits(
    dashboard: TestClient, repository: Path
) -> None:
    create_and_move_to_waiting(dashboard)

    assert git(repository, "log", "--format=%s").splitlines() == [
        f"Change Status of {FOLDER} from todo to waiting",
        f"Create Application {FOLDER}",
        "Start the data repository",
    ]
    assert git(repository, "status", "--porcelain") == ""
    committed = git(repository, "show", f"HEAD:applications/{FOLDER}/application.yaml")
    assert "status: waiting" in committed


def test_system_commits_are_attributed_to_the_system_not_to_the_human(
    dashboard: TestClient, repository: Path
) -> None:
    create_and_move_to_waiting(dashboard)

    people = git(repository, "log", "--format=%an <%ae> / %cn <%ce>").splitlines()
    assert people == [
        "jobs <jobs@localhost> / jobs <jobs@localhost>",
        "jobs <jobs@localhost> / jobs <jobs@localhost>",
        "A Human <human@localhost> / A Human <human@localhost>",
    ]


def test_a_humans_uncommitted_edits_survive_a_system_write_and_stay_out_of_its_commit(
    dashboard: TestClient, repository: Path
) -> None:
    (repository / "README.md").write_text("The data, edited by hand.\n", encoding="utf-8")
    (repository / "staged.md").write_text("Staged by hand.\n", encoding="utf-8")
    git(repository, "add", "staged.md")
    (repository / "untracked.md").write_text("Not yet added.\n", encoding="utf-8")

    create_and_move_to_waiting(dashboard)

    assert git(repository, "status", "--porcelain").splitlines() == [
        " M README.md",
        "A  staged.md",
        "?? untracked.md",
    ]
    assert (repository / "README.md").read_text(encoding="utf-8") == "The data, edited by hand.\n"
    touched = git(repository, "log", "-2", "--name-only", "--format=").split()
    assert touched == [f"applications/{FOLDER}/application.yaml"] * 2


def test_a_record_edited_by_hand_keeps_the_edit_when_the_system_next_writes_it(
    dashboard: TestClient, repository: Path
) -> None:
    dashboard.post(
        "/applications", data={"company": "Nordwind Robotics", "role": "Simulation Engineer"}
    )
    record = repository / "applications" / FOLDER / "application.yaml"
    with record.open("a", encoding="utf-8") as file:
        file.write("contact: someone at the fair\n")

    dashboard.post(f"/applications/{FOLDER}/status", data={"status": "waiting"})

    assert git(repository, "status", "--porcelain") == ""
    assert "contact: someone at the fair" in git(
        repository, "show", f"HEAD:applications/{FOLDER}/application.yaml"
    )


def test_a_commit_that_fails_is_reported_and_leaves_nothing_staged(
    dashboard: TestClient, repository: Path
) -> None:
    (repository / ".git" / "index.lock").touch()  # what another git process leaves while it runs

    failed = dashboard.post(
        "/applications", data={"company": "Nordwind Robotics", "role": "Simulation Engineer"}
    )

    assert failed.status_code == 500
    assert f"{FOLDER} was written but could not be committed" in failed.text
    assert git(repository, "log", "--format=%s").splitlines() == ["Start the data repository"]
    assert git(repository, "status", "--porcelain").splitlines() == ["?? applications/"]


def test_a_data_directory_that_is_not_the_root_of_a_repository_is_refused(
    repository: Path, tmp_path: Path
) -> None:
    nested = repository / "nested"
    nested.mkdir()

    with pytest.raises(NotARepository):
        GitCommitter(nested)
    with pytest.raises(NotARepository):
        GitCommitter(tmp_path)


def test_git_variables_inherited_from_the_environment_do_not_redirect_the_commit(
    dashboard: TestClient, repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    git(elsewhere, "init", "--quiet", "--initial-branch=main")
    monkeypatch.setenv("GIT_DIR", str(elsewhere / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(elsewhere / ".git" / "index"))

    dashboard.post(
        "/applications", data={"company": "Nordwind Robotics", "role": "Simulation Engineer"}
    )

    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_INDEX_FILE")
    assert git(repository, "log", "-1", "--format=%s") == f"Create Application {FOLDER}\n"
    assert git(repository, "status", "--porcelain") == ""
    assert git(elsewhere, "count-objects") == "0 objects, 0 kilobytes\n"


def test_a_commit_that_git_turns_down_leaves_the_record_unstaged(
    dashboard: TestClient, repository: Path
) -> None:
    hook = repository / ".git" / "hooks" / "prepare-commit-msg"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    failed = dashboard.post(
        "/applications", data={"company": "Nordwind Robotics", "role": "Simulation Engineer"}
    )

    assert failed.status_code == 500
    assert git(repository, "status", "--porcelain").splitlines() == ["?? applications/"]


def test_linked_documents_open_in_a_clone_on_another_machine(
    dashboard: TestClient, repository: Path, tmp_path: Path
) -> None:
    dashboard.post("/documents", files={"file": ("resume.pdf", b"%PDF-1.7 an invented resume")})
    create_and_move_to_waiting(dashboard)

    clone = tmp_path / "another-machine" / "data"
    git(tmp_path, "clone", "--quiet", str(repository), str(clone))

    assert git(repository, "status", "--porcelain") == ""
    link = clone / "applications" / FOLDER / "resume.pdf"
    assert link.is_symlink()
    assert link.read_bytes() == b"%PDF-1.7 an invented resume"


def test_ticking_a_proposal_leaves_one_commit_and_nothing_uncommitted(
    dashboard: TestClient, repository: Path
) -> None:
    write_posting(repository, "doctoral-researcher", first_shown=NOW)
    git(repository, "add", "postings")
    git(repository, "commit", "--quiet", "--message", "Keep a Posting")

    dashboard.post("/proposals", data={"posting": "hafenstadt/doctoral-researcher"})

    assert git(repository, "log", "--format=%s").splitlines() == [
        "Pursue Doctoral researcher at Hafenstadt Institute of Technology",
        "Keep a Posting",
        "Start the data repository",
    ]
    assert git(repository, "status", "--porcelain") == ""


def test_a_tick_that_cannot_be_committed_says_what_is_left_on_disk(
    dashboard: TestClient, repository: Path
) -> None:
    write_posting(repository, "doctoral-researcher", first_shown=NOW)
    (repository / ".git" / "index.lock").touch()

    failed = dashboard.post("/proposals", data={"posting": "hafenstadt/doctoral-researcher"})

    assert failed.status_code == 500
    assert f"{PURSUED} was created but could not be committed" in failed.text
    assert "the mark on the Posting" in failed.text
    assert (repository / "applications" / PURSUED / "posting.yaml").is_file()


def test_an_application_created_in_a_store_the_service_made_itself_is_committed(
    tmp_path: Path, clock: SettableClock
) -> None:
    store = tmp_path / "first-start"
    bring_up_to_date(store, None)
    dashboard = TestClient(
        create_app(
            data_dir=store,
            clock=clock,
            network=Offline(),
            model=NoModel(),
            committer=GitCommitter(store),
        )
    )

    created = dashboard.post(
        "/applications",
        data={"company": "Nordwind Robotics", "role": "Simulation Engineer"},
        follow_redirects=False,
    )

    assert created.status_code == 303
    assert git(store, "log", "--format=%s / %an <%ae>").splitlines() == [
        f"Create Application {FOLDER} / jobs <jobs@localhost>"
    ]
    assert git(store, "status", "--porcelain") == ""
    page = dashboard.get("/")
    assert "Nordwind Robotics" in page.text
    assert "Not pushed." not in page.text


def test_a_deletion_commits_the_removal_of_the_folder_and_nothing_else(
    dashboard: TestClient, repository: Path
) -> None:
    dashboard.post("/documents", files={"file": ("resume.pdf", b"%PDF-1.7 an invented resume")})
    dashboard.post(
        "/applications", data={"company": "Nordwind Robotics", "role": "Simulation Engineer"}
    )
    dashboard.post(f"/applications/{FOLDER}", data={"notes": "A test while trying the app out."})
    (repository / "README.md").write_text("The data, edited by hand.\n", encoding="utf-8")

    deleted = confirm(dashboard, FOLDER)

    assert deleted.status_code == 303
    assert git(repository, "log", "-1", "--format=%s").startswith("Delete Application")
    assert git(repository, "ls-tree", "-r", "--name-only", "HEAD", "applications") == ""
    assert git(repository, "status", "--porcelain").splitlines() == [" M README.md"]
    assert git(repository, "ls-tree", "-r", "--name-only", "HEAD", "documents") != ""
    touched = git(repository, "log", "-1", "--name-status", "--format=").splitlines()
    assert sorted(touched) == [
        f"D\tapplications/{FOLDER}/application.yaml",
        f"D\tapplications/{FOLDER}/resume.pdf",
    ]
