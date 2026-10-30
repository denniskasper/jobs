"""Packets: an Application's Letter and Documents built into the files that are sent in one go.

A Packet is built in one of four shapes into `packet/` in the Application's folder, apart from its
Documents, and building again replaces what was there. The Letter is printed by the Printer that
builds its own PDF; merged files follow the Application's order with the Letter first and the
resume next, and an image becomes an A4 page of its own. The files are named in German after the
surname of the applicant the Letter is from, which `letter/applicant.yaml` in the data directory
says, written by hand like the template the Letter is set in (decision 60).
"""

import io
import re
import shutil
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path, PurePosixPath

import pypdf
from PIL import Image, ImageOps

from jobs import documents, records
from jobs.applications import Status, directory_of, documents_of, read_application
from jobs.commits import CommitFailed, Committer
from jobs.letters import Letters
from jobs.printing import FOLDER_NAME, NotPrinted, Printer, letter_page
from jobs.records import Malformed, NotCommitted, Refused

PACKET_NAME = "packet"

# Who the Letters are from, as far as a Packet needs to know: beside the Letter's own template.
APPLICANT_NAME = "applicant.yaml"

# What a service started without a printer says where a Packet would be built.
NO_PRINTER = "No Packet can be built: this service was started without a printer."

# A4 in points, the size the Letter is printed in.
_A4 = (595.28, 841.89)

# The Documents taken for the resume: by a name that says so, in German or English.
_RESUME = re.compile(r"lebenslauf|(?<![a-z])(cv|r[eé]sum[eé]|curriculum[ _-]?vitae)(?![a-z])")

# Building reads the folder, removes what was built and writes it anew, and requests are served
# from threads.
_one_at_a_time = threading.Lock()


class Shape(StrEnum):
    """The files a Packet is built into. The value is what the form sends."""

    EVERYTHING = "everything"
    LETTER_AND_REST = "letter-and-rest"
    LETTER_RESUME_AND_REST = "letter-resume-and-rest"
    EACH = "each"

    @property
    def label(self) -> str:
        return _SHAPE_LABELS[self]


_SHAPE_LABELS = {
    Shape.EVERYTHING: "Everything in one PDF",
    Shape.LETTER_AND_REST: "The Letter and the rest",
    Shape.LETTER_RESUME_AND_REST: "The Letter, the resume and the rest",
    Shape.EACH: "One file per Document",
}

# As most portals want it.
DEFAULT = Shape.LETTER_RESUME_AND_REST

# What each merged file is called after the surname, in the order they are listed.
_EVERYTHING, _LETTER, _RESUME_FILE, _REST = "Bewerbung", "Anschreiben", "Lebenslauf", "Anlagen"
_MERGED = (_EVERYTHING, _LETTER, _RESUME_FILE, _REST)


@dataclass(frozen=True)
class _Document:
    name: str
    content: bytes


def build(
    data_dir: Path,
    committer: Committer,
    letters: Letters,
    printer: Printer,
    folder: str,
    *,
    shape: str,
    on: date,
) -> None:
    """Build the Packet of the Application in `folder` in `shape`, with its Letter printed as on
    the day `on`, in place of the one built before, and commit it. Nothing is written unless all
    of it could be built. Raises NotFound, Refused and NotCommitted."""
    try:
        chosen = Shape(shape)
    except ValueError:
        raise Refused(f"The shape has to be one of: {', '.join(Shape)}.") from None
    with _one_at_a_time:
        files = _files(data_dir, letters, printer, folder, chosen, on)
        packet = directory_of(data_dir, folder) / PACKET_NAME
        try:
            shutil.rmtree(packet, ignore_errors=True)
            packet.mkdir()
            for name, content in files.items():
                (packet / name).write_bytes(content)
        except OSError as error:
            raise Refused(f"The Packet could not be written: {error.strerror}.") from error
        try:
            committer.commit(f"Build Packet of {folder}", [packet])
        except CommitFailed as failed:
            raise NotCommitted(
                f"The Packet of {folder} was built but could not be committed: {failed}. The "
                "next Packet built for it will commit this one too."
            ) from failed


