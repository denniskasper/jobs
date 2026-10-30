"""A Packet: an Application's Letter and Documents built into the files a portal or an email takes,
in one of four shapes, written into its folder and offered for download.

Every company, role, applicant, Letter and file here is invented.
"""

import html
import io
import re
from pathlib import Path

import pypdf
import pytest
from PIL import Image
from starlette.testclient import TestClient

from conftest import (
    PDF,
    Pages,
    RememberedCommits,
    RememberedPages,
    ScriptedModel,
    SettableClock,
    WriteApplication,
    fetch_next_answer,
    pdf_of,
)
from jobs.printing import ChromiumPrinter
from jobs.web import create_app
from test_fetching import no_chromium
from test_letter_revision import ANSWER, BODY, FOLDER, PAGE, RECORD, part, write_brief

RESUME = pdf_of("Resume of Erika Mustermann", "Resume, second page")
CERTIFICATE = pdf_of("Certificate in welding")
ZEUGNIS = pdf_of("Zeugnis from a shipyard")

LETTER_TEXT = "Printed by the fixtures"

SHAPES = ("everything", "letter-and-rest", "letter-resume-and-rest", "each")


def photo() -> bytes:
    """A real PNG, wider than it is high."""
    out = io.BytesIO()
    Image.new("RGB", (300, 200), (30, 60, 200)).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture(name="ready")
def ready_fixture(
    dashboard: TestClient, write_application: WriteApplication, data_dir: Path
) -> Path:
    return ready(dashboard, write_application, data_dir)


def ready(dashboard: TestClient, write_application: WriteApplication, data_dir: Path) -> Path:
    """An Application that is `todo`, has a Letter and holds a certificate, a resume and a
    Zeugnis, in that order, from an applicant whose surname is known. Gives back its folder."""
    write_application(FOLDER, RECORD)
    say_who(data_dir)
    edited = dashboard.post(f"{PAGE}/letter/edit", data={"body": BODY})
    assert edited.status_code == 200, edited.text
    for name, content in (
        ("certificate.pdf", CERTIFICATE),
        ("Lebenslauf.pdf", RESUME),
        ("zeugnis.pdf", ZEUGNIS),
    ):
        upload(dashboard, name, content)
    return data_dir / "applications" / FOLDER


def say_who(data_dir: Path, text: str = "surname: Mustermann\n") -> None:
    """Say who the Letters are from, as the applicant does: by hand, in the data directory."""
    (data_dir / "letter").mkdir(exist_ok=True)
    (data_dir / "letter" / "applicant.yaml").write_text(text, encoding="utf-8")


def upload(dashboard: TestClient, name: str, content: bytes) -> None:
    uploaded = dashboard.post(f"{PAGE}/documents/upload", files={"file": (name, content)})
    assert uploaded.status_code == 200, uploaded.text


def build(dashboard: TestClient, shape: str | None = None, status: int = 200) -> str:
    """Build a Packet from the form the page offers, in `shape` or the one it has chosen."""
    page = dashboard.get(PAGE).text
    (action,) = re.findall(
        r'<form[^>]*action="([^"]+)"[^>]*>(?:(?!</form>).)*>Build<', page, re.DOTALL
    )
    built = dashboard.post(html.unescape(action), data={"shape": shape} if shape else {})
    assert built.status_code == status, built.text
    text: str = built.text
    return text


def built_files(folder: Path) -> list[str]:
    """The files built into the Packet, without what it keeps about what it was built from."""
    packet = folder / "packet"
    if not packet.is_dir():
        return []
    return sorted(each.name for each in packet.iterdir() if not each.name.startswith("."))


def offered(page: str) -> dict[str, str]:
    """The built files the page offers for download, by name, in the order it lists them."""
    found = re.findall(r'<a[^>]*class="packet-file"[^>]*href="([^"]+)"[^>]*>([^<]+)<', page)
    return {name: html.unescape(address) for address, name in found}


def texts(content: bytes) -> list[str]:
    """The text of each page of a PDF, in order."""
    return [page.extract_text().strip() for page in pypdf.PdfReader(io.BytesIO(content)).pages]


