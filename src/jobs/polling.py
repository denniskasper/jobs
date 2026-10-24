"""Polling: walk a Source, keep what it found, and record how the poll went.

A poll is plain code from end to end and never asks a model (ADR 0003). How each Source's last
poll went is kept in `polls/<source>.yaml`, apart from the Source's own file, which is a human's
to edit. That record is also what the schedule reads, so a restart neither forgets a poll nor
repeats one.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from jobs import records
from jobs.clock import Clock
from jobs.commits import CommitFailed, Committer
from jobs.fetching import Network, walk
from jobs.postings import keep
from jobs.records import Malformed, NotCommitted, Refused
from jobs.sources import Problem, Source, list_sources, read_source


@dataclass(frozen=True)
class Schedule:
    """How often Sources are polled with nobody asking."""

    every: timedelta
    """How long after its last poll a Source is due again."""
    check_every: float = 60.0
    """Seconds between two looks at which Sources are due."""


@dataclass(frozen=True)
class PollError:
    """One thing a poll could not do, and why, in words meant for the user."""

    reason: str
    url: str | None = None
    status: int | None = None
    """What the host answered, when the trouble was the host's answer."""
    unhealthy: bool = False
    """Whether this alone flags the Source, without waiting for more polls: a page that could not
    be fetched at all, one its selectors could not read, or a poll that failed outright."""
    unread: bool = False
    """Whether the page was fetched and the Source's selectors could not read it."""


@dataclass(frozen=True)
class Poll:
    """How one poll of one Source went."""

    polled: datetime
    postings: int
    """How many Postings the Source's filter rules kept."""
    rejected: int
    new: int
    changed: int
    errors: tuple[PollError, ...]
    empty_polls: int = 0
    """How many polls in a row, this one included, found no Posting at all, not even one the
    Source's filter rules removed."""


class Condition(StrEnum):
    HEALTHY = "healthy"
    QUIET = "quiet"
    """Nothing found lately, but not for long enough to call the Source unhealthy."""
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True)
class Health:
    """Whether a Source still works, as far as its polls can tell (ADR 0003)."""

    condition: Condition
    reason: str | None = None
    """Why the Source is not simply healthy, in words meant for the user."""


def health_of(source: Source, poll: Poll | None) -> Health | None:
    """What the last poll says about a Source, or None when it has never been polled. Read from
    the poll record and the Source's own threshold every time, so it survives a restart and an
    edit to the threshold takes effect at once."""
    if poll is None:
        return None
    flagging = [error for error in poll.errors if error.unhealthy]
    if flagging:
        first = flagging[0]
        could_not = "could not be read" if first.unread else "could not be fetched"
        what = f"{first.url} {could_not}: {first.reason}" if first.url else first.reason
        more = f", and {len(flagging) - 1} more" if len(flagging) > 1 else ""
        return Health(Condition.UNHEALTHY, f"{what}{more}")
    if poll.empty_polls >= source.unhealthy_after:
        return Health(Condition.UNHEALTHY, f"no Posting on {poll.empty_polls} consecutive polls")
    if poll.empty_polls:
        polls = "poll" if poll.empty_polls == 1 else f"{poll.empty_polls} polls"
        return Health(
            Condition.QUIET,
            f"no Posting on the last {polls}, flagged unhealthy after {source.unhealthy_after}",
        )
    return Health(Condition.HEALTHY)


def last_poll(data_dir: Path, source: str) -> Poll | None:
    """How the last poll of a Source went, or None when it has never been polled or the record
    of it cannot be read."""
    try:
        record = records.load(_path(data_dir, source))
        polled = record.get("polled")
        if not isinstance(polled, datetime):
            return None
        return Poll(
            # Written by hand without an offset, it is taken as the UTC the system writes.
            polled=polled if polled.tzinfo else polled.replace(tzinfo=UTC),
            postings=_count(record, "postings"),
            rejected=_count(record, "rejected"),
            new=_count(record, "new"),
            changed=_count(record, "changed"),
            errors=tuple(_error(error) for error in record.get("errors") or []),
            # A record from before polls were counted says at least whether its own was empty.
            empty_polls=_count(record, "empty_polls")
            if "empty_polls" in record
            else int(not _count(record, "postings") and not _count(record, "rejected")),
        )
    except Malformed:
        return None


