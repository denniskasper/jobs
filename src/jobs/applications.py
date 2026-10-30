"""Applications, kept in the files that are their only store (ADR 0002).

An Application is a folder under `applications/`, named by company and dated role. The folder is
the record; `application.yaml` inside it is its metadata. Files are edited by hand, so a record
that cannot be read is reported as a Problem beside the ones that can, never raised.
"""

import re
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jobs import documents, records
from jobs.commits import CommitFailed, Committer
from jobs.postings import KeptPosting, canonical_url, job_link, read_posting
from jobs.records import AlreadyExists, Malformed, NotCommitted, NotFound, Refused

RECORD_NAME = "application.yaml"

# The type, under a name the field `Application.date` does not shadow.
_Day = date

# The fields the user writes text into, on the Application's page or by hand.
_WRITTEN_DOWN = ("notes", "address")

# The Posting an Application holds: as it was kept when ticked at the Pursue Gate, or as it was
# brought in by hand.
POSTING_NAME = "posting.yaml"


class Status(StrEnum):
    """Where an Application stands. The value is what is written in the record."""

    TODO = "todo"
    WAITING = "waiting"
    INTERVIEW = "interview"
    CLOSED = "closed"

    @property
    def label(self) -> str:
        return _STATUS_LABELS[self]


_STATUS_LABELS = {
    Status.TODO: "To do",
    Status.WAITING: "Waiting",
    Status.INTERVIEW: "Interview",
    Status.CLOSED: "Closed",
}


class Channel(StrEnum):
    """How a Packet reached an employer. The value is what is written in the record."""

    PORTAL = "portal"
    EMAIL = "email"

    @property
    def label(self) -> str:
        return _CHANNEL_LABELS[self]


_CHANNEL_LABELS = {Channel.PORTAL: "Portal", Channel.EMAIL: "Email"}

# What an Email can go to: something before an at sign and a domain after it.
_EMAIL_ADDRESS = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


@dataclass(frozen=True)
class Submission:
    """One act of sending a Packet, as the record keeps it."""

    at: datetime
    channel: Channel
    where: str
    manifest: dict[str, str]
    """Every filename in the Packet, in the order it was listed, and the SHA-256 of its bytes."""


@dataclass(frozen=True)
class Application:
    folder: str
    company: str
    role: str
    date: date
    status: Status
    notes: str = ""
    """What the user wrote down about this pursuit."""
    address: str = ""
    """The employer's address, line for line as it was typed."""
    sent: bool = False
    """Whether it went out: a Submission was recorded, or its Status became `waiting` or
    `interview` at some point."""
    sent_on: _Day | None = None
    """Its Sent date, None when it was not sent or nobody recorded the day."""


class AlreadyBroughtIn(Exception):
    """A link that is on record already, as the link of the Posting `application` holds. Not a
    Refused: nothing was wrong with what was entered, it leads to what exists."""

    def __init__(self, application: Application) -> None:
        super().__init__(f"{application.folder} holds this Posting already.")
        self.application = application


@dataclass(frozen=True)
class Problem:
    """A record on disk that could not be read as an Application, and why."""

    folder: str
    reason: str


@dataclass(frozen=True)
class Inventory:
    """Everything found under `applications/`: what could be read, and what could not."""

    applications: list[Application]
    problems: list[Problem]


def list_applications(data_dir: Path) -> Inventory:
    root = data_dir / "applications"
    inventory = Inventory(applications=[], problems=[])
    for directory in sorted(path for path in root.glob("*/*") if path.is_dir()):
        folder = directory.relative_to(root).as_posix()
        try:
            inventory.applications.append(_read(directory / RECORD_NAME, folder))
        except Malformed as malformed:
            inventory.problems.append(Problem(folder=folder, reason=str(malformed)))
    for stray in sorted(root.rglob(RECORD_NAME)):
        if len(stray.relative_to(root).parts) != 3:
            inventory.problems.append(
                Problem(
                    folder=stray.parent.relative_to(root).as_posix(),
                    reason=f"{RECORD_NAME} is not inside a company folder and a dated-role folder",
                )
            )
    inventory.applications.sort(key=lambda application: application.date, reverse=True)
    return inventory


