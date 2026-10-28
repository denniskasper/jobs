"""What the page that creates an Application proposes from what a browser handed it: a company and
a role from the page's title, and the link from a text that begins or ends with one.

Plain code, no model (decision 29). What it proposes is shown in fields to be corrected, and nothing
else depends on it.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from jobs import records

# What titles are put together with, between spaces so that a hyphenated word stays one.
_SEPARATORS = re.compile(r"\s+(?:[|\-–—·•:]|::)\s+")

_AT = re.compile(r"^(?P<role>.+?)\s+(?:at|bei)\s+(?P<company>.+)$", re.IGNORECASE)
_HIRING = re.compile(r"^(?P<company>.+?)\s+is\s+hiring:?\s+(?:an?\s+)?(?P<role>.+)$", re.IGNORECASE)

# A link as a phone hands it over in the text, before or after everything else.
_LEADING_LINK = re.compile(r"^\s*(https?://\S+)(?:\s+|$)")
_TRAILING_LINK = re.compile(r"(?:^|\s+)(https?://\S+)\s*$")

# Parts of a host that name no site.
_NOT_A_NAME = {"www", "jobs", "careers", "karriere", "de", "com", "org", "net", "io", "example"}


@dataclass(frozen=True)
class Guess:
    company: str
    role: str


def from_title(title: str, link: str = "") -> Guess:
    """The company and role a reader takes a page's title to name. What fits no shape is the
    role, with the company left empty, so nothing but the site's own name is lost."""
    title = " ".join(title.split())
    separators = list(_SEPARATORS.finditer(title))
    if separators and _names_site(title[separators[-1].end() :], link):
        title = title[: separators[-1].start()]
    for part in _SEPARATORS.split(title):
        for shape in (_AT, _HIRING):
            found = shape.match(part)
            if found:
                return Guess(company=found["company"].strip(" !."), role=found["role"].strip(" !."))
    return Guess(company="", role=title)


def _names_site(part: str, link: str) -> bool:
    """Whether a part of a title is the name of the site the link is on."""
    name = records.slug(part).replace("-", "")
    host = (urlsplit(link).hostname or "").lower()
    labels = [
        label.replace("-", "")
        for label in host.split(".")
        if len(label) >= 3 and label not in _NOT_A_NAME
    ]
    return bool(name) and any(label in name or name in label for label in labels)


def link_in(text: str) -> tuple[str, str]:
    """A link the text begins or ends with, and the text without it. With none, an empty link
    and the text as it was."""
    for at in (_LEADING_LINK, _TRAILING_LINK):
        found = at.search(text)
        if found:
            return found[1], (text[: found.start()] + text[found.end() :]).strip()
    return "", text


def filled_in(entered: dict[str, str], title: str = "") -> dict[str, str]:
    """The form as it is to be shown or created from: the company and role proposed from the
    title when neither was given, and the link taken from the text when none was."""
    filled = dict(entered)
    if (
        title.strip()
        and not filled.get("company", "").strip()
        and not filled.get("role", "").strip()
    ):
        guess = from_title(title, filled.get("link", ""))
        filled["company"], filled["role"] = guess.company, guess.role
    if not filled.get("link", "").strip():
        link, text = link_in(filled.get("text", ""))
        if link:
            filled["link"], filled["text"] = link, text
    return filled
