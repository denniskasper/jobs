"""The proposal list: which kept Postings wait at the Pursue Gate today, and why the others do not.

Two dates are kept apart. A Posting's first-seen date belongs to the poll that found it. Its
first-shown date is written here, the first time it appears in the list, and is what the age-out
clock starts from. A Posting the daily cap holds back has no first-shown date, so it cannot age
out before it was ever offered, and it comes before anything found after it.

What the list is made with is `proposals.yaml` in the data directory, a human's to edit: the cap,
the age-out period and the global rules.
"""

import threading
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from jobs import applications, records
from jobs.clock import Clock
from jobs.commits import CommitFailed, Committer
from jobs.postings import KeptPosting, list_postings, mark, read_text
from jobs.records import Malformed, NotCommitted, NotFound, Refused
from jobs.relevance import Relevance, Rules, rules_from

SETTINGS = "proposals.yaml"

DEFAULT_AGE_OUT_DAYS = 14

_EXTRACT_LENGTH = 280


@dataclass(frozen=True)
class Settings:
    cap: int | None = None
    """How many Postings are shown for the first time on one day. None shows every one at once
    (decision 46, which takes back the five a day of decision 19)."""
    age_out: timedelta = timedelta(days=DEFAULT_AGE_OUT_DAYS)
    """How long after it was first shown a Posting nobody decided on leaves the list."""
    relevance: Relevance = field(default_factory=Rules)


def read_settings(data_dir: Path) -> Settings:
    """What `proposals.yaml` says, and the defaults for what it does not. Raises Malformed."""
    path = data_dir / SETTINGS
    if not path.exists():
        return Settings()
    record = records.load(path)
    return Settings(
        cap=None if record.get("cap") is None else _whole(record, "cap", 0, at_least=0),
        age_out=timedelta(days=_whole(record, "age_out_days", DEFAULT_AGE_OUT_DAYS, at_least=1)),
        relevance=rules_from(record),
    )


@dataclass(frozen=True)
class Proposal:
    """A Posting waiting at the Pursue Gate."""

    source: str
    posting: KeptPosting
    reason: str
    """Why it is proposed."""
    ages_out: date | None
    """The day it leaves the list undecided. None until it has been shown."""

    @property
    def extract(self) -> str | None:
        """The start of a description too long to read in a list, to open the whole behind. None
        when the description is short enough to be given whole."""
        text = " ".join((self.posting.description or "").split())
        if len(text) <= _EXTRACT_LENGTH:
            return None
        return text[:_EXTRACT_LENGTH].rsplit(" ", 1)[0].rstrip(".,;:") + " …"


class Why(StrEnum):
    PURSUED = "Pursued"
    REJECTED = "Rejected"
    DISMISSED = "Dismissed"
    EXPIRED = "Expired"
    AGED_OUT = "Aged out"


@dataclass(frozen=True)
class LeftOut:
    """A kept Posting that is not proposed, and the reason, in words meant for the user."""

    source: str
    posting: KeptPosting
    why: Why
    reason: str


@dataclass(frozen=True)
class Problem:
    """Something under the data directory the list could not be made with, and why."""

    path: str
    reason: str


@dataclass(frozen=True)
class Listing:
    settings: Settings
    proposals: list[Proposal] = field(default_factory=list)
    """Shown before and undecided, the one shown last at the top."""
    waiting: list[Proposal] = field(default_factory=list)
    """Never shown, the one found first at the top. The cap decides how many are shown today."""
    room: int = 0
    """How many of those waiting the cap still lets through today."""
    left_out: list[LeftOut] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)

    @property
    def held_back(self) -> int:
        return max(len(self.waiting) - self.room, 0)


def sort_out(data_dir: Path, now: datetime, relevance: Relevance | None = None) -> Listing:
    """Every kept Posting put where it belongs today. Nothing is written. `relevance` judges in
    place of the rules in `proposals.yaml`."""
    try:
        settings = read_settings(data_dir)
        if relevance is not None:
            settings = replace(settings, relevance=relevance)
    except Malformed as malformed:
        # Without its rules nothing can be judged, and showing everything instead would start
        # the age-out clock on Postings the rules were there to remove.
        return Listing(settings=Settings(), problems=[Problem(SETTINGS, str(malformed))])
    placed: list[Proposal | LeftOut] = []
    problems: list[Problem] = []
    shown_today = 0
    folders = sorted(path for path in (data_dir / "postings").glob("*") if path.is_dir())
    for folder in folders:
        kept = list_postings(data_dir, folder.name)
        problems += [
            Problem(f"postings/{folder.name}/{problem.name}.yaml", problem.reason)
            for problem in kept.problems
        ]
        for posting in kept.postings:
            if posting.first_shown and posting.first_shown.date() == now.date():
                shown_today += 1
            placed.append(_place(settings, folder.name, posting, now))
    proposals = [each for each in placed if isinstance(each, Proposal)]
    return Listing(
        settings=settings,
        proposals=sorted(
            (each for each in proposals if each.posting.first_shown),
            key=lambda each: _moment_of(each.posting.first_shown),
            reverse=True,
        ),
        waiting=sorted(
            (each for each in proposals if not each.posting.first_shown),
            key=lambda each: (_moment_of(each.posting.first_seen), each.posting.name),
        ),
        room=(len(proposals) if settings.cap is None else max(settings.cap - shown_today, 0)),
        left_out=sorted(
            (each for each in placed if isinstance(each, LeftOut)),
            key=lambda each: _moment_of(each.posting.first_seen),
            reverse=True,
        ),
        problems=problems,
    )