class Poller:
    """Polls Sources one run at a time, whether the schedule or a human asked."""

    def __init__(
        self,
        data_dir: Path,
        committer: Committer,
        network: Network,
        clock: Clock,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.data_dir = data_dir
        self.committer = committer
        self.network = network
        self.clock = clock
        self.sleep = sleep
        # Two runs at once would ask one host twice as often as its interval allows, and write
        # the same files.
        self._one_at_a_time = threading.Lock()

    def poll(self, name: str) -> None:
        """Poll one Source because a human asked. Refused when it cannot be polled."""
        found, _ = read_source(self.data_dir, name)
        if isinstance(found, Problem):
            raise Refused(f"{name} was not polled, fix it by hand first: {found.reason}.")
        if not found.enabled:
            raise Refused(f"{name} was not polled, because it is disabled.")
        if found.not_pollable:
            raise Refused(f"{name} cannot be polled: {found.not_pollable}.")
        with self._one_at_a_time:
            self._run([found])

    def poll_all(self) -> None:
        """Poll every Source that can be, because a human asked."""
        with self._one_at_a_time:
            self._run(_pollable(self.data_dir))

    def poll_due(self, every: timedelta) -> None:
        """Poll the Sources whose last poll is at least `every` ago, or that have none."""
        # Decided with the lock held: a run that was waited for may have polled them already.
        with self._one_at_a_time:
            self._run([source for source in _pollable(self.data_dir) if self._due(source, every)])

    def _due(self, source: Source, every: timedelta) -> bool:
        last = last_poll(self.data_dir, source.name)
        return last is None or self.clock() - last.polled >= every

    def _run(self, sources: list[Source]) -> None:
        paths: list[Path] = []
        new = changed = 0
        for source in sources:
            poll, written = self._poll(source)
            new, changed = new + poll.new, changed + poll.changed
            paths += written
        if not paths:
            return
        polled = sources[0].name if len(sources) == 1 else f"{len(sources)} Sources"
        try:
            self.committer.commit(f"Poll {polled}: {new} new, {changed} changed", paths)
        except CommitFailed as failed:
            raise NotCommitted(
                f"What the poll found was written but could not be committed: {failed}. "
                "The next poll will commit it too."
            ) from failed

    def _poll(self, source: Source) -> tuple[Poll, list[Path]]:
        """One Source, polled and recorded. Whatever goes wrong stays with this Source, so the
        rest of the run goes ahead."""
        polled = self.clock().replace(microsecond=0)
        paths: list[Path] = []
        try:
            walked = walk(source.url, source.config, self.network, sleep=self.sleep)
            kept = keep(
                self.data_dir,
                source.name,
                walked,
                posting_pages=source.config.reads_posting_pages,
                now=polled,
            )
            paths += kept.paths
            poll = Poll(
                polled=polled,
                postings=kept.postings,
                rejected=kept.rejected,
                new=kept.new,
                changed=kept.changed,
                errors=(
                    *(
                        PollError(
                            url=error.url,
                            status=error.status,
                            reason=error.reason,
                            unhealthy=error.fetch or error.unread,
                            unread=error.unread,
                        )
                        for error in walked.errors
                    ),
                    *(PollError(reason=problem) for problem in kept.problems),
                ),
            )
        except Exception as error:
            # Deliberately everything: one Source must never take the run down with it.
            failure = PollError(
                reason=f"the poll failed: {type(error).__name__}: {error}", unhealthy=True
            )
            poll = Poll(polled=polled, postings=0, rejected=0, new=0, changed=0, errors=(failure,))
        if not poll.postings and not poll.rejected:
            before = last_poll(self.data_dir, source.name)
            poll = replace(poll, empty_polls=(before.empty_polls if before else 0) + 1)
        path = _path(self.data_dir, source.name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            records.write(path, records.dump(_record(poll)))
            paths.append(path)
        except OSError:
            # Without its record the Source is simply due again at the next check.
            pass
        return poll, paths


def _pollable(data_dir: Path) -> list[Source]:
    sources = list_sources(data_dir).sources
    return [source for source in sources if source.enabled and not source.not_pollable]


def _path(data_dir: Path, source: str) -> Path:
    return data_dir / "polls" / f"{source}.yaml"


def _record(poll: Poll) -> dict[str, Any]:
    return {
        "polled": poll.polled,
        "postings": poll.postings,
        "rejected": poll.rejected,
        "new": poll.new,
        "changed": poll.changed,
        "empty_polls": poll.empty_polls,
        "errors": [
            {
                name: value
                for name, value in vars(error).items()
                if value is not None and value is not False
            }
            for error in poll.errors
        ],
    }


def _count(record: dict[Any, Any], name: str) -> int:
    value = record.get(name, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Malformed(f"{name} is not a number")
    return value


def _error(error: Any) -> PollError:
    if not isinstance(error, dict):
        raise Malformed("an error is not a mapping of fields to values")
    url, status = error.get("url"), error.get("status")
    return PollError(
        reason=str(error.get("reason") or ""),
        url=url if isinstance(url, str) else None,
        status=status if isinstance(status, int) and not isinstance(status, bool) else None,
        unhealthy=error.get("unhealthy") is True,
        unread=error.get("unread") is True,
    )
