"""The Committer that runs git pushes what it commits, against a real data repository in a
temporary directory."""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from conftest import SettableClock, git
from jobs.commits import GitCommitter
from jobs.fetching import Offline
from jobs.models import NoModel
from jobs.store import Origin, bring_up_to_date
from jobs.web import create_app

TOKEN = "not-a-real-token"
FOLDER = "nordwind-robotics/2026-09-18-simulation-engineer"
CREATED = f"Create Application {FOLDER}"
MOVED = f"Change Status of {FOLDER} from todo to waiting"


def clone_of(upstream: Path, into: Path) -> Path:
    git(into.parent, "clone", "--quiet", str(upstream), into.name)
    git(into, "config", "user.name", "A Human")
    git(into, "config", "user.email", "human@localhost")
    return into


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    """Stands in for the data repository on GitHub: a bare repository with one commit."""
    root = tmp_path / "upstream.git"
    git(tmp_path, "init", "--quiet", "--bare", "--initial-branch=main", root.name)
    laptop = clone_of(root, tmp_path / "seed")
    (laptop / "README.md").write_text("The data.\n", encoding="utf-8")
    git(laptop, "add", "README.md")
    git(laptop, "commit", "--quiet", "--message", "Start the data repository")
    git(laptop, "push", "--quiet", "origin", "HEAD:main")
    return root


@pytest.fixture
def origin(upstream: Path) -> Origin:
    return Origin(url=str(upstream), token=TOKEN)


@pytest.fixture
def dashboard(data_dir: Path, origin: Origin, clock: SettableClock) -> TestClient:
    bring_up_to_date(data_dir, origin)
    return TestClient(
        create_app(
            data_dir=data_dir,
            clock=clock,
            network=Offline(),
            model=NoModel(),
            committer=GitCommitter(data_dir, origin),
        )
    )


def create(dashboard: TestClient) -> None:
    created = dashboard.post(
        "/applications",
        data={"company": "Nordwind Robotics", "role": "Simulation Engineer"},
        follow_redirects=False,
    )
    assert created.status_code == 303


def move_to_waiting(dashboard: TestClient) -> None:
    moved = dashboard.post(
        f"/applications/{FOLDER}/status", data={"status": "waiting"}, follow_redirects=False
    )
    assert moved.status_code == 303


def test_a_committed_mutation_reaches_the_data_repository(
    dashboard: TestClient, upstream: Path
) -> None:
    create(dashboard)

    assert git(upstream, "log", "--format=%s", "main").splitlines() == [
        CREATED,
        "Start the data repository",
    ]
    assert "Not pushed." not in dashboard.get("/").text


def test_a_push_that_fails_leaves_the_mutation_standing_and_is_reported(
    dashboard: TestClient, data_dir: Path, upstream: Path, tmp_path: Path
) -> None:
    upstream.rename(tmp_path / "unreachable.git")

    create(dashboard)

    assert git(data_dir, "log", "-1", "--format=%s") == f"{CREATED}\n"
    assert git(data_dir, "status", "--porcelain") == ""
    page = dashboard.get("/")
    assert "Nordwind Robotics" in page.text
    assert "Not pushed." in page.text


def test_the_next_push_that_succeeds_carries_the_commits_left_behind(
    dashboard: TestClient, upstream: Path, tmp_path: Path
) -> None:
    upstream.rename(tmp_path / "unreachable.git")
    create(dashboard)
    (tmp_path / "unreachable.git").rename(upstream)

    move_to_waiting(dashboard)

    assert git(upstream, "log", "--format=%s", "main").splitlines() == [
        MOVED,
        CREATED,
        "Start the data repository",
    ]
    assert "Not pushed." not in dashboard.get("/").text


def test_a_data_repository_that_has_moved_ahead_is_reported_and_not_overwritten(
    dashboard: TestClient, data_dir: Path, upstream: Path, tmp_path: Path
) -> None:
    elsewhere = clone_of(upstream, tmp_path / "elsewhere")
    (elsewhere / "profile.md").write_text("Pushed from elsewhere.\n", encoding="utf-8")
    git(elsewhere, "add", "profile.md")
    git(elsewhere, "commit", "--quiet", "--message", "Write profile.md")
    git(elsewhere, "push", "--quiet", "origin", "HEAD:main")

    create(dashboard)

    assert git(upstream, "log", "--format=%s", "main").splitlines() == [
        "Write profile.md",
        "Start the data repository",
    ]
    assert git(data_dir, "log", "-1", "--format=%s") == f"{CREATED}\n"
    assert "has commits this server does not have" in dashboard.get("/").text


def test_commits_left_behind_by_an_earlier_run_are_pushed_without_a_new_mutation(
    dashboard: TestClient, data_dir: Path, origin: Origin, upstream: Path, tmp_path: Path
) -> None:
    upstream.rename(tmp_path / "unreachable.git")
    create(dashboard)
    (tmp_path / "unreachable.git").rename(upstream)

    restarted = GitCommitter(data_dir, origin)
    restarted.push()

    assert restarted.not_pushed() is None
    assert git(upstream, "log", "-1", "--format=%s", "main") == f"{CREATED}\n"


def test_without_an_origin_nothing_is_pushed_and_nothing_is_reported(
    data_dir: Path, origin: Origin, upstream: Path, clock: SettableClock
) -> None:
    bring_up_to_date(data_dir, origin)
    committer = GitCommitter(data_dir)
    dashboard = TestClient(
        create_app(
            data_dir=data_dir,
            clock=clock,
            network=Offline(),
            committer=committer,
            model=NoModel(),
        )
    )

    create(dashboard)

    assert git(upstream, "log", "--format=%s", "main").splitlines() == ["Start the data repository"]
    assert committer.not_pushed() is None


def test_pushing_never_writes_the_token_into_the_volume(
    dashboard: TestClient, data_dir: Path
) -> None:
    create(dashboard)

    kept = [path for path in data_dir.rglob("*") if path.is_file()]
    assert all(TOKEN.encode() not in path.read_bytes() for path in kept)