def built(data_dir: Path, folder: str) -> list[str]:
    """The files of the Packet built for the Application in `folder`, the Letter's first and the
    rest in the order of its Documents, or none when none was built. Raises NotFound."""
    packet = directory_of(data_dir, folder) / PACKET_NAME
    if not packet.is_dir():
        return []
    try:
        order = [each.name for each in documents_of(data_dir, folder)]
    except Malformed:
        order = []
    names = [each.name for each in packet.iterdir() if downloadable(each)]

    def place(name: str) -> tuple[int, int, str]:
        merged = [at for at, part in enumerate(_MERGED) if name.endswith(f"_{part}.pdf")]
        if name in order:
            return (1, order.index(name), name)
        if merged:
            return (0, merged[0], name)
        return (2, 0, name)

    return sorted(names, key=place)


def file_of(data_dir: Path, folder: str, name: str) -> Path:
    """The built file called `name` of the Application in `folder`. Raises NotFound."""
    path = directory_of(data_dir, folder) / PACKET_NAME / name
    if documents.name_problem(name) or not downloadable(path):
        raise records.NotFound(f"The Packet of {folder} has no file called {name}.")
    return path


def downloadable(path: Path) -> bool:
    """Whether `path` is a file a Packet was built into, rather than a link or a leftover."""
    return path.is_file() and not path.is_symlink() and not path.name.startswith(".")


def _files(
    data_dir: Path, letters: Letters, printer: Printer, folder: str, shape: Shape, on: date
) -> dict[str, bytes]:
    """The files of the Packet, by name. Raises NotFound and Refused."""
    application = read_application(data_dir, folder)
    if application.status is not Status.TODO:
        raise Refused(
            f"This Application is {application.status.label}, so no Packet can be built for it. "
            f"Set it back to {Status.TODO.label} to build one."
        )
    standing = letters.look(folder)
    if standing.problem:
        raise Refused(f"The Letter could not be read: {standing.problem}.")
    if not standing.letter:
        raise Refused("This Application has no Letter yet, and a Packet begins with it.")
    if standing.waiting is not None:
        raise Refused("A Revision is waiting: accept or reject it first.")
    if standing.under_way:
        raise Refused(
            "An answer about its Letter is under way, so no Packet can be built yet: try again "
            "once the answer is in."
        )
    surname = _surname(data_dir)
    held = _held(data_dir, folder)
    resume = next((each for each in held if _RESUME.search(_stem(each.name))), None)
    rest = [each for each in held if each is not resume]
    if shape is Shape.LETTER_RESUME_AND_REST and resume is None:
        raise Refused(
            "No Document is taken for the resume: one is when its name says Lebenslauf, CV or "
            "resume. Rename it, or choose another shape."
        )
    try:
        page = letter_page(data_dir, application, standing.letter, on)
        letter = _Document(f"{surname}_{_LETTER}.pdf", printer.print(page))
    except Malformed as malformed:
        raise Refused(f"The Letter could not be set: {malformed}.") from None
    except NotPrinted as not_printed:
        raise Refused(str(not_printed)) from None
    after_letter = ([resume] if resume else []) + rest
    if shape is Shape.EVERYTHING:
        parts = {f"{surname}_{_EVERYTHING}.pdf": [letter, *after_letter]}
    elif shape is Shape.LETTER_AND_REST:
        parts = {letter.name: [letter], f"{surname}_{_REST}.pdf": after_letter}
    elif shape is Shape.LETTER_RESUME_AND_REST:
        assert resume is not None
        parts = {
            letter.name: [letter],
            f"{surname}_{_RESUME_FILE}.pdf": [resume],
            f"{surname}_{_REST}.pdf": rest,
        }
    else:
        if any(each.name == letter.name for each in held):
            raise Refused(f"A Document is called {letter.name}, which is the Letter's name.")
        # Each as it is, whatever it is: nothing is merged.
        for each in held:
            _as_pdf(each)
        return {letter.name: letter.content, **{each.name: each.content for each in held}}
    return {name: _merged(merged) for name, merged in parts.items() if merged}