def read_application(data_dir: Path, folder: str) -> Application:
    """The Application in `folder`. Raises NotFound, and Refused when its record cannot be
    read."""
    directory = directory_of(data_dir, folder)
    try:
        return _read(directory / RECORD_NAME, folder)
    except Malformed as malformed:
        raise Refused(f"{folder} cannot be opened, fix it by hand first: {malformed}.") from None


def posting_of(data_dir: Path, folder: str) -> KeptPosting | None:
    """The Posting the Application in `folder` holds, read from the copy in its folder, or None
    when it holds none. Raises NotFound and Malformed."""
    path = directory_of(data_dir, folder) / POSTING_NAME
    return read_posting(path) if path.is_file() else None


def holding(data_dir: Path, link: str) -> Application | None:
    """The Application whose Posting has `link`, however that Posting came to be there, or None.
    Links are the same when their canonical URLs are. Nothing is fetched: only the copies in the
    Applications' folders are read, and one that cannot be read holds nothing."""
    wanted = canonical_url(link.strip())
    for application in list_applications(data_dir).applications:
        try:
            posting = posting_of(data_dir, application.folder)
        except Malformed:
            continue
        if posting is not None and posting.url and canonical_url(posting.url) == wanted:
            return application
    return None


def create_application(
    data_dir: Path,
    committer: Committer,
    *,
    company: str,
    role: str,
    on: date,
    link: str = "",
    text: str = "",
    deadline: str = "",
) -> Application:
    """Create an Application by hand and commit it. With a link or a text it holds a Posting
    brought in by hand, written beside it in the same commit; with neither it holds none. A link
    on record already creates nothing. Raises AlreadyBroughtIn, Refused and NotCommitted."""
    if link.strip() and (holder := holding(data_dir, link)) is not None:
        raise AlreadyBroughtIn(holder)
    posting = _brought_in(company, role, on, link, text, deadline)
    scaffolded = scaffold(data_dir, company=company, role=role, on=on, posting=posting)
    folder = scaffolded.application.folder
    _commit(committer, f"Create Application {folder}", folder, scaffolded.paths)
    return scaffolded.application


def _brought_in(
    company: str, role: str, on: date, link: str, text: str, deadline: str
) -> str | None:
    """The file of a Posting brought in by hand, in the form a kept Posting has, or None when
    neither a link nor a text was brought. It belongs to no Source, so it has no identity there.
    Raises Refused."""
    link, text = link.strip(), text.replace("\r\n", "\n").replace("\r", "\n").strip()
    try:
        due = date.fromisoformat(deadline.strip()) if deadline.strip() else None
    except ValueError:
        raise Refused("The deadline has to be a date written as YYYY-MM-DD.") from None
    if link and (
        urlsplit(link).scheme.lower() not in ("http", "https") or not urlsplit(link).netloc
    ):
        # It is shown as a link to follow, so it has to be an address a browser only opens.
        raise Refused("The link has to be an address beginning with http:// or https://.")
    if not link and not text:
        return None
    record = {
        "company": company.strip(),
        "role": role.strip(),
        "url": job_link(link) or None,
        "deadline": due,
        "brought_in_by_hand": on,
        "description": records.Lines(text) if text else None,
    }
    return records.dump({name: value for name, value in record.items() if value is not None})


def lacks_text(posting: KeptPosting | None) -> bool:
    """Whether `posting` was brought in by hand without its text, which it may then be given
    once."""
    return (
        posting is not None and posting.brought_in_by_hand is not None and not posting.description
    )


