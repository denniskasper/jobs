"""The Timeline: every Application sent, on its Sent date, and the sentences that say how many
roles are still `todo` and how the sent ones stand.

Only what the dashboard draws is worked out here, from the Applications as they were read; the
page decides how a dot looks.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from jobs.applications import Application, Status

_WORDS = "zero one two three four five six seven eight nine ten eleven twelve".split()

# Within a week, the dot that matters most stands at the bottom, nearest the axis.
_ORDER = {Status.INTERVIEW: 0, Status.WAITING: 1, Status.CLOSED: 2, Status.TODO: 3}

# Up to this many Applications in interview are named; more are counted.
_NAMED = 3


@dataclass(frozen=True)
class Dot:
    application: Application
    at: float
    """How far along the axis its week stands, in percent."""
    row: int
    """How many dots of the same week stand below it."""


@dataclass(frozen=True)
class Label:
    """The names of the Applications in interview in one week, above that week's dots."""

    text: str
    at: float
    row: int


@dataclass(frozen=True)
class Month:
    name: str
    at: float


@dataclass(frozen=True)
class Timeline:
    headline: str
    summary: str
    dots: list[Dot]
    labels: list[Label]
    months: list[Month]
    tallest: int
    """The most dots on any one day."""


def timeline(applications: Sequence[Application], today: date) -> Timeline:
    todo = sum(application.status is Status.TODO for application in applications)
    sent = [application for application in applications if application.sent]
    dated = sorted(
        (application for application in sent if application.sent_on is not None),
        key=lambda application: (application.sent_on, application.company),
    )
    dots: list[Dot] = []
    labels: list[Label] = []
    months: list[Month] = []
    if dated:
        first = dated[0].sent_on
        assert first is not None
        start = first.replace(day=1)
        span = max((max(today, dated[-1].sent_on or today) - start).days, 1)

        def along(day: date) -> float:
            return round(min(max((day - start).days / span, 0), 1) * 100, 2)

        # A day is a few pixels wide, narrower than a dot, so the dots of one week stand in one
        # stack, in the middle of that week.
        weeks: dict[date, list[Application]] = {}
        for application in dated:
            on = application.sent_on
            assert on is not None
            weeks.setdefault(on - timedelta(days=on.weekday()), []).append(application)
        for monday, sent_that_week in weeks.items():
            at = along(monday + timedelta(days=3))
            ordered = sorted(sent_that_week, key=lambda application: _ORDER[application.status])
            dots += [Dot(application, at, row) for row, application in enumerate(ordered)]
            named = [a.company for a in ordered if a.status is Status.INTERVIEW]
            if named:
                labels.append(Label(_listed(list(dict.fromkeys(named))), at, len(ordered)))
        month = start
        while month <= today:
            months.append(Month(month.strftime("%b"), along(month)))
            month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    return Timeline(
        headline=_headline(todo),
        summary=_summary(sent, dated, today),
        dots=dots,
        labels=labels,
        months=months,
        tallest=max((dot.row + 1 for dot in dots), default=0),
    )


def _headline(todo: int) -> str:
    if todo == 0:
        return "No role still needs an application."
    if todo == 1:
        return "One role still needs an application."
    return f"{_number(todo).capitalize()} roles still need an application."


def _summary(sent: list[Application], dated: list[Application], today: date) -> str:
    if not sent:
        return "Nothing has been sent yet."
    undated = len(sent) - len(dated)
    if dated:
        first = dated[0].sent_on
        assert first is not None
        since = first.strftime("%B") if first.year == today.year else first.strftime("%B %Y")
        how_many = _counted(len(dated), "application", "applications").capitalize()
        sentences = [
            f"{how_many} sent since {since}"
            + (f", and {_number(undated)} more without a date." if undated else ".")
        ]
    else:
        how_many = _counted(undated, "application", "applications").capitalize()
        sentences = [f"{how_many} sent, on days nobody recorded."]
    sentences += _standing(sent)
    return " ".join(sentences)


def _standing(sent: list[Application]) -> list[str]:
    """How the sent Applications stand now, in a sentence or two."""
    if len(sent) == 1:
        return [
            {
                Status.WAITING: "It is still waiting for a reply.",
                Status.INTERVIEW: "It reached interview.",
            }.get(sent[0].status, "It is closed.")
        ]
    waiting = sum(application.status is Status.WAITING for application in sent)
    interview = list(
        dict.fromkeys(
            application.company for application in sent if application.status is Status.INTERVIEW
        )
    )
    in_interview = sum(application.status is Status.INTERVIEW for application in sent)
    closed = len(sent) - waiting - in_interview
    clauses = []
    if waiting == len(sent):
        clauses.append(f"All {_number(waiting)} are still waiting for a reply")
    elif waiting:
        verb = "is" if waiting == 1 else "are"
        clauses.append(f"{_number(waiting).capitalize()} {verb} still waiting for a reply")
    if in_interview:
        who = (
            _listed(interview)
            if len(interview) <= _NAMED and len(interview) == in_interview
            else _number(in_interview)
        )
        clauses.append(f"{who} reached interview")
    sentences = []
    if clauses:
        sentence = ", and ".join(clauses)
        sentences.append(sentence[0].upper() + sentence[1:] + ".")
    if closed == len(sent):
        sentences.append(f"All {_number(closed)} are closed.")
    elif closed:
        sentences.append(f"The other {_number(closed)} {'is' if closed == 1 else 'are'} closed.")
    return sentences


def _listed(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _counted(count: int, one: str, many: str) -> str:
    return f"{_number(count)} {one if count == 1 else many}"


def _number(count: int) -> str:
    return _WORDS[count] if count < len(_WORDS) else str(count)
