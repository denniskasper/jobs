"""Shared Documents: stored once under their content hash, linked into Application folders."""

import hashlib
import os
import shutil
from pathlib import Path

from starlette.testclient import TestClient

from conftest import RememberedCommits

RESUME = b"%PDF-1.7 an invented resume"
CERTIFICATES = b"%PDF-1.7 an invented certificate bundle"

NORDWIND = "nordwind-robotics/2026-09-18-simulation-engineer"
HELIOS = "helios-antriebe/2026-09-18-test-engineer"


def add_document(dashboard: TestClient, name: str, content: bytes, *, called: str = "") -> None:
    added = dashboard.post(
        "/documents", files={"file": (name, content, "application/pdf")}, data={"name": called}
    )
    assert added.status_code == 200, added.text


def stored(data_dir: Path) -> list[Path]:
    return sorted((data_dir / "documents" / "store").iterdir())


def create_nordwind(dashboard: TestClient, data_dir: Path) -> Path:
    dashboard.post(
        "/applications", data={"company": "Nordwind Robotics", "role": "Simulation Engineer"}
    )
    return data_dir / "applications" / NORDWIND


def create_helios(dashboard: TestClient, data_dir: Path) -> Path:
    dashboard.post("/applications", data={"company": "Helios Antriebe", "role": "Test Engineer"})
    return data_dir / "applications" / HELIOS


def test_a_shared_document_added_twice_is_stored_once(
    dashboard: TestClient, data_dir: Path
) -> None:
    add_document(dashboard, "resume.pdf", RESUME)
    add_document(dashboard, "resume.pdf", RESUME)

    assert [path.name for path in stored(data_dir)] == [f"{hashlib.sha256(RESUME).hexdigest()}.pdf"]
    assert stored(data_dir)[0].read_bytes() == RESUME


def test_the_added_document_is_listed_with_its_hash(dashboard: TestClient) -> None:
    add_document(dashboard, "resume.pdf", RESUME)

    page = dashboard.get("/documents")

    assert "resume.pdf" in page.text
    assert hashlib.sha256(RESUME).hexdigest() in page.text


def test_creating_an_application_links_the_shared_documents_instead_of_copying_them(
    dashboard: TestClient, data_dir: Path
) -> None:
    add_document(dashboard, "resume.pdf", RESUME)
    add_document(dashboard, "certificates.pdf", CERTIFICATES)

    folder = create_nordwind(dashboard, data_dir)

    assert (folder / "resume.pdf").is_symlink()
    assert (folder / "certificates.pdf").is_symlink()
    assert (folder / "resume.pdf").read_bytes() == RESUME
    assert (folder / "certificates.pdf").read_bytes() == CERTIFICATES


def test_two_applications_with_the_same_document_share_one_copy_of_its_bytes(
    dashboard: TestClient, data_dir: Path
) -> None:
    add_document(dashboard, "resume.pdf", RESUME)

    nordwind = create_nordwind(dashboard, data_dir)
    helios = create_helios(dashboard, data_dir)

    assert (nordwind / "resume.pdf").resolve() == (helios / "resume.pdf").resolve()
    holding_the_bytes = [
        path
        for path in data_dir.rglob("*")
        if path.is_file() and not path.is_symlink() and path.read_bytes() == RESUME
    ]
    assert holding_the_bytes == stored(data_dir)


def test_the_links_do_not_depend_on_where_the_data_directory_is(
    dashboard: TestClient, data_dir: Path, tmp_path: Path
) -> None:
    add_document(dashboard, "resume.pdf", RESUME)
    create_nordwind(dashboard, data_dir)

    moved = tmp_path / "elsewhere" / "data"
    moved.parent.mkdir()
    data_dir.rename(moved)

    link = moved / "applications" / NORDWIND / "resume.pdf"
    assert not os.path.isabs(os.readlink(link))
    assert link.read_bytes() == RESUME


def test_a_document_added_under_its_own_name_can_be_linked_in_under_another(
    dashboard: TestClient, data_dir: Path
) -> None:
    add_document(dashboard, "cv-final-v7.pdf", RESUME, called="resume.pdf")

    folder = create_nordwind(dashboard, data_dir)

    assert (folder / "resume.pdf").read_bytes() == RESUME
    assert not (folder / "cv-final-v7.pdf").exists()


def test_a_new_version_reaches_new_applications_and_leaves_existing_ones_alone(
    dashboard: TestClient, data_dir: Path
) -> None:
    add_document(dashboard, "resume.pdf", RESUME)
    nordwind = create_nordwind(dashboard, data_dir)

    add_document(dashboard, "resume.pdf", b"%PDF-1.7 a revised invented resume")
    helios = create_helios(dashboard, data_dir)

    assert (nordwind / "resume.pdf").read_bytes() == RESUME
    assert (helios / "resume.pdf").read_bytes() == b"%PDF-1.7 a revised invented resume"