def _place(
    settings: Settings, source: str, posting: KeptPosting, now: datetime
) -> Proposal | LeftOut:
    def left_out(why: Why, reason: str) -> LeftOut:
        return LeftOut(source=source, posting=posting, why=why, reason=reason)

    if posting.pursued:
        return left_out(Why.PURSUED, f"Pursued on {posting.pursued.date().isoformat()}")
    if posting.dismissed:
        return left_out(Why.DISMISSED, f"Dismissed on {posting.dismissed.date().isoformat()}")
    if posting.rejected:
        return left_out(Why.REJECTED, f"Its Source's filter rules removed it: {posting.rejected}")
    verdict = settings.relevance.judge(posting)
    if not verdict.proposed:
        return left_out(Why.REJECTED, f"It was judged not worth proposing: {verdict.reason}")
    if posting.deadline and posting.expired(now.date()):
        return left_out(Why.EXPIRED, f"Its Deadline passed on {posting.deadline.isoformat()}")
    if posting.first_shown is None:
        return Proposal(source, posting, verdict.reason, ages_out=None)
    ages_out = posting.first_shown + settings.age_out
    if ages_out <= now:
        return left_out(
            Why.AGED_OUT,
            f"Nobody decided on it within {settings.age_out.days} days of "
            f"{posting.first_shown.date().isoformat()}, when it was first shown",
        )
    return Proposal(source, posting, verdict.reason, ages_out=ages_out.date())


class Proposals:
    """The proposal list and what a human does to it, the Pursue Gate included."""

    def __init__(
        self,
        data_dir: Path,
        committer: Committer,
        clock: Clock,
        relevance: Relevance | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.committer = committer
        self.clock = clock
        # None leaves the judging to the rules in `proposals.yaml`.
        self.relevance = relevance
        # Two requests at once would each let the cap's worth of Postings through.
        self._one_at_a_time = threading.Lock()

    def look(self) -> Listing:
        """The list as it stands, showing nothing new."""
        return sort_out(self.data_dir, self.clock(), self.relevance)

    def show(self) -> Listing:
        """The list for a human to read. Whatever the cap lets through appears in it for the
        first time, and that moment is written on the Posting, never to be written over. Raises
        NotCommitted."""
        with self._one_at_a_time:
            now = self.clock()
            listing = sort_out(self.data_dir, now, self.relevance)
            paths: list[Path] = []
            for proposal in listing.waiting[: listing.room]:
                try:
                    path = mark(
                        self.data_dir, proposal.source, proposal.posting.name, "first_shown", now
                    )
                except Malformed, OSError:
                    # It stays waiting, and comes first the next time.
                    continue
                if path:
                    paths.append(path)
            if not paths:
                return listing
            self._commit(f"Show {len(paths)} {'Posting' if len(paths) == 1 else 'Postings'}", paths)
            return sort_out(self.data_dir, now, self.relevance)

    def dismiss(self, source: str, name: str) -> None:
        """Take a Posting off the list for good, as a decision. Raises Refused and
        NotCommitted."""
        # With the lock the Pursue Gate takes, so one Posting is never both.
        with self._one_at_a_time:
            found = self._proposed(source, name)
            try:
                path = mark(self.data_dir, source, name, "dismissed", self.clock())
            except (Malformed, OSError) as error:
                raise Refused(f"{source}/{name} was not dismissed: {error}.") from error
            if path:
                self._commit(f"Dismiss {found.role} at {found.company}", [path])

    def pursue(self, source: str, name: str) -> applications.Application:
        """The Pursue Gate: make the proposed Posting an Application that holds it, mark it
        pursued, and commit both as one. Raises Refused and NotCommitted."""
        with self._one_at_a_time:
            # Only what was shown: the gate is a decision on something a human has seen.
            found = self._proposed(source, name, shown=True)
            now = self.clock()
            try:
                posting = read_text(self.data_dir, source, name)
            except OSError as error:
                raise Refused(f"{source}/{name} was not pursued: {error.strerror}.") from error
            scaffolded = applications.scaffold(
                self.data_dir,
                company=found.company,
                role=found.role,
                on=now.date(),
                posting=posting,
            )
            try:
                path = mark(self.data_dir, source, name, "pursued", now)
            except (Malformed, OSError) as error:
                # Without the mark it would be proposed again beside its own Application.
                scaffolded.remove()
                raise Refused(f"{source}/{name} was not pursued: {error}.") from error
            folder = scaffolded.application.folder
            try:
                self.committer.commit(
                    f"Pursue {found.role} at {found.company}",
                    [*scaffolded.paths, *([path] if path else [])],
                )
            except CommitFailed as failed:
                raise NotCommitted(
                    f"{folder} was created but could not be committed: {failed}. The next "
                    "change to its Status will commit the folder, and the next poll that finds "
                    "the Posting will commit the mark on the Posting."
                ) from failed
            return scaffolded.application

    def _proposed(self, source: str, name: str, *, shown: bool = False) -> KeptPosting:
        listing = self.look()
        for each in (*listing.proposals, *([] if shown else listing.waiting)):
            if (each.source, each.posting.name) == (source, name):
                return each.posting
        raise NotFound(f"No Posting {source}/{name} is proposed.")

    def _commit(self, message: str, paths: list[Path]) -> None:
        try:
            self.committer.commit(message, paths)
        except CommitFailed as failed:
            raise NotCommitted(
                f"It was written but could not be committed: {failed}. "
                "The next poll of the Source will commit it too."
            ) from failed


def _moment_of(moment: datetime | None) -> float:
    return moment.timestamp() if moment else 0.0


def _whole(record: dict[object, object], name: str, default: int, *, at_least: int) -> int:
    value = record.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < at_least:
        raise Malformed(f"{name} is not a whole number of at least {at_least}")
    return value
