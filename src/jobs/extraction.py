"""Extraction: from the bytes of a fetched page to Postings (ADR 0003). Performs no I/O.

A poll runs the one Strategy its Source config names. A page either yields Postings, possibly none,
or fails with a reason; it never yields a Posting with a required field missing.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from html import unescape
from html.parser import HTMLParser
from typing import Any, Literal, get_args
from urllib.parse import unquote, urldefrag, urljoin

import soupsieve
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString, PreformattedString


class Strategy(StrEnum):
    """How a page is read, in order of preference. The value is what is written in the Source
    config."""

    STRUCTURED_RECORD = "structured-record"
    SELECTORS = "selectors"


class Render(StrEnum):
    """How the bytes of a page are obtained. A concern of the fetch, never of how a page is read
    (decision 14). The value is what is written in the Source config."""

    STATIC = "static"
    BROWSER = "browser"


@dataclass(frozen=True)
class Hop:
    """One step of a Source config's hop chain: which of a page's links lead onward."""

    follow: str
    """A regular expression, searched for in each absolute URL the page links to."""
    render: Render = Render.STATIC
    """How the page these links are on is fetched."""


FilterField = Literal["company", "role", "description", "location", "identifier", "url"]
FILTER_FIELDS: tuple[FilterField, ...] = get_args(FilterField)

DEFAULT_INTERVAL = 1.0


@dataclass(frozen=True)
class FilterRule:
    """A Source-level rule: the Source keeps only Postings one of whose fields matches."""

    field: FilterField
    pattern: str
    """A regular expression, searched for in the field. A Posting without the field does not
    match."""
    rule: str | None = None
    """The rule in the words it was given in at registration, which the pattern was made from."""


@dataclass(frozen=True)
class Selectors:
    """Where a page holds each field of a Posting. Every value is a CSS selector;
    `selector@attribute` reads that attribute of the match instead of its text."""

    role: str
    description: str
    """Every match is read, in page order. Every other field takes the first."""
    company: str | None = None
    location: str | None = None
    identifier: str | None = None
    posted: str | None = None
    deadline: str | None = None
    item: str | None = None
    """What holds one Posting, where a page carries several: each match is one, and the other
    selectors are matched within it. Without it a page is one Posting."""


SELECTOR_FIELDS = tuple(field.name for field in fields(Selectors) if field.name != "item")
"""The fields of a Posting that a selector reads."""


@dataclass(frozen=True)
class SourceConfig:
    """The part of a Source config that reaching and reading its Postings takes. Extraction reads
    the strategy, the selectors and the literal company; the walk in fetching drives the rest."""

    strategy: Strategy
    selectors: Selectors | None = None
    """How the selectors Strategy finds each field. The structured-record Strategy reads none."""
    company: str | None = None
    """The company in plain words, for pages that never state it. `selectors.company` wins."""
    hops: tuple[Hop, ...] = ()
    """From the registered URL to the posting pages. With none, the registered URL is itself the
    page that is read."""
    render: Render = Render.STATIC
    """How the pages at the end of the hop chain are fetched: the posting pages, or the page a
    chain that could not be derived any further stops at."""
    filters: tuple[FilterRule, ...] = ()
    interval: float = DEFAULT_INTERVAL
    """Seconds a walk leaves between one request and the next, to be polite to the host."""

    @property
    def reads_posting_pages(self) -> bool:
        """Whether each page that is read is one Posting's own: reached through the hop chain,
        and not read item by item as a page that lists several is (decision 40)."""
        return bool(self.hops) and not (self.selectors and self.selectors.item)


@dataclass(frozen=True)
class Page:
    """A fetched page: the final URL it was served from, and its bytes."""

    url: str
    body: bytes


@dataclass(frozen=True)
class Remuneration:
    """What a Posting pays, as published: a single figure has `low` equal to `high`."""

    currency: str | None
    low: Decimal
    high: Decimal
    per: str | None


@dataclass(frozen=True)
class Posting:
    company: str
    role: str
    description: str
    url: str
    location: str | None
    identifier: str | None
    posted: date | None
    deadline: date | None
    remuneration: Remuneration | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class Extracted:
    """What a page yielded. No Postings is a result, not a failure."""

    postings: list[Posting]


@dataclass(frozen=True)
class Failed:
    """A page that could not be read, and why. It carries no Postings, not even the good ones."""

    reason: str


@dataclass(frozen=True)
class Derived:
    """The first Strategy, in order of preference, that read Postings off a page."""

    strategy: Strategy
    postings: list[Posting]


@dataclass(frozen=True)
class Underivable:
    """No Strategy read any Posting off a page. Holds why, for each one tried."""

    attempts: dict[Strategy, str]


class Malformed(Exception):
    pass


def extract(page: Page, config: SourceConfig) -> Extracted | Failed:
    try:
        return Extracted(postings=_READERS[config.strategy](page, config))
    except Malformed as malformed:
        return Failed(reason=str(malformed))


def derive_strategy(page: Page) -> Derived | Underivable:
    """Find the Strategy to freeze into a Source config. For registration only: a poll never
    tries more than the one its config names."""
    attempts = {}
    for strategy in Strategy:
        result = extract(page, SourceConfig(strategy=strategy))
        if isinstance(result, Failed):
            attempts[strategy] = result.reason
        elif not result.postings:
            attempts[strategy] = "the page yielded no Postings"
        else:
            return Derived(strategy=strategy, postings=result.postings)
    return Underivable(attempts=attempts)


def discover_links(page: Page, hop: Hop) -> list[str]:
    """The URLs of the next hop, in page order, each once. Nothing is fetched."""
    markup = _parsed(page)
    follow = re.compile(hop.follow)
    urls = (_absolute(link, urljoin(page.url, markup.base)) for link in markup.links)
    return list(dict.fromkeys(url for url in urls if url and follow.search(url)))


def links(page: Page) -> list[str]:
    """Every URL a page links to, in page order, each once."""
    return discover_links(page, Hop(follow=""))


def visible_text(page: Page) -> str:
    """What a reader of the page would see, without its markup, scripts or styles."""
    return " ".join(" ".join(_parsed(page).text).split())


def contact_addresses(page: Page) -> list[str]:
    """The email addresses a page links to, in page order, each once, with obfuscation undone."""
    addresses = (unquote(link.partition("?")[0]).strip() for link in _parsed(page).mail_links)
    return list(dict.fromkeys(address for address in addresses if "@" in address))


def _absolute(link: str, base: str) -> str | None:
    """The page a link leads to. A fragment is a place within that page, so it is dropped."""
    try:
        return urldefrag(urljoin(base, link.strip())).url
    except ValueError:
        return None


def selector_problem(selector: str, *, element: bool = False) -> str | None:
    """Why a selector cannot be read, or None when it can. `element` says that it is to select
    an element, as `item` does, so that there is no attribute to read of it."""
    css, attribute = _css_and_attribute(selector)
    if element and attribute:
        return f"it selects an element that holds a Posting, which @{attribute} is not"
    try:
        soupsieve.compile(css)
    except soupsieve.SelectorSyntaxError as error:
        return str(error).strip().partition("\n")[0]
    return None


# What a model needs of a page to say where each field is, and no more than that.
_NOT_SELECTED_FROM = ("script", "style", "svg", "template", "noscript", "link", "meta")
_SELECTED_BY = frozenset({"id", "class", "itemprop", "datetime"})
_TEXT_KEPT = 80
_MARKUP_KEPT = 20_000


def reduced_markup(page: Page, cap: int = _MARKUP_KEPT) -> str:
    """A page's markup reduced for a model that is to name selectors: scripts, styles, SVG,
    comments and templates gone, long text shortened, tags kept with their ids, classes and the
    few other attributes a selector is made from, and the whole capped in length."""
    markup = _soup(page)
    for unseen in markup(_NOT_SELECTED_FROM):
        unseen.decompose()
    for text in markup.find_all(string=True):
        words = " ".join(text.split())
        if isinstance(text, PreformattedString) or not words:
            # Comments, the doctype and the like, and the whitespace between tags.
            text.extract()
        elif len(words) > _TEXT_KEPT:
            text.replace_with(NavigableString(f"{words[:_TEXT_KEPT].rstrip()}…"))
        else:
            text.replace_with(NavigableString(words))
    for tag in markup.find_all(True):
        tag.attrs = {name: value for name, value in tag.attrs.items() if name in _SELECTED_BY}
    return str(markup)[:cap]


def _soup(page: Page) -> BeautifulSoup:
    # The standard library's parser: lenient, and nothing to install beside it.
    return BeautifulSoup(page.body.decode("utf-8", errors="replace"), "html.parser")


def _read_structured_records(page: Page, config: SourceConfig) -> list[Posting]:
    return [_posting(record, page) for record in _job_posting_records(page)]


def _read_by_selectors(page: Page, config: SourceConfig) -> list[Posting]:
    selectors = config.selectors
    if selectors is None:
        raise Malformed("there are no selectors to read the page with")
    markup = _soup(page)
    if selectors.item is None:
        return [_read_within(markup, "the page", page, config, selectors)]
    # No match is an employer advertising nothing, which is a result.
    items = markup.select(selectors.item)
    # An item within another is part of that one's Posting, not a second one.
    outermost = [item for item in items if not any(item in other.descendants for other in items)]
    return [
        _read_within(item, f"item {number} of {selectors.item!r}", page, config, selectors)
        for number, item in enumerate(outermost, start=1)
    ]


def _read_within(
    markup: Tag, where: str, page: Page, config: SourceConfig, selectors: Selectors
) -> Posting:
    """The one Posting that the whole page, or one item of it, holds."""
    picked: dict[str, Any] = {}
    read: dict[str, str] = {}
    for name in SELECTOR_FIELDS:
        selector = getattr(selectors, name)
        if selector is None:
            continue
        matches = _selected(markup, selector)
        if not matches:
            continue
        if name == "description":
            picked[name] = [raw for raw, _ in matches]
        else:
            matches = matches[:1]
            picked[name] = matches[0][0]
        read[name] = " ".join(" ".join(text for _, text in matches).split())
    if selectors.company is None and config.company:
        read["company"] = config.company.strip()
    for name in ("role", "description", "company"):
        if not read.get(name):
            selector = getattr(selectors, name)
            found_by = f"the selector {selector!r}" if selector else "any selector"
            raise Malformed(f"{where} has no {name}: {found_by} found none")
    return Posting(
        company=read["company"],
        role=read["role"],
        description=read["description"],
        url=page.url,
        location=read.get("location") or None,
        identifier=read.get("identifier") or None,
        posted=_date_within(read.get("posted")),
        deadline=_date_within(read.get("deadline")),
        remuneration=None,
        raw=picked,
    )


_ATTRIBUTE = re.compile(r"[A-Za-z_:][-\w:.]*")


def _css_and_attribute(selector: str) -> tuple[str, str | None]:
    """`time.closes@datetime` is the selector `time.closes` and the attribute `datetime`."""
    css, at, attribute = selector.rpartition("@")
    if at and _ATTRIBUTE.fullmatch(attribute.strip()):
        return css.strip(), attribute.strip()
    return selector.strip(), None


def _selected(markup: Tag, selector: str) -> list[tuple[str, str]]:
    """Each match, in page order: as the page has it, and as the text that is read from it."""
    css, attribute = _css_and_attribute(selector)
    selected = []
    matches = markup.select(css)
    for match in matches:
        if attribute is None and any(match in other.descendants for other in matches):
            # Within a match already read, so its text is there once.
            continue
        if attribute is None:
            selected.append((str(match), match.get_text(" ")))
        elif (value := _attribute(match, attribute)) is not None:
            selected.append((value, value))
    return selected


def _attribute(match: Tag, attribute: str) -> str | None:
    value = match.get(attribute)
    # The parser splits the few attributes that hold a list, `class` above all.
    return " ".join(value) if isinstance(value, list) else value


_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_GERMAN_DATE = re.compile(r"(?<!\d)(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})(?!\d)")


def _date_within(text: str | None) -> date | None:
    """A date written as ISO or as `DD.MM.YYYY`, anywhere in the text. A page is written by
    people, so anything else leaves the field empty rather than failing the page."""
    if not text:
        return None
    written = [(iso[1], iso[2], iso[3]) for iso in _ISO_DATE.finditer(text)]
    written += [(german[3], german[2], german[1]) for german in _GERMAN_DATE.finditer(text)]
    for year, month, day in written:
        try:
            return date(int(year), int(month), int(day))
        except ValueError:
            continue
    return None


_READERS: dict[Strategy, Callable[[Page, SourceConfig], list[Posting]]] = {
    Strategy.STRUCTURED_RECORD: _read_structured_records,
    Strategy.SELECTORS: _read_by_selectors,
}


def _posting(record: dict[str, Any], page: Page) -> Posting:
    organization = record.get("hiringOrganization")
    return Posting(
        company=_text(
            organization if isinstance(organization, dict) else {"name": organization},
            "name",
            "company",
        ),
        role=_text(record, "title", "role"),
        description=_plain_text(_text(record, "description", "description")),
        url=page.url,
        location=_location(record.get("jobLocation")),
        identifier=_identifier(record.get("identifier")),
        posted=_date(record, "datePosted"),
        deadline=_date(record, "validThrough"),
        remuneration=_remuneration(record.get("baseSalary")),
        raw=record,
    )


def _text(record: dict[str, Any], field: str, meaning: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise Malformed(f"the structured record has no {field}, so the Posting has no {meaning}")
    return value.strip()


def _location(value: Any) -> str | None:
    localities = []
    for place in value if isinstance(value, list) else [value]:
        address = place.get("address") if isinstance(place, dict) else None
        locality = address.get("addressLocality") if isinstance(address, dict) else address
        if isinstance(locality, str) and locality.strip():
            localities.append(locality.strip())
    return ", ".join(localities) or None


def _identifier(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None
    return str(value).strip() or None


def _remuneration(value: Any) -> Remuneration | None:
    if not isinstance(value, dict):
        return None
    amount = value.get("value")
    figures = amount if isinstance(amount, dict) else {"value": amount}
    single = _decimal(figures.get("value"))
    low, high = _decimal(figures.get("minValue")), _decimal(figures.get("maxValue"))
    low = next((figure for figure in (low, single, high) if figure is not None), None)
    high = next((figure for figure in (high, single, low) if figure is not None), None)
    if low is None or high is None:
        return None
    currency, unit = value.get("currency"), figures.get("unitText")
    return Remuneration(
        currency=currency if isinstance(currency, str) else None,
        low=low,
        high=high,
        per=unit if isinstance(unit, str) else None,
    )


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    try:
        figure = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return figure if figure.is_finite() else None


def _date(record: dict[str, Any], field: str) -> date | None:
    """The calendar date as the site wrote it; a time or an offset beside it is not applied."""
    value = record.get(field)
    if value is None or value == "":
        return None
    try:
        return datetime.fromisoformat(value).date()
    except TypeError, ValueError:
        raise Malformed(f"the structured record's {field} is {value!r}, not a date") from None


def _plain_text(markup: str) -> str:
    """Records carry their description as markup, and some escape that markup once more."""
    return " ".join(unescape(_without_tags(unescape(_without_tags(markup)))).split())


def _without_tags(markup: str) -> str:
    return re.sub(r"<[^>]+>", " ", markup)


# Rows made clickable by a script keep their target in a data attribute (verified: TUHH). `href`
# counts on anchors only, where it is a link to a page and not to a stylesheet or an icon.
_LINK_ATTRIBUTES = frozenset({"href", "data-url", "data-href"})


# What these hold is never shown to a reader of the page.
_UNSEEN_TAGS = frozenset({"script", "style", "noscript", "template"})


class _Markup(HTMLParser):
    """One lenient pass over a page, keeping the few things extraction reads."""

    def __init__(self) -> None:
        super().__init__()
        self.base = ""
        self.links: list[str] = []
        self.mail_links: list[str] = []
        self.record_scripts: list[str] = []
        self.text: list[str] = []
        self.opened = False
        self.closed = False
        self._record_script: list[str] | None = None
        self._unseen = 0

    @property
    def truncated(self) -> bool:
        return self._record_script is not None or (self.opened and not self.closed)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in _UNSEEN_TAGS:
            self._unseen += 1
        if tag == "html":
            self.opened = True
        elif tag == "base" and not self.base:
            self.base = attributes.get("href") or ""
        elif tag == "script" and _is_record_script(attributes.get("type")):
            self._record_script = []
        elif tag == "a":
            self._mail_link(attributes)
        names = _LINK_ATTRIBUTES if tag == "a" else _LINK_ATTRIBUTES - {"href"}
        self.links.extend(value for name, value in attrs if name in names and value)

    def handle_data(self, data: str) -> None:
        if self._record_script is not None:
            self._record_script.append(data)
        elif not self._unseen:
            self.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in _UNSEEN_TAGS:
            self._unseen = max(0, self._unseen - 1)
        if tag == "html":
            self.closed = True
        elif tag == "script" and self._record_script is not None:
            self.record_scripts.append("".join(self._record_script))
            self._record_script = None

    def _mail_link(self, attributes: dict[str, str | None]) -> None:
        link = attributes.get("href") or ""
        token, vector = attributes.get("data-mailto-token"), attributes.get("data-mailto-vector")
        if token and vector and vector.lstrip("-").isdigit():
            # The vector is the rotation that hid the link, so undoing it is the opposite one.
            link = _typo3_rotated(token, -int(vector))
        if link.lower().startswith("mailto:"):
            self.mail_links.append(link[len("mailto:") :])


def _parsed(page: Page) -> _Markup:
    markup = _Markup()
    markup.feed(page.body.decode("utf-8", errors="replace"))
    markup.close()
    return markup


def _is_record_script(kind: str | None) -> bool:
    return (kind or "").partition(";")[0].strip().lower() == "application/ld+json"


# TYPO3 hides a mail link by rotating each character within its own range (verified: TUHH).
_TYPO3_RANGES = ((0x2B, 0x3A), (0x40, 0x5A), (0x61, 0x7A))


def _typo3_rotated(token: str, offset: int) -> str:
    def rotate(character: str) -> str:
        for start, end in _TYPO3_RANGES:
            if start <= ord(character) <= end:
                return chr(start + (ord(character) - start + offset) % (end - start + 1))
        return character

    return "".join(rotate(character) for character in token)


def _job_posting_records(page: Page) -> list[dict[str, Any]]:
    markup = _parsed(page)
    if markup.truncated:
        raise Malformed("the page is truncated: it ends before its markup does")
    records: list[dict[str, Any]] = []
    for script in markup.record_scripts:
        try:
            # Sites publish records with raw newlines inside strings; strict JSON forbids them.
            document = json.loads(script, strict=False)
        except json.JSONDecodeError as error:
            raise Malformed(f"a structured record is not valid JSON: {error}") from None
        records.extend(node for node in _nodes(document) if _is_job_posting(node))
    return records


def _nodes(document: Any) -> list[dict[str, Any]]:
    """Every object in a record, however deep: sites nest a JobPosting under `@graph` and others."""
    if isinstance(document, list):
        return [node for item in document for node in _nodes(item)]
    if isinstance(document, dict):
        return [document, *(node for value in document.values() for node in _nodes(value))]
    return []


def _is_job_posting(node: dict[str, Any]) -> bool:
    kind = node.get("@type")
    return "JobPosting" in (kind if isinstance(kind, list) else [kind])
