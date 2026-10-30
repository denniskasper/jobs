"""Shared Documents, stored once under their content hash and linked into Application folders
(ADR 0002).

`documents/store/` holds the bytes, each file named by the SHA-256 of its content.
`documents/shared.yaml` says which of them a new Application gets, and under which filename; one
can be added to an Application later as well. The links are relative symlinks, which git stores as
they are, so they resolve in every clone.

Only the scaffolding half of ADR 0002 lives here. Nothing is frozen and no Manifest is written;
both belong to the Send Gate.
"""

import hashlib
import os
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from jobs import records
from jobs.commits import CommitFailed, Committer
from jobs.records import Malformed, NotCommitted, Refused

SHARED_NAME = "shared.yaml"

# The filenames in an Application folder that a shared Document cannot take: its record, the
# Posting it holds, its Letter, and the Conversation about that.
_NOT_DOCUMENTS = ("application.yaml", "posting.yaml", "letter.md", "conversation.yaml")

_EXTENSION = r"\.[a-z0-9]{1,10}"
_STORED = re.compile(rf"[0-9a-f]{{64}}({_EXTENSION})?")

# What a Document can be, told by how its bytes begin, and the extensions its name may have.
_KINDS = {"PDF": b"%PDF-", "PNG": b"\x89PNG\r\n\x1a\n", "JPG": b"\xff\xd8\xff"}
_EXTENSIONS = {"PDF": (".pdf",), "PNG": (".png",), "JPG": (".jpg", ".jpeg")}

# What most filesystems allow a filename, in bytes.
_LONGEST_NAME = 255

# Adding reads `shared.yaml`, changes it and writes it back, and requests are served from threads.
_one_at_a_time = threading.Lock()


@dataclass(frozen=True)
class SharedDocument:
    """One Document every new Application gets: the filename it is linked in under, and the
    file in the store that holds its bytes."""

    name: str
    stored: str
    size: int

    @property
    def content_hash(self) -> str:
        return self.stored.partition(".")[0]


def list_shared(data_dir: Path) -> list[SharedDocument]:
    """The shared Documents, by filename. Raises Malformed when `shared.yaml` cannot be read or
    names bytes the store does not hold: a link to nothing is worse than no link."""
    path = _root(data_dir) / SHARED_NAME
    # A file a human has emptied shares nothing, the same as no file.
    if not path.exists() or not path.read_bytes().strip():
        return []
    entries = records.load(path).get("documents") or {}
    if not isinstance(entries, dict):
        raise Malformed(f"documents in {SHARED_NAME} is not a mapping of filenames to stored files")
    shared = []
    for name, stored in entries.items():
        if not isinstance(name, str) or name_problem(name):
            raise Malformed(f"{name!r} in {SHARED_NAME} is not a plain filename")
        if not isinstance(stored, str) or not _STORED.fullmatch(stored):
            raise Malformed(f"{name} in {SHARED_NAME} does not name a file in the store")
        held = _store(data_dir) / stored
        if not held.is_file():
            raise Malformed(
                f"{name} in {SHARED_NAME} names {stored}, which the store does not hold"
            )
        size = held.stat().st_size
        shared.append(SharedDocument(name=name, stored=stored, size=size))
    return sorted(shared, key=lambda document: document.name)


def add_shared(data_dir: Path, committer: Committer, *, name: str, content: bytes) -> None:
    """Store `content` once, make it the shared Document called `name`, and commit both.

    Bytes the store already holds are not written again. A name that is already taken moves to
    the new bytes; Applications linked to the old ones keep them.
    """
    name = name.strip()
    refuse_unless_document(name, content)
    with _one_at_a_time:
        _add(data_dir, committer, name, content)


