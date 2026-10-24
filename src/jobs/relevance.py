"""Relevance: whether a kept Posting is worth proposing, and the reason shown either way.

In this slice the judgement is rules only, and only the global ones: where the position is, and
keywords wanted and unwanted. A Source's own filter rules were applied during the walk and are not
applied again here. `Relevance` is what the proposal list asks, so a scorer can take the place of
the rules later without the list changing.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from jobs.postings import KeptPosting
from jobs.records import Malformed


@dataclass(frozen=True)
class Verdict:
    proposed: bool
    reason: str
    """Why, in words meant for the user, whichever way it went."""


class Relevance(Protocol):
    def judge(self, posting: KeptPosting) -> Verdict:
        """Whether to propose the Posting, and why."""
        ...


@dataclass(frozen=True)
class Rules:
    """The global rules. A keyword or a location matches anywhere in the text, whatever its case,
    so `ingenieur` also finds `Entwicklungsingenieur`."""

    locations: tuple[str, ...] = ()
    """Where a position may be. One that names no location is not removed for it."""
    include: tuple[str, ...] = ()
    """Keywords of which the role or the description has to mention at least one."""
    exclude: tuple[str, ...] = ()
    """Keywords the role and the description must not mention."""

    def judge(self, posting: KeptPosting) -> Verdict:
        text = f"{posting.role}\n{posting.description or ''}".casefold()
        if unwanted := _first_in(self.exclude, text):
            return Verdict(False, f"it mentions {unwanted}, which is excluded")
        reasons: list[str] = []
        if self.include:
            wanted = _first_in(self.include, text)
            if wanted is None:
                return Verdict(False, f"it mentions none of {', '.join(self.include)}")
            reasons.append(f"it mentions {wanted}")
        if self.locations:
            if posting.location is None:
                reasons.append("it names no location")
            elif place := _first_in(self.locations, posting.location.casefold()):
                reasons.append(f"its location matches {place}")
            else:
                return Verdict(
                    False,
                    f"its location, {posting.location}, is none of {', '.join(self.locations)}",
                )
        if not reasons:
            return Verdict(True, "no rules are set for it to fail, so every Posting is proposed")
        return Verdict(True, " and ".join(reasons))


def rules_from(record: dict[Any, Any]) -> Rules:
    """The rules as they are written in a record. Raises Malformed."""
    return Rules(
        locations=_words(record, "locations"),
        include=_words(record, "include"),
        exclude=_words(record, "exclude"),
    )


def _first_in(words: tuple[str, ...], text: str) -> str | None:
    return next((word for word in words if word.casefold() in text), None)


def _words(record: dict[Any, Any], name: str) -> tuple[str, ...]:
    value = record.get(name)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(word, str) for word in value):
        raise Malformed(f"{name} is not a list of words")
    return tuple(word.strip() for word in value if word.strip())
