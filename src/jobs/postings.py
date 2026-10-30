"""Postings, kept one file each under `postings/<source>/` (ADR 0002).

A Posting's identity decides which file it is, so the same position found again next week is
written over the file it already has and never becomes a second one. Identity is scoped to the
Source: the folder is part of it, and the same position found through two Sources is two Postings.
"""

import hashlib
import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from jobs import records
from jobs.extraction import Posting, Remuneration
from jobs.fetching import Walked
from jobs.records import Malformed

IdentifiedBy = Literal["identifier", "url", "content"]

# Query parameters that say where a visitor came from, never which page is meant.
_TRACKING = ("utm_", "fbclid", "gclid")

_DEFAULT_PORTS = {"http": 80, "https": 443}

# A LinkedIn job's page, by its ID alone or after a slug of its title.
_LINKEDIN_VIEW = re.compile(r"^(?:/comm)?/jobs/view/(?:[^/]*-)?(?P<id>\d+)/?$")

# When a Posting was found, which every poll moves on and which is no change to the Posting.
_SEEN = ("first_seen", "last_seen")

# A poll refreshes a Posting's file while a request marks it, and each reads the file before it
# writes it. One at a time, so neither writes over what the other just wrote.
_writing = threading.Lock()


@dataclass(frozen=True)
class Identity:
    """What makes a Posting the same Posting when it is found again, within its Source."""

    by: IdentifiedBy
    value: str
    readable: str = field(compare=False)
    """What the identity was made from, in words, for the name of the file."""

    @property
    def name(self) -> str:
        """What the Posting's file is called. Made from the identity alone, so a Posting whose
        role is reworded keeps its file."""
        digest = hashlib.sha256(f"{self.by}\n{self.value}".encode()).hexdigest()[:10]
        readable = records.slug(self.readable)[-60:].strip("-")
        return f"{readable}-{digest}" if readable else digest


def identity(posting: Posting, *, own_url: bool) -> Identity:
    """In order of preference: the identifier the site publishes, otherwise the canonical URL of
    the posting page, otherwise a hash over company, role and location. `own_url` says whether
    the URL is the Posting's alone; that of a listing carrying several is nobody's identity."""
    if posting.identifier:
        return Identity(by="identifier", value=posting.identifier, readable=posting.identifier)
    if own_url:
        canonical = canonical_url(posting.url)
        return Identity(by="url", value=canonical, readable=canonical.partition("://")[2])
    parts = [_normalised(part or "") for part in (posting.company, posting.role, posting.location)]
    return Identity(
        by="content",
        value=hashlib.sha256("\n".join(parts).encode()).hexdigest(),
        readable=" ".join(parts),
    )


def canonical_url(url: str) -> str:
    """The URL without what changes from one visit to the next: the case of the host, a default
    port, a fragment, and the parameters that track where a visitor came from. A LinkedIn job's
    address is the job's own, whichever of its addresses it came as."""
    url = job_link(url)
    parts = urlsplit(url)
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        host = f"{host}:{port}"
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith(_TRACKING)
        ]
    )
    return urlunsplit((scheme, host, parts.path or "/", query, ""))


def job_link(url: str) -> str:
    """The address a link is kept under: every LinkedIn address that names a job, as
    `/jobs/view/<id>` with or without a slug, under `/comm/` from an alert email, under a country's
    subdomain, or as `currentJobId=<id>` on a search or recommendations page, is
    `https://www.linkedin.com/jobs/view/<id>`. Any other is kept as it came, `lnkd.in` among them,
    since resolving a short link would mean fetching it. The only rule for one site."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() not in ("http", "https") or not (
        host == "linkedin.com" or host.endswith(".linkedin.com")
    ):
        return url
    viewed = _LINKEDIN_VIEW.match(parts.path)
    current = [
        value for key, value in parse_qsl(parts.query) if key == "currentJobId" and value.isdigit()
    ]
    job = viewed["id"] if viewed else current[0] if current else None
    return f"https://www.linkedin.com/jobs/view/{job}" if job else url


def _normalised(text: str) -> str:
    return " ".join(text.casefold().split())


@dataclass(frozen=True)
class Kept:
    """What keeping one walk's Postings came to."""

    paths: list[Path]
    """Every file written, new or refreshed."""
    new: int
    changed: int
    """Postings seen before whose content is no longer what it was."""
    postings: int
    rejected: int
    problems: list[str]
    """What could not be kept, and why, in words meant for the user."""