def test_a_document_unique_to_one_application_stays_an_ordinary_file(
    dashboard: TestClient, data_dir: Path
) -> None:
    add_document(dashboard, "resume.pdf", RESUME)
    nordwind = create_nordwind(dashboard, data_dir)
    letter = nordwind / "cover-letter.pdf"
    letter.write_bytes(b"%PDF-1.7 an invented cover letter")

    helios = create_helios(dashboard, data_dir)
    dashboard.post(f"/applications/{NORDWIND}/status", data={"status": "waiting"})

    assert letter.is_file() and not letter.is_symlink()
    assert not (helios / "cover-letter.pdf").exists()
    assert len(stored(data_dir)) == 1


def test_removing_an_application_leaves_the_documents_of_the_others_whole(
    dashboard: TestClient, data_dir: Path
) -> None:
    add_document(dashboard, "resume.pdf", RESUME)
    nordwind = create_nordwind(dashboard, data_dir)
    helios = create_helios(dashboard, data_dir)

    shutil.rmtree(nordwind)

    assert (helios / "resume.pdf").read_bytes() == RESUME
    assert "Helios Antriebe" in dashboard.get("/").text
    create_nordwind(dashboard, data_dir)
    assert (nordwind / "resume.pdf").read_bytes() == RESUME


def test_the_store_and_the_links_are_committed(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    add_document(dashboard, "resume.pdf", RESUME)
    folder = create_nordwind(dashboard, data_dir)

    added, created = committer.commits
    assert added.message == "Add shared Document resume.pdf"
    assert sorted(added.paths) == sorted(
        [*stored(data_dir), data_dir / "documents" / "shared.yaml"]
    )
    assert sorted(created.paths) == [folder / "application.yaml", folder / "resume.pdf"]


def test_nothing_writes_a_manifest(dashboard: TestClient, data_dir: Path) -> None:
    add_document(dashboard, "resume.pdf", RESUME)
    folder = create_nordwind(dashboard, data_dir)

    assert sorted(path.name for path in folder.iterdir()) == ["application.yaml", "resume.pdf"]
    assert not [path for path in data_dir.rglob("*") if "manifest" in path.name.lower()]


def test_a_document_without_a_usable_filename_is_refused(
    dashboard: TestClient, data_dir: Path
) -> None:
    for name in (
        "../escaped.pdf",
        "application.yaml",
        "posting.yaml",
        "letter.md",
        "conversation.yaml",
        ".hidden",
    ):
        refused = dashboard.post(
            "/documents", files={"file": ("resume.pdf", RESUME)}, data={"name": name}
        )
        assert refused.status_code == 422
        assert "Not added." in refused.text

    assert not (data_dir / "documents").exists()


def test_an_application_is_not_created_while_the_shared_documents_cannot_be_read(
    dashboard: TestClient, data_dir: Path
) -> None:
    add_document(dashboard, "resume.pdf", RESUME)
    stored(data_dir)[0].unlink()

    refused = dashboard.post(
        "/applications", data={"company": "Nordwind Robotics", "role": "Simulation Engineer"}
    )

    assert refused.status_code == 422
    assert "the store does not hold" in refused.text
    assert not (data_dir / "applications").exists()
    assert "the store does not hold" in dashboard.get("/documents").text


def test_a_name_too_long_for_a_filename_is_refused(dashboard: TestClient, data_dir: Path) -> None:
    refused = dashboard.post(
        "/documents", files={"file": ("resume.pdf", RESUME)}, data={"name": "r" * 252 + ".pdf"}
    )

    assert refused.status_code == 422
    assert not (data_dir / "documents").exists()


def test_adding_a_document_again_restores_bytes_that_were_saved_over_through_a_link(
    dashboard: TestClient, data_dir: Path
) -> None:
    add_document(dashboard, "resume.pdf", RESUME)
    folder = create_nordwind(dashboard, data_dir)
    (folder / "resume.pdf").write_bytes(b"saved over by a viewer")

    add_document(dashboard, "certificates.pdf", RESUME)

    assert (folder / "resume.pdf").read_bytes() == RESUME


def test_an_emptied_shared_file_shares_nothing_and_blocks_nothing(
    dashboard: TestClient, data_dir: Path
) -> None:
    add_document(dashboard, "resume.pdf", RESUME)
    (data_dir / "documents" / "shared.yaml").write_text("", encoding="utf-8")

    folder = create_nordwind(dashboard, data_dir)

    assert sorted(path.name for path in folder.iterdir()) == ["application.yaml"]


def test_links_left_uncommitted_go_with_the_next_change_to_the_application(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    add_document(dashboard, "resume.pdf", RESUME)
    folder = create_nordwind(dashboard, data_dir)
    (folder / "notes-link").symlink_to("application.yaml")

    dashboard.post(f"/applications/{NORDWIND}/status", data={"status": "waiting"})

    assert sorted(committer.commits[-1].paths) == [
        folder / "application.yaml",
        folder / "resume.pdf",
    ]
