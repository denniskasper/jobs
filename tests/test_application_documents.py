"""An Application's Documents: what it will send, in order, changed on its page until it is sent.

Every company, role and file here is invented.
"""

import re
from pathlib import Path
from typing import Any

import yaml
from starlette.testclient import TestClient

from conftest import RememberedCommits, WriteApplication

RESUME = b"%PDF-1.7 an invented resume"
CERTIFICATE = b"%PDF-1.7 an invented certificate"
PHOTO = b"\x89PNG\r\n\x1a\n an invented photo"
FORM = b"\xff\xd8\xff\xe0 an invented form, scanned"

NORDWIND = "nordwind-robotics/2026-09-18-simulation-engineer"
HELIOS = "helios-antriebe/2026-09-18-test-engineer"


def share(dashboard: TestClient, name: str, content: bytes) -> None:
    added = dashboard.post("/documents", files={"file": (name, content)})
    assert added.status_code == 200, added.text


def create(dashboard: TestClient, company: str, role: str) -> None:
    created = dashboard.post("/applications", data={"company": company, "role": role})
    assert created.status_code == 200, created.text


def create_nordwind(dashboard: TestClient) -> None:
    create(dashboard, "Nordwind Robotics", "Simulation Engineer")


def create_helios(dashboard: TestClient) -> None:
    create(dashboard, "Helios Antriebe", "Test Engineer")


def listed(dashboard: TestClient, folder: str) -> list[str]:
    """The Documents the Application's page lists, in the order it lists them."""
    page = dashboard.get(f"/applications/{folder}").text
    section = re.search(r'<section[^>]*id="documents".*?</section>', page, re.DOTALL)
    assert section, "the page has no Documents section"
    return re.findall(r'class="document-name">([^<]+)<', section[0])


def documents_section(dashboard: TestClient, folder: str) -> str:
    page = dashboard.get(f"/applications/{folder}").text
    section = re.search(r'<section[^>]*id="documents".*?</section>', page, re.DOTALL)
    assert section
    return section[0]


def record(data_dir: Path, folder: str) -> dict[str, Any]:
    text = (data_dir / "applications" / folder / "application.yaml").read_text(encoding="utf-8")
    loaded = yaml.safe_load(text)
    assert isinstance(loaded, dict)
    return loaded


def upload(dashboard: TestClient, folder: str, name: str, content: bytes, status: int = 200) -> str:
    uploaded = dashboard.post(
        f"/applications/{folder}/documents/upload", files={"file": (name, content)}
    )
    assert uploaded.status_code == status, uploaded.text
    text: str = uploaded.text
    return text


def add(dashboard: TestClient, folder: str, name: str, status: int = 200) -> str:
    added = dashboard.post(f"/applications/{folder}/documents/add", data={"name": name})
    assert added.status_code == status, added.text
    text: str = added.text
    return text


def remove(dashboard: TestClient, folder: str, name: str, status: int = 200) -> str:
    removed = dashboard.post(f"/applications/{folder}/documents/remove", data={"name": name})
    assert removed.status_code == status, removed.text
    text: str = removed.text
    return text


def move(dashboard: TestClient, folder: str, name: str, direction: str, status: int = 200) -> str:
    moved = dashboard.post(
        f"/applications/{folder}/documents/move", data={"name": name, "direction": direction}
    )
    assert moved.status_code == status, moved.text
    text: str = moved.text
    return text


def test_a_new_application_starts_with_every_shared_document_in_the_tabs_order(
    dashboard: TestClient, data_dir: Path
) -> None:
    share(dashboard, "zeugnis.pdf", CERTIFICATE)
    share(dashboard, "resume.pdf", RESUME)
    share(dashboard, "photo.png", PHOTO)

    create_nordwind(dashboard)

    assert listed(dashboard, NORDWIND) == ["photo.png", "resume.pdf", "zeugnis.pdf"]
    assert record(data_dir, NORDWIND)["documents"] == [
        {"shared": "photo.png"},
        {"shared": "resume.pdf"},
        {"shared": "zeugnis.pdf"},
    ]