def test_the_page_offers_the_four_shapes_with_the_letter_the_resume_and_the_rest_chosen(
    dashboard: TestClient, ready: Path
) -> None:
    page = dashboard.get(PAGE).text
    values = re.findall(r'<input[^>]*name="shape"[^>]*value="([^"]+)"', page)
    assert values == list(SHAPES)
    (checked,) = re.findall(r'<input[^>]*name="shape"[^>]*value="([^"]+)"[^>]*checked', page)
    assert checked == "letter-resume-and-rest"


def test_the_default_builds_the_letter_the_resume_and_the_rest(
    dashboard: TestClient, ready: Path
) -> None:
    build(dashboard)

    assert built_files(ready) == [
        "Mustermann_Anlagen.pdf",
        "Mustermann_Anschreiben.pdf",
        "Mustermann_Lebenslauf.pdf",
    ]
    packet = ready / "packet"
    assert texts((packet / "Mustermann_Anschreiben.pdf").read_bytes()) == [LETTER_TEXT]
    assert texts((packet / "Mustermann_Lebenslauf.pdf").read_bytes()) == texts(RESUME)
    assert texts((packet / "Mustermann_Anlagen.pdf").read_bytes()) == [
        "Certificate in welding",
        "Zeugnis from a shipyard",
    ]
    # The Documents stay as they are, apart from what was built.
    assert (ready / "Lebenslauf.pdf").read_bytes() == RESUME


@pytest.mark.parametrize(
    ("shape", "files"),
    [
        ("everything", ["Mustermann_Bewerbung.pdf"]),
        ("letter-and-rest", ["Mustermann_Anlagen.pdf", "Mustermann_Anschreiben.pdf"]),
        (
            "letter-resume-and-rest",
            ["Mustermann_Anlagen.pdf", "Mustermann_Anschreiben.pdf", "Mustermann_Lebenslauf.pdf"],
        ),
        (
            "each",
            ["Lebenslauf.pdf", "Mustermann_Anschreiben.pdf", "certificate.pdf", "zeugnis.pdf"],
        ),
    ],
)
def test_each_shape_builds_its_files_under_their_names(
    dashboard: TestClient, ready: Path, shape: str, files: list[str]
) -> None:
    build(dashboard, shape)

    assert built_files(ready) == files


def test_everything_in_one_has_the_letter_first_the_resume_next_and_the_rest_in_order(
    dashboard: TestClient, ready: Path
) -> None:
    build(dashboard, "everything")

    assert texts((ready / "packet" / "Mustermann_Bewerbung.pdf").read_bytes()) == [
        LETTER_TEXT,
        "Resume of Erika Mustermann",
        "Resume, second page",
        "Certificate in welding",
        "Zeugnis from a shipyard",
    ]


def test_the_rest_follows_the_order_of_the_documents_with_the_resume_first(
    dashboard: TestClient, ready: Path
) -> None:
    for _ in range(2):
        moved = dashboard.post(
            f"{PAGE}/documents/move", data={"name": "zeugnis.pdf", "direction": "up"}
        )
        assert moved.status_code == 200, moved.text

    build(dashboard, "letter-and-rest")

    assert texts((ready / "packet" / "Mustermann_Anlagen.pdf").read_bytes()) == [
        "Resume of Erika Mustermann",
        "Resume, second page",
        "Zeugnis from a shipyard",
        "Certificate in welding",
    ]


def test_one_file_per_document_keeps_each_documents_bytes(
    dashboard: TestClient, ready: Path
) -> None:
    picture = photo()
    upload(dashboard, "photo.png", picture)

    build(dashboard, "each")

    packet = ready / "packet"
    assert (packet / "photo.png").read_bytes() == picture
    assert (packet / "certificate.pdf").read_bytes() == CERTIFICATE
    assert (packet / "Mustermann_Anschreiben.pdf").read_bytes() == PDF


def test_an_image_becomes_one_page_of_a_merged_pdf(dashboard: TestClient, ready: Path) -> None:
    upload(dashboard, "photo.png", photo())

    build(dashboard, "letter-and-rest")

    reader = pypdf.PdfReader(io.BytesIO((ready / "packet" / "Mustermann_Anlagen.pdf").read_bytes()))
    assert len(reader.pages) == 5
    page = reader.pages[4]
    assert len(page.images) == 1
    # An A4 page, like the Letter's.
    assert (round(float(page.mediabox.width)), round(float(page.mediabox.height))) == (595, 842)