def give_text(data_dir: Path, committer: Committer, folder: str, text: str) -> None:
    """Write `text` into the Posting brought in by hand without one that the Application in
    `folder` holds, and commit that file alone. From then on it is fixed like the rest of the
    Posting. Raises NotFound, Refused and NotCommitted."""
    path = directory_of(data_dir, folder) / POSTING_NAME
    text = _as_typed(text)
    try:
        posting = read_posting(path) if path.is_file() else None
        record = records.load(path) if posting is not None else {}
    except Malformed as malformed:
        raise Refused(f"{folder} was left as it is, fix it by hand first: {malformed}.") from None
    if not lacks_text(posting):
        raise Refused(
            "Only a Posting brought in by hand without its text can be given one, and only once."
        )
    if not text:
        raise Refused("The text is empty: paste the Posting's text first.")
    record["description"] = records.Lines(text)
    records.write(path, records.dump(record))
    _commit(committer, f"Give the Posting of {folder} its text", folder, [path])


@dataclass(frozen=True)
class Scaffolded:
    """A new Application on disk that no commit holds yet."""

    application: Application
    directory: Path
    paths: list[Path]
    """Everything written for it."""

    def remove(self) -> None:
        """Take it off the disk again, for when what it was created for did not come about."""
        shutil.rmtree(self.directory, ignore_errors=True)
        _remove_if_empty(self.directory.parent)


def scaffold(
    data_dir: Path, *, company: str, role: str, on: date, posting: str | None = None
) -> Scaffolded:
    """Scaffold the folder for a new Application, write its record, starting at `todo`, and link
    in every shared Document, which it then holds. `posting` is the text of the Posting it holds,
    stored beside the record. Raises Refused. Nothing is committed here."""
    application = Application(
        folder=f"{_folder_part(company, 'company')}/{on.isoformat()}-{_folder_part(role, 'role')}",
        company=company.strip(),
        role=role.strip(),
        date=on,
        status=Status.TODO,
    )
    directory = data_dir / "applications" / application.folder
    try:
        shared = documents.list_shared(data_dir)
    except Malformed as malformed:
        raise Refused(
            f"{application.folder} was not created, because the shared Documents cannot be "
            f"linked into it: {malformed}. Fix that by hand first."
        ) from None
    record = {
        "company": application.company,
        "role": application.role,
        "date": application.date,
        "status": application.status.value,
        "history": [_history_entry(application.status, on)],
        # Every shared Document, in the order the Documents tab lists them.
        "documents": [{SHARED: document.name} for document in shared],
    }
    try:
        directory.parent.mkdir(parents=True, exist_ok=True)
        directory.mkdir()
    except FileExistsError:
        raise AlreadyExists(
            f"{application.folder} already exists. Nothing was written; change the role or the "
            "date, or remove the folder by hand."
        ) from None
    except OSError as error:
        _remove_if_empty(directory.parent)
        raise Refused(f"{application.folder} could not be created: {error.strerror}.") from error
    paths = [directory / RECORD_NAME]
    try:
        records.write(directory / RECORD_NAME, records.dump(record))
        if posting is not None:
            records.write(directory / POSTING_NAME, posting)
            paths.append(directory / POSTING_NAME)
        paths += documents.link(data_dir, shared, into=directory)
    except OSError as error:
        # The folder is ours and holds nothing yet, so a failed create leaves no trace to trip on.
        Scaffolded(application, directory, paths).remove()
        raise Refused(f"{application.folder} could not be created: {error.strerror}.") from error
    return Scaffolded(application, directory, paths)


def _commit(committer: Committer, message: str, folder: str, paths: Sequence[Path]) -> None:
    try:
        committer.commit(message, paths)
    except CommitFailed as failed:
        raise NotCommitted(
            f"{folder} was written but could not be committed: {failed}. The next change to it "
            "will commit this one too."
        ) from failed


def _posting_in(directory: Path) -> list[Path]:
    posting = directory / POSTING_NAME
    return [posting] if posting.is_file() else []


def _remove_if_empty(directory: Path) -> None:
    try:
        directory.rmdir()
    except OSError:
        pass