def test_an_existing_application_keeps_the_shared_documents_it_is_linked_to(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    share(dashboard, "resume.pdf", RESUME)
    share(dashboard, "certificate.pdf", CERTIFICATE)
    share(dashboard, "photo.png", PHOTO)
    path = write_application(
        "tuhh/2026-08-01-research-engineer",
        "company: TUHH\nrole: Research Engineer\ndate: 2026-08-01\nstatus: todo\n",
    )
    folder = path.parent
    store = data_dir / "documents" / "store"
    (stored_resume,) = [each for each in store.iterdir() if each.read_bytes() == RESUME]
    (stored_photo,) = [each for each in store.iterdir() if each.read_bytes() == PHOTO]
    (folder / "resume.pdf").symlink_to(Path("../../../documents/store") / stored_resume.name)
    (folder / "photo.png").symlink_to(Path("../../../documents/store") / stored_photo.name)

    assert listed(dashboard, "tuhh/2026-08-01-research-engineer") == ["photo.png", "resume.pdf"]


def test_a_shared_document_can_be_added_to_an_application_made_before_it(
    dashboard: TestClient, data_dir: Path
) -> None:
    create_nordwind(dashboard)
    share(dashboard, "resume.pdf", RESUME)

    add(dashboard, NORDWIND, "resume.pdf")

    assert listed(dashboard, NORDWIND) == ["resume.pdf"]
    assert (data_dir / "applications" / NORDWIND / "resume.pdf").read_bytes() == RESUME


def test_the_page_offers_only_the_shared_documents_the_application_does_not_hold(
    dashboard: TestClient,
) -> None:
    share(dashboard, "resume.pdf", RESUME)
    create_nordwind(dashboard)
    share(dashboard, "certificate.pdf", CERTIFICATE)

    offered = re.findall(r'<option value="([^"]+)"', documents_section(dashboard, NORDWIND))

    assert offered == ["certificate.pdf"]


def test_removing_a_shared_document_leaves_the_tab_and_the_other_applications_alone(
    dashboard: TestClient, data_dir: Path
) -> None:
    share(dashboard, "resume.pdf", RESUME)
    share(dashboard, "certificate.pdf", CERTIFICATE)
    create_nordwind(dashboard)
    create_helios(dashboard)

    remove(dashboard, NORDWIND, "certificate.pdf")

    assert listed(dashboard, NORDWIND) == ["resume.pdf"]
    assert not (data_dir / "applications" / NORDWIND / "certificate.pdf").exists()
    assert listed(dashboard, HELIOS) == ["certificate.pdf", "resume.pdf"]
    assert "certificate.pdf" in dashboard.get("/documents").text
    assert any(
        each.read_bytes() == CERTIFICATE for each in (data_dir / "documents" / "store").iterdir()
    )


def test_a_file_uploaded_to_one_application_appears_only_there(
    dashboard: TestClient, data_dir: Path
) -> None:
    share(dashboard, "resume.pdf", RESUME)
    create_nordwind(dashboard)
    create_helios(dashboard)

    upload(dashboard, NORDWIND, "form.jpg", FORM)

    assert listed(dashboard, NORDWIND) == ["resume.pdf", "form.jpg"]
    held = data_dir / "applications" / NORDWIND / "form.jpg"
    assert held.read_bytes() == FORM and not held.is_symlink()
    assert listed(dashboard, HELIOS) == ["resume.pdf"]
    assert "form.jpg" not in dashboard.get("/documents").text
    assert record(data_dir, NORDWIND)["documents"][-1] == {"own": "form.jpg"}


def test_a_file_of_its_own_can_be_removed(dashboard: TestClient, data_dir: Path) -> None:
    create_nordwind(dashboard)
    upload(dashboard, NORDWIND, "tailored-resume.pdf", RESUME)

    remove(dashboard, NORDWIND, "tailored-resume.pdf")

    assert listed(dashboard, NORDWIND) == []
    assert not (data_dir / "applications" / NORDWIND / "tailored-resume.pdf").exists()


def test_documents_can_be_moved_up_and_down_and_the_order_is_kept(
    dashboard: TestClient, data_dir: Path
) -> None:
    share(dashboard, "certificate.pdf", CERTIFICATE)
    share(dashboard, "resume.pdf", RESUME)
    create_nordwind(dashboard)
    upload(dashboard, NORDWIND, "photo.png", PHOTO)

    move(dashboard, NORDWIND, "resume.pdf", "up")
    move(dashboard, NORDWIND, "photo.png", "up")
    move(dashboard, NORDWIND, "resume.pdf", "down")

    assert listed(dashboard, NORDWIND) == ["photo.png", "resume.pdf", "certificate.pdf"]
    assert [next(iter(each.values())) for each in record(data_dir, NORDWIND)["documents"]] == [
        "photo.png",
        "resume.pdf",
        "certificate.pdf",
    ]


def test_moving_the_first_one_up_changes_nothing(
    dashboard: TestClient, committer: RememberedCommits
) -> None:
    share(dashboard, "certificate.pdf", CERTIFICATE)
    share(dashboard, "resume.pdf", RESUME)
    create_nordwind(dashboard)
    made = len(committer.commits)

    move(dashboard, NORDWIND, "certificate.pdf", "up")
    move(dashboard, NORDWIND, "resume.pdf", "down")

    assert listed(dashboard, NORDWIND) == ["certificate.pdf", "resume.pdf"]
    assert len(committer.commits) == made


def test_an_upload_that_is_no_pdf_or_image_is_refused_on_the_application_page(
    dashboard: TestClient, data_dir: Path
) -> None:
    create_nordwind(dashboard)

    for name, content in (
        ("letter.docx", b"PK\x03\x04 an invented word file"),
        ("notes.txt", b"invented notes"),
        ("resume.pdf", b"PK\x03\x04 a word file named as a PDF"),
    ):
        refused = upload(dashboard, NORDWIND, name, content, status=422)
        assert "Not added." in refused
        assert "PDF, JPG or PNG" in refused

    assert listed(dashboard, NORDWIND) == []
    assert sorted(each.name for each in (data_dir / "applications" / NORDWIND).iterdir()) == [
        "application.yaml"
    ]


def test_an_upload_that_is_no_pdf_or_image_is_refused_on_the_documents_tab(
    dashboard: TestClient, data_dir: Path
) -> None:
    refused = dashboard.post(
        "/documents", files={"file": ("letter.docx", b"PK\x03\x04 an invented word file")}
    )

    assert refused.status_code == 422
    assert "Not added." in refused.text
    assert "PDF, JPG or PNG" in refused.text
    assert not (data_dir / "documents").exists()


def test_an_upload_under_a_name_the_application_holds_already_is_refused(
    dashboard: TestClient, data_dir: Path
) -> None:
    share(dashboard, "resume.pdf", RESUME)
    create_nordwind(dashboard)

    refused = upload(dashboard, NORDWIND, "resume.pdf", CERTIFICATE, status=422)

    assert "Not added." in refused
    assert (data_dir / "applications" / NORDWIND / "resume.pdf").read_bytes() == RESUME
    assert listed(dashboard, NORDWIND) == ["resume.pdf"]


def test_a_sent_application_lists_its_documents_and_offers_no_changes(
    dashboard: TestClient, data_dir: Path
) -> None:
    share(dashboard, "resume.pdf", RESUME)
    create_nordwind(dashboard)
    upload(dashboard, NORDWIND, "form.jpg", FORM)
    share(dashboard, "certificate.pdf", CERTIFICATE)
    dashboard.post(f"/applications/{NORDWIND}/status", data={"status": "waiting"})
    # Ever sent is sent, whatever it stands at now.
    dashboard.post(f"/applications/{NORDWIND}/status", data={"status": "todo"})

    section = documents_section(dashboard, NORDWIND)
    assert listed(dashboard, NORDWIND) == ["resume.pdf", "form.jpg"]
    assert "<form" not in section

    for refused in (
        add(dashboard, NORDWIND, "certificate.pdf", status=422),
        remove(dashboard, NORDWIND, "form.jpg", status=422),
        move(dashboard, NORDWIND, "form.jpg", "up", status=422),
        upload(dashboard, NORDWIND, "photo.png", PHOTO, status=422),
    ):
        assert "was sent" in refused
    assert listed(dashboard, NORDWIND) == ["resume.pdf", "form.jpg"]
    assert (data_dir / "applications" / NORDWIND / "form.jpg").exists()


def test_each_change_is_one_commit(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    folder = data_dir / "applications" / NORDWIND
    create_nordwind(dashboard)
    share(dashboard, "resume.pdf", RESUME)
    made = len(committer.commits)

    add(dashboard, NORDWIND, "resume.pdf")
    upload(dashboard, NORDWIND, "form.jpg", FORM)
    move(dashboard, NORDWIND, "form.jpg", "up")
    remove(dashboard, NORDWIND, "resume.pdf")

    added, uploaded, moved, removed = committer.commits[made:]
    record_path = folder / "application.yaml"
    assert sorted(added.paths) == [record_path, folder / "resume.pdf"]
    assert sorted(uploaded.paths) == [record_path, folder / "form.jpg"]
    assert moved.paths == [record_path]
    assert sorted(removed.paths) == [record_path, folder / "resume.pdf"]
    assert all(NORDWIND in commit.message for commit in (added, uploaded, moved, removed))


def test_a_change_that_could_not_be_committed_says_so(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    create_nordwind(dashboard)
    committer.commit_failure = "the disk is full"

    page = upload(dashboard, NORDWIND, "form.jpg", FORM, status=500)

    assert "could not be committed" in page
    assert "the disk is full" in page
    assert listed(dashboard, NORDWIND) == ["form.jpg"]