def keep(
    data_dir: Path, source: str, walked: Walked, *, posting_pages: bool, now: datetime
) -> Kept:
    """Write what a walk of `source` brought back: a new file for a Posting not seen before, the
    same file refreshed for one that was. What the Source's filter rules removed is kept too,
    with the reason. `posting_pages` says whether each page read is meant to be one Posting's
    own, and not the registered URL itself or a page listing several. Nothing is committed here."""
    now = now.replace(microsecond=0)
    found: list[tuple[Posting, str | None]] = [(posting, None) for posting in walked.postings]
    found += [(each.posting, each.reason) for each in walked.rejected]
    urls = [posting.url for posting, _ in found]
    folder = data_dir / "postings" / source
    paths: list[Path] = []
    problems: list[str] = []
    new = changed = 0
    written: dict[str, Posting] = {}
    for posting, rejected in found:
        # A posting page's URL is the Posting's own as long as the page carries no other.
        identified = identity(posting, own_url=posting_pages and urls.count(posting.url) == 1)
        if identified.name in written:
            first = written[identified.name]
            # A page that carries the same record twice is one Posting and no trouble. Anything
            # else is two Postings about to become one, which must not happen quietly.
            if posting != first:
                problems.append(
                    f"{posting.role} at {posting.url} and {first.role} at {first.url} have the "
                    f"same {identified.by}, so only the first was kept"
                )
            continue
        written[identified.name] = posting
        path = folder / f"{identified.name}.yaml"
        with _writing:
            try:
                before = records.load(path) if path.exists() else None
            except Malformed as malformed:
                problems.append(
                    f"{posting.role} was not refreshed, because the file it is kept in cannot "
                    f"be read: {malformed}"
                )
                continue
            record = _record(posting, identified, rejected, before, now)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                records.write(path, records.dump(record))
            except OSError as error:
                problems.append(f"{posting.role} could not be written: {error.strerror}")
                continue
        if before is None:
            new += 1
        elif _content(before) != _content(record):
            changed += 1
        paths.append(path)
    return Kept(
        paths=paths,
        new=new,
        changed=changed,
        postings=len(walked.postings),
        rejected=len(walked.rejected),
        problems=problems,
    )


Mark = Literal["first_shown", "dismissed", "pursued"]


def mark(data_dir: Path, source: str, name: str, what: Mark, moment: datetime) -> Path | None:
    """Write down when something happened to a kept Posting, once: a moment already there is
    never written over. Returns the file when it was written. Raises Malformed when the file
    cannot be read. Nothing is committed here."""
    path = _path(data_dir, source, name)
    with _writing:
        record = records.load(path)
        if record.get(what) is not None:
            return None
        record[what] = moment.replace(microsecond=0)
        records.write(path, records.dump(record))
    return path


def pursued_as(data_dir: Path, identity: tuple[str, str]) -> list[tuple[str, str]]:
    """The Source and name of every kept Posting with `identity` that is marked pursued. A file
    that cannot be read is none of them."""
    return [
        (folder.name, posting.name)
        for folder in sorted(path for path in (data_dir / "postings").glob("*") if path.is_dir())
        for posting in list_postings(data_dir, folder.name).postings
        if posting.pursued and posting.identity == identity
    ]


def read_text(data_dir: Path, source: str, name: str) -> str:
    """A kept Posting as it stands in its file, to be stored somewhere else as well. Raises
    OSError."""
    with _writing:
        return _path(data_dir, source, name).read_text(encoding="utf-8")


def _path(data_dir: Path, source: str, name: str) -> Path:
    return data_dir / "postings" / source / f"{name}.yaml"


def _record(
    posting: Posting,
    identified: Identity,
    rejected: str | None,
    before: dict[Any, Any] | None,
    now: datetime,
) -> dict[Any, Any]:
    refreshed = {
        "identity": {"by": identified.by, "value": identified.value},
        "company": posting.company,
        "role": posting.role,
        "location": posting.location,
        "url": posting.url,
        "identifier": posting.identifier,
        "posted": posting.posted,
        "deadline": posting.deadline,
        "remuneration": _remuneration(posting.remuneration),
        "rejected": rejected,
        "first_seen": (before or {}).get("first_seen", now),
        "last_seen": now,
        "description": posting.description,
        "raw": posting.raw,
    }
    record = {name: value for name, value in refreshed.items() if value is not None}
    # Everything else the file held goes after, in the order it was in.
    record.update({name: value for name, value in (before or {}).items() if name not in refreshed})
    return record


def _content(record: dict[Any, Any]) -> dict[Any, Any]:
    return {name: value for name, value in record.items() if name not in _SEEN}


def _remuneration(remuneration: Remuneration | None) -> dict[str, Any] | None:
    if remuneration is None:
        return None
    return {
        "currency": remuneration.currency,
        # As text, so the figure stays exactly what the site published.
        "low": str(remuneration.low),
        "high": str(remuneration.high),
        "per": remuneration.per,
    }