def change_status(data_dir: Path, committer: Committer, folder: str, *, to: str, on: date) -> None:
    """Set the Status of the Application in `folder`, adding the change to its dated history, and
    commit it."""
    path = directory_of(data_dir, folder) / RECORD_NAME
    try:
        status = Status(to)
    except ValueError:
        raise Refused(f"The Status has to be one of: {', '.join(Status)}.") from None
    try:
        record = records.load(path)
        current = _parse(record, folder).status
        history = _history(record, current)
    except Malformed as malformed:
        raise Refused(f"{folder} was left as it is, fix it by hand first: {malformed}.") from None
    if status == current:
        return
    record["status"] = status.value
    record["history"] = [*history, _history_entry(status, on)]
    records.write(path, _dump(record))
    _commit(
        committer,
        f"Change Status of {folder} from {current.value} to {status.value}",
        folder,
        # With the record, so that what a failed commit left behind is committed too.
        [path, *_posting_in(path.parent), *documents.links_in(data_dir, path.parent)],
    )


def record_submission(
    data_dir: Path,
    committer: Committer,
    folder: str,
    *,
    channel: str,
    where: str,
    manifest: dict[str, str],
    frozen: Sequence[Path],
    at: datetime,
) -> Submission:
    """Record that the Application in `folder` sent a Packet with this `manifest` by `channel` to
    `where`, at `at`, and move its Status from `todo` to `waiting`, in one commit with the
    `frozen` files that hold what was sent. The Submissions recorded before stay as they are.
    Raises NotFound, Refused and NotCommitted."""
    path = directory_of(data_dir, folder) / RECORD_NAME
    submission = _submission_entered(channel, where, manifest, at)
    try:
        record = records.load(path)
        current = _parse(record, folder).status
        history = _history(record, current)
        # Read only to refuse what cannot be added to: the earlier ones are written back as
        # they are.
        _submissions(record)
    except Malformed as malformed:
        raise Refused(f"{folder} was left as it is, fix it by hand first: {malformed}.") from None
    refuse_unless_todo(current)
    record["status"] = Status.WAITING.value
    record["history"] = [*history, _history_entry(Status.WAITING, at.date())]
    record["submissions"] = [*(record.get("submissions") or []), _entry(submission)]
    records.write(path, _dump(record))
    _commit(
        committer,
        f"Record Submission of {folder} by {submission.channel.value}, from todo to waiting",
        folder,
        [path, *frozen, *_posting_in(path.parent), *documents.links_in(data_dir, path.parent)],
    )
    return submission


def refuse_unless_todo(status: Status) -> None:
    """Refuse to mark sent an Application that is not `todo`: one is marked sent once per time
    it is set to `todo`."""
    if status is not Status.TODO:
        raise Refused(
            f"This Application is {status.label}, so it cannot be marked sent. Set it back to "
            f"{Status.TODO.label} and build a Packet to send it again."
        )


def _submission_entered(
    channel: str, where: str, manifest: dict[str, str], at: datetime
) -> Submission:
    try:
        chosen = Channel(channel)
    except ValueError:
        raise Refused(f"The Channel has to be one of: {', '.join(Channel)}.") from None
    where = where.strip()
    if not where:
        raise Refused("Say where it went: the portal's address, or the email address.")
    if chosen is Channel.EMAIL and not _EMAIL_ADDRESS.fullmatch(where):
        raise Refused(f"An Email goes to an email address, and {where} is none.")
    return Submission(at=at, channel=chosen, where=where, manifest=dict(manifest))


def _entry(submission: Submission) -> dict[str, Any]:
    return {
        "at": submission.at,
        "channel": submission.channel.value,
        "where": submission.where,
        "manifest": dict(submission.manifest),
    }


def submissions_of(data_dir: Path, folder: str) -> list[Submission]:
    """The Submissions of the Application in `folder`, in the order they were recorded. Raises
    NotFound and Malformed."""
    return _submissions(records.load(directory_of(data_dir, folder) / RECORD_NAME))


