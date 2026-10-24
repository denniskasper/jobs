"""Registrations: every attempt to register a Source, from handing in the URL to its end.

They are kept in memory, on purpose. The app is one process with one worker, so every request sees
the same registry, and a restart ends the thread that was exploring: a record that outlived it
would describe a process that no longer exists. What a restart costs is every Registration that
had not ended, and the end of one that nobody had read yet, remedied by registering again.
Nothing about a Registration is written to the data directory except the Source it ends in.
"""

import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from jobs.clock import Clock
from jobs.commits import Committer
from jobs.fetching import Network
from jobs.models import Model
from jobs.records import AlreadyExists, NotCommitted, Refused
from jobs.sources import name_for, register

# How the exploring of a Registration gets run: handed the work, it sees to it that it is done.
Runner = Callable[[Callable[[], None]], None]


def on_a_daemon_thread(work: Callable[[], None]) -> None:
    """The Runner of the running service. A daemon, so that shutdown never waits for it."""
    threading.Thread(target=work, daemon=True).start()


class State(StrEnum):
    WAITING = "waiting"
    UNDER_WAY = "under way"
    REFUSED = "refused"
    REGISTERED = "registered"


@dataclass
class Registration:
    """One attempt to register a Source, with what was entered."""

    identifier: int
    url: str
    label: str
    rule: str
    # The name its Source would get, which it claims until it has ended.
    name: str
    handed_in: datetime
    state: State = State.WAITING
    started: datetime | None = None
    # Why it was refused.
    reason: str | None = None
    # The name of the Source it ended in.
    source: str | None = None
    # What is left to read about that Source: that it was written and could not be committed.
    note: str | None = None
    # As it stood when it was looked at, and told only by `look`: how many are explored before
    # this waiting one, and for how long this one under way has been.
    ahead: int = 0
    under_way_for: timedelta | None = None

    @property
    def title(self) -> str:
        return self.label or self.url

    @property
    def entered(self) -> dict[str, str]:
        """What the form was handed in with, under the names of its fields."""
        return {"url": self.url, "label": self.label, "rule": self.rule}

    @property
    def refused(self) -> bool:
        return self.state is State.REFUSED

    @property
    def ended(self) -> bool:
        return self.state in (State.REFUSED, State.REGISTERED)

    @property
    def waiting(self) -> bool:
        return self.state is State.WAITING

    @property
    def under_way_for_in_words(self) -> str:
        minutes, seconds = divmod(int((self.under_way_for or timedelta()).total_seconds()), 60)
        return f"{minutes} min {seconds} s" if minutes else f"{seconds} s"

    @property
    def to_be_cleared(self) -> bool:
        """Whether it ended with something to read, and so stays until the user clears it. One
        that ended cleanly goes once it has been seen."""
        return self.refused or self.note is not None


class Registrations:
    """Takes Registrations on and explores them one at a time, in the order they were handed in.
    Without a `runner`, handing one in only queues it, and `run_next` is for whoever holds this
    to call."""

    def __init__(
        self,
        data_dir: Path,
        committer: Committer,
        network: Network,
        model: Model,
        clock: Clock,
        sleep: Callable[[float], None] = time.sleep,
        runner: Runner | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.committer = committer
        self.network = network
        self.model = model
        self.clock = clock
        self.sleep = sleep
        self.runner = runner
        self._all: list[Registration] = []
        self._handed_in = 0
        self._registry = threading.Lock()
        # Pacing is kept per exploration, so two at once could ask one host twice as often as
        # allowed. Not the Poller's lock: a poll of every Source must not hold a Registration up.
        self._one_at_a_time = threading.Lock()

    def hand_in(self, *, url: str, label: str, rule: str) -> None:
        """Take a Registration on, to be explored later. Refused at once for what can be told
        without the network or the model, which includes a name that one not yet ended claims:
        two of them would race for one file."""
        url, label, rule = url.strip(), label.strip(), rule.strip()
        # Checked and taken on in one go, so that Register pressed twice claims the name once.
        with self._registry:
            name = name_for(self.data_dir, url=url, label=label)
            if any(each.name == name and not each.ended for each in self._all):
                raise AlreadyExists(
                    f"A Registration that has not ended yet already claims the name {name}. "
                    "Give this one another label."
                )
            self._handed_in += 1
            self._all.append(
                Registration(
                    identifier=self._handed_in,
                    url=url,
                    label=label,
                    rule=rule,
                    name=name,
                    handed_in=self.clock(),
                )
            )
        if self.runner:
            self.runner(self.run_next)

    def look(self) -> list[Registration]:
        """Every Registration there is something to know about, as it stands now."""
        with self._registry:
            # Read in here, so that none can have started after it.
            now = self.clock()
            ahead = sum(each.state is State.UNDER_WAY for each in self._all)
            shown = []
            for each in self._all:
                # Copies: the one under way is ended from another thread.
                shown.append(
                    replace(
                        each,
                        ahead=ahead,
                        under_way_for=now - each.started
                        if each.state is State.UNDER_WAY and each.started
                        else None,
                    )
                )
                if each.waiting:
                    ahead += 1
            return shown

    def seen(self, shown: Sequence[Registration]) -> None:
        """The user has been shown these. One that was shown as registered is gone after that,
        because the Source is in the table, unless it has a note left to read; one that has ended
        since then stays to be shown."""
        registered = {
            each.identifier
            for each in shown
            if each.state is State.REGISTERED and not each.to_be_cleared
        }
        with self._registry:
            self._all = [each for each in self._all if each.identifier not in registered]

    def clear(self, identifier: int) -> None:
        """The user has read how this one ended. Nothing is recorded. Only one that ended with
        something to read goes: there is no cancelling, and hiding one that is still explored
        would be a lie. One that is not there is left at that."""
        with self._registry:
            self._all = [
                each
                for each in self._all
                if each.identifier != identifier or not each.to_be_cleared
            ]

    def run_next(self) -> None:
        """Explore the Registration that has waited longest, to its end. Nothing to do when none
        is waiting."""
        with self._one_at_a_time:
            with self._registry:
                waiting = [each for each in self._all if each.state is State.WAITING]
                if not waiting:
                    return
                registration = waiting[0]
                registration.state, registration.started = State.UNDER_WAY, self.clock()
            try:
                register(
                    self.data_dir,
                    self.committer,
                    self.network,
                    self.model,
                    url=registration.url,
                    label=registration.label,
                    rule=registration.rule,
                    now=self.clock(),
                    sleep=self.sleep,
                )
            except Refused as refusal:
                self._end(registration, State.REFUSED, reason=str(refusal))
            except NotCommitted as not_committed:
                # Not a refusal: the Source was written, and trying again would find its name taken.
                self._end(
                    registration,
                    State.REGISTERED,
                    source=registration.name,
                    note=str(not_committed),
                )
            except Exception as error:
                # Deliberately everything: one left under way for ever would be a lie.
                print(f"jobs: a registration failed: {error!r}", file=sys.stderr)
                self._end(registration, State.REFUSED, reason=f"It failed unexpectedly: {error}.")
            else:
                self._end(registration, State.REGISTERED, source=registration.name)

    def _end(
        self,
        registration: Registration,
        state: State,
        *,
        reason: str | None = None,
        source: str | None = None,
        note: str | None = None,
    ) -> None:
        with self._registry:
            registration.state, registration.reason, registration.source = state, reason, source
            registration.note = note