def _surname(data_dir: Path) -> str:
    """The surname of the applicant the Letters are from. Raises Refused."""
    where = f"{FOLDER_NAME}/{APPLICANT_NAME}"
    path = data_dir / FOLDER_NAME / APPLICANT_NAME
    if not path.is_file():
        raise Refused(
            f"No Packet can be built until {where} in the data directory names the surname its "
            "files are named after, as surname: and the name."
        )
    try:
        surname = records.text_of(records.load(path), "surname")
    except Malformed as malformed:
        raise Refused(f"{where} could not be read: {malformed}.") from None
    if documents.name_problem(f"{surname}_{_EVERYTHING}.pdf"):
        raise Refused(f"The surname in {where} cannot name a file: {surname!r}.")
    return surname


def _held(data_dir: Path, folder: str) -> list[_Document]:
    """The Documents of the Application in `folder`, in order, with their bytes. Raises
    Refused."""
    directory = directory_of(data_dir, folder)
    try:
        held = documents_of(data_dir, folder)
    except Malformed as malformed:
        raise Refused(f"Its Documents could not be read: {malformed}.") from None
    found = []
    for each in held:
        try:
            found.append(_Document(each.name, (directory / each.name).read_bytes()))
        except OSError:
            raise Refused(f"{each.name} is missing from the Application's folder.") from None
    return found


def _stem(name: str) -> str:
    return PurePosixPath(name).stem.lower()


def _merged(parts: Sequence[_Document]) -> bytes:
    """One PDF of every page of `parts`, in order. Raises Refused."""
    writer = pypdf.PdfWriter()
    for part in parts:
        try:
            writer.append(_as_pdf(part))
        except Refused:
            raise
        except Exception as error:
            # Deliberately everything, as reading it: a PDF comes from anywhere.
            raise Refused(f"{part.name} could not be merged: {error}.") from error
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _as_pdf(document: _Document) -> pypdf.PdfReader:
    """The Document as a PDF to take the pages of: a PDF as it is, an image as one A4 page.
    Raises Refused."""
    try:
        if document.content.startswith(b"%PDF-"):
            reader = pypdf.PdfReader(io.BytesIO(document.content))
            if reader.is_encrypted:
                raise Refused(
                    f"{document.name} is protected by a password, so it cannot be merged."
                )
            if not reader.pages:
                raise Refused(f"{document.name} has no pages.")
            return reader
        return pypdf.PdfReader(io.BytesIO(_image_page(document.content)))
    except Refused:
        raise
    except Exception as error:
        # Deliberately everything: a file a human uploaded can be broken in any way, and that is
        # a reason to name it, not a failure of the service.
        raise Refused(f"{document.name} could not be read: {error}.") from error


def _image_page(content: bytes) -> bytes:
    """A PDF of one A4 page with the image on it, as large as it fits and centred."""
    with Image.open(io.BytesIO(content)) as opened:
        # A photo taken upright on a phone says so only in its metadata.
        turned = ImageOps.exif_transpose(opened).convert("RGBA")
    picture = Image.new("RGB", turned.size, "white")
    picture.paste(turned, mask=turned.getchannel("A"))
    width, height = picture.size
    resolution = max(width * 72 / _A4[0], height * 72 / _A4[1])
    image_pdf = io.BytesIO()
    picture.save(image_pdf, format="PDF", resolution=resolution, quality=95)
    image = pypdf.PdfReader(image_pdf).pages[0]
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=_A4[0], height=_A4[1])
    left = (_A4[0] - float(image.mediabox.width)) / 2
    bottom = (_A4[1] - float(image.mediabox.height)) / 2
    page.merge_transformed_page(image, pypdf.Transformation().translate(left, bottom))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