def _submissions(record: dict[Any, Any]) -> list[Submission]:
    """The Submissions the record holds. Raises Malformed."""
    entries = record.get("submissions")
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise Malformed("submissions is not a list")
    found = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise Malformed("each of submissions is a mapping of at, channel, where and manifest")
        at = entry.get("at")
        if not isinstance(at, datetime):
            raise Malformed("at of a Submission is missing or is not a time")
        try:
            channel = Channel(str(entry.get("channel")))
        except ValueError:
            raise Malformed(f"channel of a Submission is one of: {', '.join(Channel)}") from None
        manifest = entry.get("manifest")
        if not isinstance(manifest, dict) or not all(
            isinstance(name, str)
            and not documents.name_problem(name)
            and isinstance(hashed, str)
            and documents.is_hash(hashed)
            for name, hashed in manifest.items()
        ):
            raise Malformed("manifest of a Submission maps filenames to SHA-256 hashes")
        where = records.text_of(entry, "where")
        found.append(Submission(at=at, channel=channel, where=where, manifest=manifest))
    return found


def delete_application(
    data_dir: Path,
    committer: Committer,
    folder: str,
    *,
    dismissing: Callable[[KeptPosting], Sequence[Path]] = lambda posting: [],
) -> Application:
    """Delete the Application in `folder`: remove its folder, and its company's folder when that
    is left empty, and commit the removal under a message naming the role and the company. The
    shared Documents linked into it go as links only. One that was ever sent is refused: it can
    only be closed. `dismissing` is handed the Posting it holds before anything is removed, and
    the files it gives back are committed with the removal. Gives back what was deleted. Raises
    NotFound, Refused and NotCommitted."""
    directory = directory_of(data_dir, folder)
    # Its record names what is deleted, so one that cannot be read is left as it is.
    application = read_application(data_dir, folder)
    refuse_if_sent(application)
    try:
        held = posting_of(data_dir, folder)
    except Malformed:
        # Nothing can be told about where it came from, and that does not keep it.
        held = None
    dismissed = list(dismissing(held)) if held is not None else []
    try:
        shutil.rmtree(directory)
    except OSError as error:
        raise Refused(
            f"{folder} could not be deleted completely: {error.strerror}. Look at what is left of "
            "it by hand."
        ) from error
    _remove_if_empty(directory.parent)
    try:
        committer.commit(
            f"Delete Application {folder}, {application.role} at {application.company}",
            [directory, *dismissed],
        )
    except CommitFailed as failed:
        raise NotCommitted(
            f"{folder} was deleted but the deletion could not be committed: {failed}. Commit "
            "its removal by hand in the data repository."
        ) from failed
    return application


# How the record names a Document it holds: shared, by the name it has on the Documents tab, or
# its own, by the name of the file in its folder.
SHARED = "shared"
OWN = "own"


@dataclass(frozen=True)
class Held:
    """One Document an Application holds, under the filename it has in the Application's
    folder."""

    name: str
    shared: bool
    size: int | None
    """In bytes, None when its folder holds no such file."""


def documents_of(data_dir: Path, folder: str) -> list[Held]:
    """The Documents the Application in `folder` holds, in order. Raises NotFound and
    Malformed."""
    directory = directory_of(data_dir, folder)
    held = []
    for kind, name in _held(data_dir, records.load(directory / RECORD_NAME), directory):
        path = directory / name
        size = path.stat().st_size if path.is_file() else None
        held.append(Held(name=name, shared=kind == SHARED, size=size))
    return held


def hold_shared(data_dir: Path, committer: Committer, folder: str, name: str) -> None:
    """Add the shared Document called `name` to the end of what the Application in `folder`
    holds, linked to the bytes it names now, and commit it. Raises NotFound, Refused and
    NotCommitted."""

    def change(held: list[tuple[str, str]], directory: Path) -> list[Path]:
        try:
            shared = {document.name: document for document in documents.list_shared(data_dir)}
        except Malformed as malformed:
            raise Refused(f"The shared Documents cannot be read: {malformed}.") from None
        if name not in shared:
            raise Refused(f"There is no shared Document called {name}.")
        _refuse_taken(held, directory, name)
        held.append((SHARED, name))
        return documents.link(data_dir, [shared[name]], into=directory)

    _change_documents(data_dir, committer, folder, f"Add shared Document {name} to", change)


