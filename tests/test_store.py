"""Bringing the store up to date at start, against real repositories in a temporary directory."""

import os
from pathlib import Path

import pytest

from conftest import git
from jobs.git import is_repository_root
from jobs.store import Origin, StoreNotUsable, bring_up_to_date

TOKEN = "not-a-real-token"


def commit_file(repository: Path, name: str, text: str) -> None:
    (repository / name).write_text(text, encoding="utf-8")
    git(repository, "add", name)
    git(
        repository,
        "-c",
        "user.name=A Human",
        "-c",
        "user.email=human@localhost",
        "commit",
        "--quiet",
        "--message",
        f"Write {name}",
    )


def state_of(repository: Path) -> tuple[str, str, str]:
    """The commit a repository is on, its branch with everything not committed, and how it is
    configured."""
    return (
        git(repository, "rev-parse", "HEAD"),
        git(repository, "status", "--porcelain", "-b"),
        (repository / ".git" / "config").read_text(encoding="utf-8"),
    )


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    """Stands in for the data repository on GitHub: a repository with one commit."""
    root = tmp_path / "upstream"
    root.mkdir()
    git(root, "init", "--quiet", "--initial-branch=main")
    commit_file(root, "README.md", "The data.\n")
    return root


@pytest.fixture
def origin(upstream: Path) -> Origin:
    return Origin(url=str(upstream), token=TOKEN)


def test_an_empty_volume_is_filled_with_a_clone(data_dir: Path, origin: Origin) -> None:
    bring_up_to_date(data_dir, origin)

    assert (data_dir / "README.md").read_text(encoding="utf-8") == "The data.\n"
    assert git(data_dir, "log", "--format=%s").splitlines() == ["Write README.md"]


def test_a_later_start_brings_in_what_was_pushed_since(
    data_dir: Path, origin: Origin, upstream: Path
) -> None:
    bring_up_to_date(data_dir, origin)
    commit_file(upstream, "profile.md", "Pushed from elsewhere.\n")

    bring_up_to_date(data_dir, origin)

    assert (data_dir / "profile.md").read_text(encoding="utf-8") == "Pushed from elsewhere.\n"


def test_commits_the_server_has_not_pushed_survive_a_restart(
    data_dir: Path, origin: Origin
) -> None:
    bring_up_to_date(data_dir, origin)
    commit_file(data_dir, "application.yaml", "company: Nordwind Robotics\n")

    bring_up_to_date(data_dir, origin)

    assert git(data_dir, "log", "--format=%s").splitlines() == [
        "Write application.yaml",
        "Write README.md",
    ]


def test_a_store_that_cannot_be_updated_still_starts_and_says_why(
    data_dir: Path, origin: Origin, upstream: Path
) -> None:
    bring_up_to_date(data_dir, origin)
    commit_file(data_dir, "application.yaml", "company: Nordwind Robotics\n")
    commit_file(upstream, "profile.md", "Pushed from elsewhere.\n")

    warning = bring_up_to_date(data_dir, origin)

    assert warning is not None
    assert (data_dir / "application.yaml").exists()
    assert not (data_dir / "profile.md").exists()


def test_an_unreachable_origin_does_not_stop_an_existing_store(
    data_dir: Path, origin: Origin, tmp_path: Path
) -> None:
    bring_up_to_date(data_dir, origin)

    warning = bring_up_to_date(data_dir, Origin(url=str(tmp_path / "gone"), token=TOKEN))

    assert warning is not None
    assert (data_dir / "README.md").exists()


def test_an_empty_volume_with_an_unreachable_origin_refuses_to_start(
    data_dir: Path, tmp_path: Path
) -> None:
    with pytest.raises(StoreNotUsable):
        bring_up_to_date(data_dir, Origin(url=str(tmp_path / "gone"), token=TOKEN))


def test_a_clone_that_failed_leaves_the_volume_ready_for_the_next_start(
    data_dir: Path, origin: Origin, tmp_path: Path
) -> None:
    with pytest.raises(StoreNotUsable):
        bring_up_to_date(data_dir, Origin(url=str(tmp_path / "gone"), token=TOKEN))

    bring_up_to_date(data_dir, origin)

    assert (data_dir / "README.md").exists()


def test_files_that_are_not_a_repository_are_never_cloned_over(
    data_dir: Path, origin: Origin
) -> None:
    (data_dir / "notes.txt").write_text("Left here by hand.\n", encoding="utf-8")

    with pytest.raises(StoreNotUsable):
        bring_up_to_date(data_dir, origin)

    assert (data_dir / "notes.txt").read_text(encoding="utf-8") == "Left here by hand.\n"


def test_the_token_is_never_written_into_the_volume(data_dir: Path, origin: Origin) -> None:
    bring_up_to_date(data_dir, origin)

    kept = [path for path in data_dir.rglob("*") if path.is_file()]
    assert all(TOKEN.encode() not in path.read_bytes() for path in kept)


def test_without_an_origin_a_missing_directory_becomes_a_repository(tmp_path: Path) -> None:
    missing = tmp_path / "not" / "there" / "yet"

    assert bring_up_to_date(missing, None) is None

    assert is_repository_root(missing)


def test_without_an_origin_an_empty_directory_becomes_a_repository(data_dir: Path) -> None:
    assert bring_up_to_date(data_dir, None) is None

    assert is_repository_root(data_dir)


def test_without_an_origin_files_that_are_not_a_repository_are_never_adopted(
    data_dir: Path,
) -> None:
    (data_dir / "notes.txt").write_text("Left here by hand.\n", encoding="utf-8")

    with pytest.raises(StoreNotUsable, match="neither empty nor a git repository"):
        bring_up_to_date(data_dir, None)

    assert [path.name for path in data_dir.iterdir()] == ["notes.txt"]
    assert (data_dir / "notes.txt").read_text(encoding="utf-8") == "Left here by hand.\n"


def test_without_an_origin_an_existing_repository_is_left_exactly_as_it_is(
    data_dir: Path,
) -> None:
    git(data_dir, "init", "--quiet", "--initial-branch=trunk")
    commit_file(data_dir, "application.yaml", "company: Nordwind Robotics\n")
    (data_dir / "notes.txt").write_text("Not committed yet.\n", encoding="utf-8")
    before = state_of(data_dir)

    assert bring_up_to_date(data_dir, None) is None

    assert state_of(data_dir) == before
    assert (data_dir / "notes.txt").read_text(encoding="utf-8") == "Not committed yet.\n"


def test_a_missing_volume_with_an_unreachable_origin_refuses_to_start(tmp_path: Path) -> None:
    with pytest.raises(StoreNotUsable):
        bring_up_to_date(tmp_path / "missing", Origin(url=str(tmp_path / "gone"), token=TOKEN))

    assert not (tmp_path / "missing").exists()


def test_without_an_origin_an_empty_directory_inside_another_repository_becomes_its_own(
    tmp_path: Path,
) -> None:
    engine = tmp_path / "engine"
    store = engine / "data"
    store.mkdir(parents=True)
    git(engine, "init", "--quiet", "--initial-branch=main")

    bring_up_to_date(store, None)

    assert is_repository_root(store)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every directory")
def test_without_an_origin_a_directory_that_cannot_be_read_refuses_to_start(
    data_dir: Path,
) -> None:
    data_dir.chmod(0o000)
    try:
        with pytest.raises(StoreNotUsable, match="could not be made a git repository"):
            bring_up_to_date(data_dir, None)
    finally:
        data_dir.chmod(0o700)
