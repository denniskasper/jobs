"""Applications, kept in the files that are their only store (ADR 0002).

An Application is a folder under `applications/`, named by company and dated role. The folder is
the record; `application.yaml` inside it is its metadata. Files are edited by hand, so a record
that cannot be read is reported as a Problem beside the ones that can, never raised.
"""

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from jobs import documents, records
from jobs.commits import CommitFailed, Committer
from jobs.postings import KeptPosting, read_posting
from jobs.records import AlreadyExists, Malformed, NotCommitted, NotFound, Refused

RECORD_NAME = "application.yaml"

# The fields the user writes text into, on the Application's page or by hand.
_WRITTEN_DOWN = ("notes", "address")

# The Posting an Application was created from at the Pursue Gate, as it was kept when ticked.
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


def create_application(
    data_dir: Path, committer: Committer, *, company: str, role: str, on: date
) -> Application:
    """Create an Application by hand, with no Posting, and commit it. Raises Refused and
    NotCommitted."""
    scaffolded = scaffold(data_dir, company=company, role=role, on=on)
    folder = scaffolded.application.folder
    _commit(committer, f"Create Application {folder}", folder, scaffolded.paths)
    return scaffolded.application


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
    in the shared Documents. `posting` is the text of the Posting it holds, stored beside the
    record. Raises Refused. Nothing is committed here."""
    application = Application(
        folder=f"{_folder_part(company, 'company')}/{on.isoformat()}-{_folder_part(role, 'role')}",
        company=company.strip(),
        role=role.strip(),
        date=on,
        status=Status.TODO,
    )
    record = {
        "company": application.company,
        "role": application.role,
        "date": application.date,
        "status": application.status.value,
        "history": [_history_entry(application.status, on)],
    }
    directory = data_dir / "applications" / application.folder
    try:
        shared = documents.list_shared(data_dir)
    except Malformed as malformed:
        raise Refused(
            f"{application.folder} was not created, because the shared Documents cannot be "
            f"linked into it: {malformed}. Fix that by hand first."
        ) from None
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
    )


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