def hold_own(data_dir: Path, committer: Committer, folder: str, name: str, content: bytes) -> None:
    """Store `content` in the folder of the Application in `folder` as a Document of its own
    called `name`, last in its order, and commit it. Raises NotFound, Refused and
    NotCommitted."""
    name = name.strip()
    documents.refuse_unless_document(name, content)

    def change(held: list[tuple[str, str]], directory: Path) -> list[Path]:
        _refuse_taken(held, directory, name)
        (directory / name).write_bytes(content)
        held.append((OWN, name))
        return [directory / name]

    _change_documents(data_dir, committer, folder, f"Add Document {name} to", change)


def add_shared(data_dir: Path, committer: Committer, *, name: str, content: bytes) -> None:
    """Make `content` the shared Document called `name`, and move every Application that was
    never sent and holds it to the new bytes, in the same commit. One that was sent keeps the
    bytes it held. Raises Refused and NotCommitted."""
    documents.add_shared(
        data_dir,
        committer,
        name=name,
        content=content,
        follow=lambda document: _follow(data_dir, document),
    )


def _follow(data_dir: Path, document: documents.SharedDocument) -> list[Path]:
    """Link `document` into every Application that was never sent and holds it, in place of
    the bytes it was linked to, and return the links that moved. One whose record cannot be read
    is left as it is: whether it was sent cannot be told. So is a file a human put in place of
    the link."""
    moved = []
    for application in list_applications(data_dir).applications:
        if application.sent:
            continue
        directory = data_dir / "applications" / application.folder
        try:
            held = _held(data_dir, records.load(directory / RECORD_NAME), directory)
        except Malformed:
            continue
        link = directory / document.name
        if (SHARED, document.name) not in held or documents.links_to(data_dir, document, link):
            continue
        if link.exists() and not link.is_symlink():
            continue
        link.unlink(missing_ok=True)
        moved += documents.link(data_dir, [document], into=directory)
    return moved


def let_go(data_dir: Path, committer: Committer, folder: str, name: str) -> None:
    """Remove the Document called `name` from the Application in `folder`, and commit it. A
    shared one goes as a link only: the Documents tab and every other Application keep it. Raises
    NotFound, Refused and NotCommitted."""

    def change(held: list[tuple[str, str]], directory: Path) -> list[Path]:
        held.pop(_position(held, name))
        (directory / name).unlink(missing_ok=True)
        return [directory / name]

    _change_documents(data_dir, committer, folder, f"Remove Document {name} from", change)


def move_document(
    data_dir: Path, committer: Committer, folder: str, name: str, *, up: bool
) -> None:
    """Move the Document called `name` one place up or down in the order of the Application in
    `folder`, and commit it. The first one up and the last one down stay where they are. Raises
    NotFound, Refused and NotCommitted."""

    def change(held: list[tuple[str, str]], directory: Path) -> list[Path]:
        at = _position(held, name)
        to = at - 1 if up else at + 1
        if 0 <= to < len(held):
            held[at], held[to] = held[to], held[at]
        return []

    direction = "up" if up else "down"
    _change_documents(data_dir, committer, folder, f"Move Document {name} {direction} in", change)