# Reading what is kept


@dataclass(frozen=True)
class KeptPosting:
    """A Posting as it is kept on disk, with what showing it takes."""

    name: str
    company: str
    role: str
    location: str | None
    url: str | None
    deadline: date | None
    first_seen: datetime | None
    last_seen: datetime | None
    rejected: str | None
    """Why the Source's filter rules removed it, when they did."""
    description: str | None = None
    identifier: str | None = None
    """What the site calls it: its reference number, where it publishes one."""
    posted: date | None = None
    pay: str | None = None
    """What it pays, in the figures the site published."""
    first_shown: datetime | None = None
    """When it first appeared in the proposal list, which is what starts the age-out clock."""
    dismissed: datetime | None = None
    pursued: datetime | None = None
    """When it was ticked at the Pursue Gate and became an Application."""
    brought_in_by_hand: date | None = None
    """The day it was brought in by hand with its Application, for one that belongs to no
    Source."""
    identity: tuple[str, str] | None = None
    """How it is identified within its Source, and by what value; None for one brought in by
    hand, which belongs to no Source."""

    def expired(self, today: date) -> bool:
        """Past its Deadline. The day of the Deadline itself still counts."""
        return self.deadline is not None and self.deadline < today


@dataclass(frozen=True)
class Problem:
    """A file under `postings/` that could not be read as a Posting, and why."""

    name: str
    reason: str


@dataclass(frozen=True)
class Inventory:
    postings: list[KeptPosting]
    problems: list[Problem]


def list_postings(data_dir: Path, source: str) -> Inventory:
    """The Postings kept for a Source, the one seen first most recently at the top."""
    inventory = Inventory(postings=[], problems=[])
    for path in sorted((data_dir / "postings" / source).glob("*.yaml")):
        try:
            inventory.postings.append(read_posting(path))
        except Malformed as malformed:
            inventory.problems.append(Problem(name=path.stem, reason=str(malformed)))
    inventory.postings.sort(
        key=lambda posting: (
            posting.first_seen.timestamp() if posting.first_seen else 0.0,
            posting.role.casefold(),
        ),
        reverse=True,
    )
    return inventory


def read_posting(path: Path) -> KeptPosting:
    """The Posting kept in one file, wherever that file is. Raises Malformed."""
    return _parse(records.load(path), path.stem)


def _parse(record: dict[Any, Any], name: str) -> KeptPosting:
    return KeptPosting(
        name=name,
        company=records.text_of(record, "company"),
        role=records.text_of(record, "role"),
        location=_optional_text(record, "location"),
        url=_optional_text(record, "url"),
        deadline=_day(record, "deadline"),
        first_seen=_moment(record, "first_seen"),
        last_seen=_moment(record, "last_seen"),
        rejected=_optional_text(record, "rejected"),
        description=_optional_text(record, "description"),
        identifier=_optional_text(record, "identifier"),
        posted=_day(record, "posted"),
        pay=_pay(record.get("remuneration")),
        first_shown=_moment(record, "first_shown"),
        dismissed=_moment(record, "dismissed"),
        pursued=_moment(record, "pursued"),
        brought_in_by_hand=_day(record, "brought_in_by_hand"),
        identity=_identity(record),
    )


def _identity(record: dict[Any, Any]) -> tuple[str, str] | None:
    identified = record.get("identity")
    if not isinstance(identified, dict):
        return None
    by, value = identified.get("by"), identified.get("value")
    return (by, value) if isinstance(by, str) and isinstance(value, str) else None


def _pay(remuneration: Any) -> str | None:
    """A kept remuneration in words: `EUR 52000 to 61000 per year`, with whatever is missing
    left out."""
    if not isinstance(remuneration, dict) or remuneration.get("low") is None:
        return None
    low, high = str(remuneration["low"]), str(remuneration.get("high") or remuneration["low"])
    currency, per = remuneration.get("currency"), remuneration.get("per")
    return " ".join(
        [
            *([str(currency)] if currency else []),
            low if low == high else f"{low} to {high}",
            *([f"per {str(per).lower()}"] if per else []),
        ]
    )


def _optional_text(record: dict[Any, Any], name: str) -> str | None:
    value = record.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise Malformed(f"{name} is not text")
    return value.strip() or None


def _day(record: dict[Any, Any], name: str) -> date | None:
    value = record.get(name)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, date):
        raise Malformed(f"{name} is not a date")
    return value


def _moment(record: dict[Any, Any], name: str) -> datetime | None:
    value = record.get(name)
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise Malformed(f"{name} is not a date and time")
    # Written by hand without an offset, it is taken as the UTC the system writes. With another
    # one it is brought to UTC, so its day is the day the clock would have given it.
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