def _add(data_dir: Path, committer: Committer, name: str, content: bytes) -> None:
    try:
        shared = {document.name: document.stored for document in list_shared(data_dir)}
    except Malformed as malformed:
        raise Refused(f"Nothing was added, fix {SHARED_NAME} by hand first: {malformed}.") from None
    stored = hashlib.sha256(content).hexdigest() + _suffix(name)
    if shared.get(name) == stored:
        return
    target = _store(data_dir) / stored
    record = _root(data_dir) / SHARED_NAME
    shared[name] = stored
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not _holds(target, content):
            _write_bytes(target, content)
        records.write(record, records.dump({"documents": dict(sorted(shared.items()))}))
    except OSError as error:
        raise Refused(f"{name} could not be stored: {error.strerror}.") from error
    try:
        committer.commit(f"Add shared Document {name}", [target, record])
    except CommitFailed as failed:
        raise NotCommitted(
            f"{name} was stored but could not be committed: {failed}. It is on disk and will "
            "be linked in; commit documents/ by hand."
        ) from failed


def refuse_unless_document(name: str, content: bytes) -> None:
    """Refuse a file that cannot be a Document called `name`: one without a plain filename, an
    empty one, and one that is not a PDF, JPG or PNG, which a merged PDF could not take."""
    problem = name_problem(name)
    if problem:
        raise Refused(f"The filename {problem}.")
    if not content:
        raise Refused("The file is empty, or none was chosen.")
    kind = next((kind for kind, start in _KINDS.items() if content.startswith(start)), None)
    if kind is None:
        raise Refused(f"Only a PDF, JPG or PNG can be a Document, and {name} is none of them.")
    if PurePosixPath(name).suffix.lower() not in _EXTENSIONS[kind]:
        raise Refused(f"{name} is a {kind}, so its name has to end in {_EXTENSIONS[kind][0]}.")


def link(data_dir: Path, shared: Sequence[SharedDocument], *, into: Path) -> list[Path]:
    """Link the `shared` Documents into the Application folder `into`, to the bytes each names
    now, and return the links."""
    links = []
    for document in shared:
        link = into / document.name
        # Relative, so the link holds wherever the data repository is cloned to.
        link.symlink_to(os.path.relpath(_store(data_dir) / document.stored, start=into))
        links.append(link)
    return links


def links_in(data_dir: Path, directory: Path) -> list[Path]:
    """The links into the store that the Application folder `directory` holds. Any other link
    in it is a human's, and not the system's to commit."""
    store = _store(data_dir).resolve()
    return sorted(
        each for each in directory.iterdir() if each.is_symlink() and each.resolve().parent == store
    )


def _holds(target: Path, content: bytes) -> bool:
    """Whether the store already has these bytes. Read rather than taken from the filename: a
    stored file that was saved over through one of its links no longer holds what it is named
    after, and adding the Document again is what puts that right."""
    try:
        return target.read_bytes() == content
    except OSError:
        return False


def _root(data_dir: Path) -> Path:
    return data_dir / "documents"


def _store(data_dir: Path) -> Path:
    return _root(data_dir) / "store"


def name_problem(name: str) -> str | None:
    if not name:
        return "is missing"
    if PurePosixPath(name).name != name or "\\" in name or name in (".", ".."):
        return "has to be a plain filename, without a folder"
    if name.startswith(".") or not name.isprintable():
        return "has to start with a letter or digit and hold no control characters"
    if len(name.encode()) > _LONGEST_NAME:
        return f"is longer than the {_LONGEST_NAME} bytes a filename can have"
    if name in _NOT_DOCUMENTS:
        return f"cannot be {name}, which an Application keeps for itself"
    return None


def _suffix(name: str) -> str:
    """The extension the stored file keeps, so the store can be browsed and opened too."""
    suffix = PurePosixPath(name).suffix.lower()
    return suffix if re.fullmatch(_EXTENSION, suffix) else ""


def _write_bytes(path: Path, content: bytes) -> None:
    """In one step, so a write that fails never leaves a file whose name is not its hash."""
    draft = path.with_name(f".{path.name}.draft")
    try:
        draft.write_bytes(content)
        draft.replace(path)
    finally:
        draft.unlink(missing_ok=True)