def _change_documents(
    data_dir: Path,
    committer: Committer,
    folder: str,
    doing: str,
    change: Callable[[list[tuple[str, str]], Path], list[Path]],
) -> None:
    """Hand what the Application in `folder` holds to `change`, which changes it in place and
    gives back the files it wrote or removed, and commit those with the record. Nothing is
    written when the order comes back as it was and no file was touched."""
    directory = directory_of(data_dir, folder)
    path = directory / RECORD_NAME
    try:
        record = records.load(path)
        refuse_changes_if_sent(_parse(record, folder))
        before = _held(data_dir, record, directory)
    except Malformed as malformed:
        raise Refused(f"{folder} was left as it is, fix it by hand first: {malformed}.") from None
    held = list(before)
    try:
        touched = change(held, directory)
    except OSError as error:
        raise Refused(f"{folder} could not be changed: {error.strerror}.") from error
    if held == before and not touched:
        return
    record["documents"] = [{kind: name} for kind, name in held]
    records.write(path, _dump(record))
    _commit(committer, f"{doing} {folder}", folder, [path, *touched])


def refuse_changes_if_sent(application: Application) -> None:
    """Refuse a change to the Documents of an Application that was ever sent: what it sent is
    what it holds."""
    if application.sent:
        raise Refused(
            f"{application.role} at {application.company} was sent, so its Documents stay as "
            "they are."
        )