def test_the_letter_is_printed_as_the_letters_own_pdf_is(
    dashboard: TestClient, ready: Path, printer: RememberedPages
) -> None:
    letter_pdf = dashboard.get(f"{PAGE}/letter.pdf")
    assert letter_pdf.status_code == 200

    build(dashboard)

    assert len(printer.printed) == 2
    assert printer.printed[0] == printer.printed[1]


def test_the_built_files_are_offered_for_download(dashboard: TestClient, ready: Path) -> None:
    page = build(dashboard)

    files = offered(page)
    assert list(files) == [
        "Mustermann_Anschreiben.pdf",
        "Mustermann_Lebenslauf.pdf",
        "Mustermann_Anlagen.pdf",
    ]
    for name, address in files.items():
        downloaded = dashboard.get(address)
        assert downloaded.status_code == 200
        assert downloaded.content == (ready / "packet" / name).read_bytes()
        assert downloaded.headers["content-type"] == "application/pdf"
        assert f'attachment; filename="{name}"' == downloaded.headers["content-disposition"]


def test_one_file_per_document_is_offered_in_the_order_of_the_documents(
    dashboard: TestClient, ready: Path
) -> None:
    page = build(dashboard, "each")

    assert list(offered(page)) == [
        "Mustermann_Anschreiben.pdf",
        "certificate.pdf",
        "Lebenslauf.pdf",
        "zeugnis.pdf",
    ]


def test_only_a_built_file_can_be_downloaded(dashboard: TestClient, ready: Path) -> None:
    build(dashboard)

    assert dashboard.get(f"{PAGE}/packet/Mustermann_Bewerbung.pdf").status_code == 404
    assert dashboard.get(f"{PAGE}/packet/..%2Fapplication.yaml").status_code == 404
    assert dashboard.get(f"{PAGE}/packet/.draft").status_code == 404


def test_building_again_replaces_the_files_built_before(dashboard: TestClient, ready: Path) -> None:
    build(dashboard, "each")

    page = build(dashboard, "everything")

    assert built_files(ready) == ["Mustermann_Bewerbung.pdf"]
    assert list(offered(page)) == ["Mustermann_Bewerbung.pdf"]


def test_building_is_committed_with_the_files_it_wrote_and_removed(
    dashboard: TestClient, ready: Path, committer: RememberedCommits
) -> None:
    build(dashboard, "each")
    build(dashboard, "everything")

    last = committer.commits[-1]
    assert last.message == f"Build Packet of {FOLDER}"
    assert last.paths == [ready / "packet"]


def test_a_failed_commit_says_so_and_the_files_are_built(
    dashboard: TestClient, ready: Path, committer: RememberedCommits
) -> None:
    committer.commit_failure = "the disk is full"

    page = build(dashboard, status=500)

    assert "could not be committed" in page
    assert "the disk is full" in page
    assert built_files(ready) != []


def test_nothing_is_built_while_the_application_is_not_todo(
    dashboard: TestClient, ready: Path
) -> None:
    dashboard.post(f"{PAGE}/status", data={"status": "waiting"})
    assert ">Build<" not in dashboard.get(PAGE).text

    refused = dashboard.post(f"{PAGE}/packet", data={"shape": "everything"})

    assert refused.status_code == 422
    page = refused.text

    assert "Not built." in part(page, "packet")
    assert "Waiting" in part(page, "packet")
    assert built_files(ready) == []


def test_nothing_is_built_without_a_letter(
    dashboard: TestClient, write_application: WriteApplication, data_dir: Path
) -> None:
    write_application(FOLDER, RECORD)
    say_who(data_dir)

    page = build(dashboard, status=422)

    assert "has no Letter" in part(page, "packet")
    assert not (data_dir / "applications" / FOLDER / "packet").exists()


def test_nothing_is_built_while_a_revision_waits(
    dashboard: TestClient, ready: Path, data_dir: Path, model: ScriptedModel
) -> None:
    write_brief(data_dir)
    said = dashboard.post(f"{PAGE}/letter/say", data={"message": "Shorter, please."})
    assert said.status_code == 200, said.text
    model.answers.append(ANSWER.replace("Kind regards", "Best regards"))
    fetch_next_answer(dashboard)

    page = build(dashboard, status=422)

    assert "Revision is waiting" in part(page, "packet")
    assert built_files(ready) == []