def _held(data_dir: Path, record: dict[Any, Any], directory: Path) -> list[tuple[str, str]]:
    """What the record says the Application holds, as kind and name, in order. A record that
    says nothing holds the shared Documents linked into its folder, in the Documents tab's
    order, which is what an Application held before its record named them. Raises Malformed."""
    if "documents" not in record:
        return [(SHARED, link.name) for link in documents.links_in(data_dir, directory)]
    entries = record["documents"]
    if not isinstance(entries, list):
        raise Malformed("documents is not a list")
    held: list[tuple[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict) or len(entry) != 1 or next(iter(entry)) not in (SHARED, OWN):
            raise Malformed(f"each of documents is either {SHARED}: or {OWN}: and a filename")
        ((kind, name),) = entry.items()
        if not isinstance(name, str) or documents.name_problem(name):
            raise Malformed(f"{name!r} in documents is not a plain filename")
        if any(name == other for _, other in held):
            raise Malformed(f"{name} is in documents more than once")
        held.append((kind, name))
    return held


def _refuse_taken(held: Sequence[tuple[str, str]], directory: Path, name: str) -> None:
    if any(name == other for _, other in held):
        raise Refused(f"This Application holds a Document called {name} already.")
    if (directory / name).exists() or (directory / name).is_symlink():
        raise Refused(f"There is a file called {name} in this Application's folder already.")


def _position(held: Sequence[tuple[str, str]], name: str) -> int:
    for at, (_, other) in enumerate(held):
        if other == name:
            return at
    raise Refused(f"This Application holds no Document called {name}.")


def refuse_if_sent(application: Application) -> None:
    """Refuse the Deletion of an Application that was ever sent, by the rule of the Sent date:
    what went out to whom stays, and it can only be `closed`."""
    if application.sent:
        raise Refused(
            f"{application.role} at {application.company} was sent, so it cannot be deleted: "
            "set it to closed instead."
        )


def write_down(
    data_dir: Path,
    committer: Committer,
    folder: str,
    *,
    notes: str | None = None,
    address: str | None = None,
) -> None:
    """Save what the user wrote down about the Application in `folder`: its Notes and the
    employer's address, each left as it is when None. Commits the record and only that. Raises
    Refused and NotCommitted."""
    path = directory_of(data_dir, folder) / RECORD_NAME
    try:
        record = records.load(path)
        before = _parse(record, folder)
    except Malformed as malformed:
        raise Refused(f"{folder} was left as it is, fix it by hand first: {malformed}.") from None
    changed = []
    for field, name, text in (("notes", "Notes", notes), ("address", "address", address)):
        if text is None or _as_typed(text) == getattr(before, field):
            continue
        # A record without the field is one where nothing was written down.
        record.pop(field, None)
        if _as_typed(text):
            record[field] = _as_typed(text)
        changed.append(name)
    if not changed:
        return
    records.write(path, _dump(record))
    _commit(committer, f"Change {' and '.join(changed)} of {folder}", folder, [path])


def _as_typed(text: str) -> str:
    """Text as it was typed, line for line, without what nobody typed on purpose: the line ends
    a browser sends, spaces at the end of a line, and empty lines around the whole."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip("\n")


def _dump(record: dict[Any, Any]) -> str:
    """The record as it is written, with what the user wrote down line for line in the file
    whichever field the write was for."""
    return records.dump(
        {
            field: records.Lines(value)
            if field in _WRITTEN_DOWN and isinstance(value, str)
            else value
            for field, value in record.items()
        }
    )


def _written_down(record: dict[Any, Any], field: str) -> str:
    """An optional field the user writes text into. A record without it has nothing in it."""
    value = record.get(field)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise Malformed(f"{field} is not text")
    return _as_typed(value)


def _history(record: dict[Any, Any], current: Status) -> list[Any]:
    """The history so far. A record written by hand may have none; the Status it holds is then
    kept as the first entry, undated, because nobody recorded when it was set."""
    history = record.get("history")
    if history is None:
        return [{"status": current.value}]
    if not isinstance(history, list):
        raise Malformed("history is not a list of Status changes")
    return history


def directory_of(data_dir: Path, folder: str) -> Path:
    """The folder of an existing Application. Anything else, above all a path that would leave
    `applications/`, is not found."""
    root = data_dir / "applications"
    parts = folder.split("/")
    directory = root / folder
    if (
        len(parts) != 2
        or any(part in ("", ".", "..") for part in parts)
        or not directory.is_dir()
        or not directory.resolve().is_relative_to(root.resolve())
    ):
        raise NotFound(f"There is no Application at {folder}.")
    return directory


def _history_entry(status: Status, on: date) -> dict[str, Any]:
    return {"status": status.value, "date": on}


def _folder_part(text: str, field: str) -> str:
    slug = records.slug(text)
    if not slug:
        raise Refused(f"The {field} needs at least one letter or digit to name the folder after.")
    return slug


def _read(path: Path, folder: str) -> Application:
    return _parse(records.load(path), folder)


def _parse(record: dict[Any, Any], folder: str) -> Application:
    return Application(
        folder=folder,
        company=records.text_of(record, "company"),
        role=records.text_of(record, "role"),
        date=_date(record, "date"),
        status=_status(record, "status"),
        notes=_written_down(record, "notes"),
        address=_written_down(record, "address"),
        **_sent(record, _status(record, "status")),
    )


_GONE_OUT = (Status.WAITING, Status.INTERVIEW)


def _sent(record: dict[Any, Any], status: Status) -> dict[str, Any]:
    """Whether the Application went out, and on which day: the day of its first Submission, or
    without one the first time its history reached `waiting` or `interview`. What was written by
    hand is read as far as it can be, and a history that says nothing leaves the current Status
    to decide."""
    submitted = record.get("submissions")
    if isinstance(submitted, list) and submitted:
        days = [
            entry["at"].date()
            for entry in submitted
            if isinstance(entry, dict) and isinstance(entry.get("at"), datetime)
        ]
        if days:
            return {"sent": True, "sent_on": min(days)}
        return {**_sent({"history": record.get("history")}, status), "sent": True}
    history = record.get("history")
    for entry in history if isinstance(history, list) else []:
        if isinstance(entry, dict) and entry.get("status") in _GONE_OUT:
            try:
                return {"sent": True, "sent_on": _date(entry, "date")}
            except Malformed:
                return {"sent": True}
    return {"sent": status in _GONE_OUT}


def _date(record: dict[Any, Any], field: str) -> date:
    value = record.get(field)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise Malformed(f"{field} is missing or is not a date written as YYYY-MM-DD")


def _status(record: dict[Any, Any], field: str) -> Status:
    value = records.text_of(record, field)
    try:
        return Status(value)
    except ValueError:
        raise Malformed(f"{field} is {value!r}, expected one of: {', '.join(Status)}") from None