def test_nothing_is_built_while_an_answer_is_under_way(
    dashboard: TestClient, ready: Path, data_dir: Path
) -> None:
    write_brief(data_dir)
    said = dashboard.post(f"{PAGE}/letter/say", data={"message": "Shorter, please."})
    assert said.status_code == 200, said.text

    page = build(dashboard, status=422)

    assert "answer about its Letter is under way" in part(page, "packet")
    assert built_files(ready) == []


def test_without_a_printer_the_page_says_no_packet_can_be_built(
    without_a_printer: TestClient, write_application: WriteApplication, data_dir: Path
) -> None:
    write_application(FOLDER, RECORD)
    say_who(data_dir)
    without_a_printer.post(f"{PAGE}/letter/edit", data={"body": BODY})

    page = without_a_printer.get(PAGE).text

    assert "No Packet can be built: this service was started without a printer." in part(
        page, "packet"
    )
    assert ">Build<" not in page
    refused = without_a_printer.post(f"{PAGE}/packet", data={"shape": "everything"})
    assert refused.status_code == 422
    assert "No Packet can be built" in part(refused.text, "packet")
    assert not (data_dir / "applications" / FOLDER / "packet").exists()


def test_nothing_is_built_without_the_applicants_surname(
    dashboard: TestClient, ready: Path, data_dir: Path
) -> None:
    (data_dir / "letter" / "applicant.yaml").unlink()

    page = build(dashboard, status=422)

    assert "letter/applicant.yaml" in part(page, "packet")
    assert built_files(ready) == []


def test_a_surname_that_cannot_name_a_file_builds_nothing(
    dashboard: TestClient, ready: Path, data_dir: Path
) -> None:
    say_who(data_dir, "surname: ../Mustermann\n")

    page = build(dashboard, status=422)

    assert "surname" in part(page, "packet")
    assert built_files(ready) == []


def test_the_letter_and_the_resume_need_a_document_recognised_as_the_resume(
    dashboard: TestClient, ready: Path
) -> None:
    removed = dashboard.post(f"{PAGE}/documents/remove", data={"name": "Lebenslauf.pdf"})
    assert removed.status_code == 200, removed.text

    page = build(dashboard, status=422)

    assert "resume" in part(page, "packet")
    assert built_files(ready) == []
    # The other shapes do without one.
    build(dashboard, "letter-and-rest")
    assert texts((ready / "packet" / "Mustermann_Anlagen.pdf").read_bytes()) == [
        "Certificate in welding",
        "Zeugnis from a shipyard",
    ]


def test_with_nothing_but_the_letter_there_is_no_file_for_the_rest(
    dashboard: TestClient, ready: Path
) -> None:
    for name in ("certificate.pdf", "zeugnis.pdf"):
        dashboard.post(f"{PAGE}/documents/remove", data={"name": name})

    build(dashboard)

    assert built_files(ready) == ["Mustermann_Anschreiben.pdf", "Mustermann_Lebenslauf.pdf"]


def test_a_document_that_cannot_be_read_builds_nothing_and_is_named(
    dashboard: TestClient, ready: Path
) -> None:
    upload(dashboard, "scan.pdf", b"%PDF-1.7 not really a PDF")

    page = build(dashboard, "everything", status=422)

    assert "scan.pdf" in part(page, "packet")
    assert built_files(ready) == []


def test_the_real_printers_letter_is_merged_first(
    data_dir: Path,
    clock: SettableClock,
    network: Pages,
    committer: RememberedCommits,
    model: ScriptedModel,
    write_application: WriteApplication,
) -> None:
    if reason := no_chromium():
        pytest.skip(reason)
    real = TestClient(
        create_app(
            data_dir=data_dir,
            clock=clock,
            network=network,
            committer=committer,
            model=model,
            printer=ChromiumPrinter(),
            sleep=lambda seconds: None,
        )
    )
    ready(real, write_application, data_dir)

    build(real, "everything")

    pages = texts(
        (data_dir / "applications" / FOLDER / "packet" / "Mustermann_Bewerbung.pdf").read_bytes()
    )
    assert len(pages) == 5
    assert "towing tanks are where I learned" in pages[0]
    assert pages[1:] == [
        "Resume of Erika Mustermann",
        "Resume, second page",
        "Certificate in welding",
        "Zeugnis from a shipyard",
    ]
