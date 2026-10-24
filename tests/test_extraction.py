"""Seam B: extraction as a pure function over the corpus of saved pages.

Every page under `fixtures/pages/` is the bytes a real site served, except the deliberately
malformed ones and the invented ones; `fixtures/pages/README.md` says where each came from.
Expected values are read off the page as a human sees it, never recomputed the way the code
computes them.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from jobs.extraction import (
    Derived,
    Extracted,
    Failed,
    Hop,
    Page,
    Remuneration,
    Selectors,
    SourceConfig,
    Strategy,
    Underivable,
    contact_addresses,
    derive_strategy,
    discover_links,
    extract,
    reduced_markup,
)

PAGES = Path(__file__).parent / "fixtures" / "pages"

TUHH_POSTING_URL = (
    "https://stellenportal.tuhh.de/jobposting/038dac54bf802d6b19e9ea4c68e61396d65cf78e"
)

TUHH_LISTING_URL = (
    "https://www.tuhh.de/tuhh/tu-hamburg/arbeiten-an-der-tu-hamburg"
    "/stellenausschreibungen/wissenschaftliche-mitarbeit"
)

JOBSACUK_POSTING_URL = (
    "https://www.jobs.ac.uk/job/DSL052/lecturer-or-senior-lecturer-in-thermohydraulics"
)

STRUCTURED_RECORD = SourceConfig(strategy=Strategy.STRUCTURED_RECORD)


def saved_page(name: str, url: str) -> Page:
    return Page(url=url, body=(PAGES / name).read_bytes())


def test_a_posting_page_with_a_structured_record_yields_a_posting() -> None:
    page = saved_page("tuhh-posting-digital-economics.html", TUHH_POSTING_URL)

    result = extract(page, STRUCTURED_RECORD)

    assert isinstance(result, Extracted)
    [posting] = result.postings
    assert posting.company == "Technische Universität Hamburg"
    assert posting.role == "Research Associate (m/f/d) at the Institute of Digital Economics"
    assert posting.location == "Hamburg"
    assert posting.identifier == "038dac54bf802d6b19e9ea4c68e61396d65cf78e"
    assert posting.posted == date(2026, 9, 7)
    assert posting.deadline == date(2026, 9, 23)
    assert posting.url == TUHH_POSTING_URL
    assert "We study decision making, strategic interaction" in posting.description
    assert "<div" not in posting.description


def test_remuneration_is_captured_when_the_page_publishes_it() -> None:
    page = saved_page("jobsacuk-posting-thermohydraulics.html", JOBSACUK_POSTING_URL)

    result = extract(page, STRUCTURED_RECORD)

    assert isinstance(result, Extracted)
    [posting] = result.postings
    assert posting.company == "Loughborough University"
    assert posting.role == "Lecturer or Senior Lecturer in Thermohydraulics"
    assert posting.location == "Loughborough"
    assert posting.deadline == date(2026, 9, 30)
    assert posting.remuneration == Remuneration(
        currency="GBP", low=Decimal("47389.00"), high=Decimal("67468.00"), per="YEAR"
    )


def test_a_page_that_publishes_no_remuneration_yields_a_posting_without_one() -> None:
    page = saved_page("tuhh-posting-digital-economics.html", TUHH_POSTING_URL)

    result = extract(page, STRUCTURED_RECORD)

    assert isinstance(result, Extracted)
    assert result.postings[0].remuneration is None


def test_a_page_that_publishes_no_identifier_yields_a_posting_without_one() -> None:
    page = saved_page("jobsacuk-posting-thermohydraulics.html", JOBSACUK_POSTING_URL)

    result = extract(page, STRUCTURED_RECORD)

    assert isinstance(result, Extracted)
    assert result.postings[0].identifier is None


def test_the_raw_record_is_kept_beside_the_parsed_posting() -> None:
    page = saved_page("tuhh-posting-digital-economics.html", TUHH_POSTING_URL)

    result = extract(page, STRUCTURED_RECORD)

    assert isinstance(result, Extracted)
    raw = result.postings[0].raw
    assert raw["validThrough"] == "2026-09-23T21:59:00+00:00"
    assert raw["employmentType"] == ["FULL_TIME"]
    assert raw["jobLocation"]["address"]["postalCode"] == "21073"


def test_a_structured_record_missing_a_required_field_is_rejected_with_a_reason() -> None:
    page = saved_page("malformed-record-without-role.html", TUHH_POSTING_URL)

    result = extract(page, STRUCTURED_RECORD)

    assert isinstance(result, Failed)
    assert "title" in result.reason


def test_a_truncated_page_yields_a_reason_and_no_postings() -> None:
    page = saved_page("malformed-truncated-posting.html", TUHH_POSTING_URL)

    result = extract(page, STRUCTURED_RECORD)

    assert isinstance(result, Failed)
    assert "truncated" in result.reason


def test_a_page_truncated_before_its_record_is_a_failure_not_an_empty_result() -> None:
    page = saved_page("malformed-truncated-before-record.html", TUHH_POSTING_URL)

    result = extract(page, STRUCTURED_RECORD)

    assert isinstance(result, Failed)
    assert "truncated" in result.reason


def test_an_empty_page_yields_an_empty_result_not_a_failure() -> None:
    result = extract(saved_page("empty.html", TUHH_POSTING_URL), STRUCTURED_RECORD)

    assert result == Extracted(postings=[])


def test_a_signpost_page_reports_the_next_hop() -> None:
    page = saved_page(
        "tuhh-fds-job-vacancies.html", "https://www.tuhh.de/fds/research/job-vacancies"
    )

    links = discover_links(page, Hop(follow=r"/working-at-tu-hamburg/job-opportunities$"))

    assert links == [
        "https://www.tuhh.de/tuhh/en/tu-hamburg/working-at-tu-hamburg/job-opportunities"
    ]


def test_a_listing_page_reports_the_posting_links_in_its_static_markup() -> None:
    page = saved_page("tuhh-listing-research-staff.html", TUHH_LISTING_URL)

    links = discover_links(page, Hop(follow=r"^https://stellenportal\.tuhh\.de/jobposting/"))

    assert links == [
        "https://stellenportal.tuhh.de/jobposting/038dac54bf802d6b19e9ea4c68e61396d65cf78e",
        "https://stellenportal.tuhh.de/jobposting/472929048bec0c54a27dfe0ec4269a0363b7ff94",
        "https://stellenportal.tuhh.de/jobposting/66a69a312b3f32c2a1d90d52fdc2a160b8c37801",
        "https://stellenportal.tuhh.de/jobposting/999b0825a64512d1c1b7efa1ba863badb7ade079",
        "https://stellenportal.tuhh.de/jobposting/1729e5f11fd9bc5fd97ba72b2308551af7231560",
    ]


def test_a_listing_rendered_by_javascript_reports_no_links() -> None:
    page = saved_page(
        "airbus-workday-listing.html", "https://ag.wd3.myworkdayjobs.com/en-US/Airbus"
    )

    assert discover_links(page, Hop(follow=r"/job/")) == []


def test_a_relative_link_is_reported_as_an_absolute_url() -> None:
    page = saved_page("tuhh-listing-research-staff.html", TUHH_LISTING_URL)

    links = discover_links(page, Hop(follow=r"/stellenausschreibungen/professuren$"))

    assert links == [
        "https://www.tuhh.de/tuhh/tu-hamburg/arbeiten-an-der-tu-hamburg"
        "/stellenausschreibungen/professuren"
    ]


def test_extraction_runs_only_the_strategy_the_config_names() -> None:
    page = saved_page("tuhh-posting-digital-economics.html", TUHH_POSTING_URL)

    result = extract(page, SourceConfig(strategy=Strategy.SELECTORS))

    assert isinstance(result, Failed)
    assert "selectors" in result.reason


INVENTED_POSTING_URL = "https://portal.example/jobs/117"

IN_SECTIONS = Selectors(
    role="h1.job-title",
    description=".job-section",
    company=".employer",
    location=".job-meta .place",
    identifier=".reference",
    posted="time.published@datetime",
    deadline=".closes",
)


def by_selectors(selectors: Selectors, company: str | None = None) -> SourceConfig:
    return SourceConfig(strategy=Strategy.SELECTORS, selectors=selectors, company=company)


def test_a_page_is_read_into_one_posting_each_field_from_its_selector() -> None:
    page = saved_page("invented-posting-in-sections.html", INVENTED_POSTING_URL)

    result = extract(page, by_selectors(IN_SECTIONS))

    assert isinstance(result, Extracted)
    [posting] = result.postings
    assert posting.role == "Doctoral researcher in ship hydrodynamics"
    assert posting.company == "Hafenstadt Institute of Technology"
    assert posting.location == "Hafenstadt"
    assert posting.identifier == "HIT-2026-117"
    assert posting.posted == date(2026, 9, 1)
    assert posting.deadline == date(2026, 10, 31)
    assert posting.url == INVENTED_POSTING_URL
    assert posting.remuneration is None


def test_a_description_split_over_several_sections_is_read_as_one_in_page_order() -> None:
    page = saved_page("invented-posting-in-sections.html", INVENTED_POSTING_URL)

    result = extract(page, by_selectors(IN_SECTIONS))

    assert isinstance(result, Extracted)
    description = result.postings[0].description
    assert description.startswith("Your tasks You measure the wake of model hulls")
    assert "towing tank & compare" in description
    assert description.index("Your tasks") < description.index("Your profile")
    assert description.index("Your profile") < description.index("What we offer")
    assert "Share this position" not in description
    assert "trackSection" not in description
    assert "<" not in description


def test_a_selector_naming_an_attribute_reads_it_and_not_the_text() -> None:
    page = saved_page("invented-posting-in-sections.html", INVENTED_POSTING_URL)

    result = extract(page, by_selectors(IN_SECTIONS))

    assert isinstance(result, Extracted)
    assert result.postings[0].posted == date(2026, 9, 1)
    assert result.postings[0].raw["posted"] == "2026-09-01T08:00:00+02:00"


def test_a_deadline_written_in_words_is_left_empty_and_the_posting_kept() -> None:
    page = saved_page("invented-posting-sparse.html", "https://employer.example/careers")
    selectors = Selectors(role="h2.opening", description=".body", deadline=".closes")

    result = extract(page, by_selectors(selectors, company="Example GmbH"))

    assert isinstance(result, Extracted)
    [posting] = result.postings
    assert posting.role == "Laboratory engineer"
    assert posting.deadline is None
    assert posting.raw["deadline"] == '<span class="closes">as soon as possible</span>'


def test_a_field_the_page_does_not_have_is_left_empty() -> None:
    page = saved_page("invented-posting-sparse.html", "https://employer.example/careers")
    selectors = Selectors(
        role="h2.opening", description=".body", location=".place", identifier=".reference"
    )

    result = extract(page, by_selectors(selectors, company="Example GmbH"))

    assert isinstance(result, Extracted)
    [posting] = result.postings
    assert (posting.location, posting.identifier, posting.posted) == (None, None, None)


def test_the_literal_company_is_used_when_no_selector_reads_one() -> None:
    page = saved_page("invented-posting-sparse.html", "https://employer.example/careers")
    selectors = Selectors(role="h2.opening", description=".body")

    result = extract(page, by_selectors(selectors, company="Example GmbH"))

    assert isinstance(result, Extracted)
    assert result.postings[0].company == "Example GmbH"


def test_the_company_selector_wins_over_the_literal() -> None:
    page = saved_page("invented-posting-in-sections.html", INVENTED_POSTING_URL)

    result = extract(page, by_selectors(IN_SECTIONS, company="Hafenstadt Portal"))

    assert isinstance(result, Extracted)
    assert result.postings[0].company == "Hafenstadt Institute of Technology"


def test_a_page_with_no_match_for_the_role_fails_naming_the_field_and_the_selector() -> None:
    page = saved_page("invented-posting-sparse.html", "https://employer.example/careers")

    result = extract(page, by_selectors(IN_SECTIONS))

    assert isinstance(result, Failed)
    assert "role" in result.reason
    assert "h1.job-title" in result.reason


def test_a_company_selector_that_matches_nothing_fails_rather_than_guessing() -> None:
    page = saved_page("invented-posting-sparse.html", "https://employer.example/careers")
    selectors = Selectors(role="h2.opening", description=".body", company=".employer")

    result = extract(page, by_selectors(selectors, company="Example GmbH"))

    assert isinstance(result, Failed)
    assert "company" in result.reason
    assert ".employer" in result.reason


def test_a_description_match_within_another_is_read_once() -> None:
    page = written_page(
        "<html><body><h1>Laboratory engineer</h1>"
        "<section><p>You run the wave flume.</p></section></body></html>"
    )
    selectors = Selectors(role="h1", description="section, section p")

    result = extract(page, by_selectors(selectors, company="Example GmbH"))

    assert isinstance(result, Extracted)
    assert result.postings[0].description == "You run the wave flume."


def test_something_that_only_looks_like_a_date_does_not_hide_the_one_beside_it() -> None:
    page = written_page(
        "<html><body><h1>Laboratory engineer</h1><p>Rigs.</p>"
        "<i>Reference 2026-13-40, apply by 1.2.2027</i></body></html>"
    )
    selectors = Selectors(role="h1", description="p", deadline="i")

    result = extract(page, by_selectors(selectors, company="Example GmbH"))

    assert isinstance(result, Extracted)
    assert result.postings[0].deadline == date(2027, 2, 1)


def test_the_raw_part_holds_what_the_selectors_picked_before_tidying() -> None:
    page = saved_page("invented-posting-in-sections.html", INVENTED_POSTING_URL)

    result = extract(page, by_selectors(IN_SECTIONS))

    assert isinstance(result, Extracted)
    raw = result.postings[0].raw
    assert raw["role"].startswith('<h1 class="job-title">\n')
    assert raw["company"] == '<dd class="employer">Hafenstadt Institute of Technology</dd>'
    assert raw["deadline"] == '<dd class="closes">Applications close on 31.10.2026 at noon</dd>'
    assert len(raw["description"]) == 3
    assert "towing tank &amp; compare" in raw["description"][0]


INVENTED_LISTING_URL = "https://tidewater.example/work-with-us"

ITEMS = Selectors(
    item="li.opening",
    role="h3",
    description=".body",
    location=".place",
    identifier=".reference",
    deadline=".closes time@datetime",
)


def test_a_page_listing_three_positions_is_read_into_three_postings() -> None:
    page = saved_page("invented-listing-three-positions.html", INVENTED_LISTING_URL)

    result = extract(page, by_selectors(ITEMS, company="Tidewater Instruments"))

    assert isinstance(result, Extracted)
    assert [posting.role for posting in result.postings] == [
        "Electronics technician",
        "Field service engineer",
        "Office assistant",
    ]
    assert {posting.company for posting in result.postings} == {"Tidewater Instruments"}
    assert {posting.url for posting in result.postings} == {INVENTED_LISTING_URL}


def test_each_posting_of_a_listing_holds_the_fields_found_within_its_item() -> None:
    page = saved_page("invented-listing-three-positions.html", INVENTED_LISTING_URL)

    result = extract(page, by_selectors(ITEMS, company="Tidewater Instruments"))

    assert isinstance(result, Extracted)
    technician, engineer, assistant = result.postings
    assert technician.identifier == "TI-7"
    assert technician.location == "Hafenstadt"
    assert technician.deadline is None
    assert engineer.identifier is None
    assert engineer.location == "Kiel"
    assert (
        engineer.description == "You install gauges along the coast. A driving licence is needed."
    )
    assert assistant.location is None
    assert assistant.deadline == date(2026, 11, 15)
    assert assistant.raw["role"] == "<h3>Office assistant</h3>"


def test_a_listing_whose_item_matches_nothing_yields_no_postings_and_is_no_failure() -> None:
    page = written_page("<html><body><h1>Work with us</h1><p>Nothing at present.</p></body></html>")

    result = extract(page, by_selectors(ITEMS, company="Tidewater Instruments"))

    assert result == Extracted(postings=[])


def test_an_item_lacking_a_role_fails_the_page_and_none_of_its_postings_is_kept() -> None:
    page = saved_page("invented-listing-item-without-role.html", INVENTED_LISTING_URL)

    result = extract(page, by_selectors(ITEMS, company="Tidewater Instruments"))

    assert isinstance(result, Failed)
    assert "no role" in result.reason
    assert "'h3'" in result.reason
    assert "li.opening" in result.reason


def test_markup_reduced_for_a_model_drops_what_no_selector_is_made_from() -> None:
    page = saved_page("invented-posting-in-sections.html", INVENTED_POSTING_URL)

    reduced = reduced_markup(page)

    for dropped in ("trackSection", "margin: 1em", "<svg", "An anchor", "the logo is drawn"):
        assert dropped not in reduced
    for dropped in ("<template", "A position in research", "<!DOCTYPE", "viewBox", "href"):
        assert dropped not in reduced


def test_markup_reduced_for_a_model_keeps_the_tags_with_their_ids_and_classes() -> None:
    page = saved_page("invented-posting-in-sections.html", INVENTED_POSTING_URL)

    reduced = reduced_markup(page)

    assert '<h1 class="job-title">Doctoral researcher in ship hydrodynamics</h1>' in reduced
    assert '<dd class="employer">Hafenstadt Institute of Technology</dd>' in reduced
    assert '<time class="published" datetime="2026-09-01T08:00:00+02:00">' in reduced
    assert '<h1 class="site-name">Hafenstadt Portal</h1>' in reduced
    assert reduced.count('<section class="job-section">') == 3


def test_markup_reduced_for_a_model_shortens_long_text_and_keeps_its_beginning() -> None:
    sentence = "You measure the wake of model hulls in the towing tank. "
    page = written_page(f"<html><body><p id='tasks'>{sentence * 40}</p></body></html>")

    reduced = reduced_markup(page)

    assert reduced.startswith('<html><body><p id="tasks">You measure the wake of model hulls')
    assert len(reduced) < 200
    assert reduced.endswith("…</p></body></html>")


def test_markup_reduced_for_a_model_is_capped_in_length() -> None:
    items = "".join(f"<li class='opening'>Position {number}</li>" for number in range(5000))
    page = written_page(f"<html><body><ul>{items}</ul></body></html>")

    assert len(reduced_markup(page, cap=2000)) == 2000
    assert len(reduced_markup(page)) < len(page.body) / 2


def test_derivation_tries_the_strategies_in_order_until_one_yields_postings() -> None:
    page = saved_page("tuhh-posting-digital-economics.html", TUHH_POSTING_URL)

    derived = derive_strategy(page)

    assert isinstance(derived, Derived)
    assert derived.strategy == Strategy.STRUCTURED_RECORD
    assert [posting.identifier for posting in derived.postings] == [
        "038dac54bf802d6b19e9ea4c68e61396d65cf78e"
    ]


def test_derivation_says_why_no_strategy_reads_a_page() -> None:
    page = saved_page("tuhh-listing-research-staff.html", TUHH_LISTING_URL)

    derived = derive_strategy(page)

    assert isinstance(derived, Underivable)
    assert set(derived.attempts) == set(Strategy)
    assert derived.attempts[Strategy.STRUCTURED_RECORD] == "the page yielded no Postings"


def test_a_staff_page_yields_its_obfuscated_contact_addresses() -> None:
    page = saved_page("tuhh-fds-staff.html", "https://www.tuhh.de/fds/institute/staff-1")

    addresses = contact_addresses(page)

    assert addresses[:3] == [
        "gerrit.falke@example.org",
        "y.ben-hourani@example.org",
        "b.hoelscher@example.org",
    ]
    assert len(addresses) == len(set(addresses))


def test_nothing_in_extraction_performs_io(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        saved_page(path.name, f"https://example.org/{path.name}") for path in PAGES.glob("*.html")
    ]

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("extraction reached for the filesystem, the network or the clock")

    monkeypatch.setattr("builtins.open", refuse)
    monkeypatch.setattr("os.open", refuse)
    monkeypatch.setattr("socket.socket", refuse)
    monkeypatch.setattr("time.time", refuse)

    for page in pages:
        extract(page, STRUCTURED_RECORD)
        extract(page, by_selectors(IN_SECTIONS))
        derive_strategy(page)
        discover_links(page, Hop(follow="."))
        contact_addresses(page)


def written_page(markup: str) -> Page:
    """A page written for one edge the saved pages do not happen to show."""
    return Page(url="https://example.org/careers/", body=markup.encode())


def test_a_relative_link_is_resolved_against_the_base_the_page_declares() -> None:
    page = written_page(
        '<html><head><base href="https://example.org/portal/"></head>'
        '<body><a href="posting/1">One</a></body></html>'
    )

    assert discover_links(page, Hop(follow="/posting/")) == ["https://example.org/portal/posting/1"]


def test_links_that_differ_only_by_fragment_are_one_link() -> None:
    page = written_page(
        '<html><body><a href="/posting/1">One</a><a href="/posting/1#apply">Apply</a></body></html>'
    )

    assert discover_links(page, Hop(follow="/posting/")) == ["https://example.org/posting/1"]


def test_a_link_that_is_not_a_url_does_not_hide_the_others() -> None:
    page = written_page(
        '<html><body><a href="http://[broken">x</a><a href="/posting/1">One</a></body></html>'
    )

    assert discover_links(page, Hop(follow="/posting/")) == ["https://example.org/posting/1"]


def test_remuneration_that_starts_at_zero_keeps_its_range() -> None:
    page = written_page(
        '<html><script type="application/ld+json; charset=utf-8">{"@type": "JobPosting",'
        ' "title": "Doctoral Researcher",'
        ' "description": "&lt;p&gt;Waves &amp;amp; wakes&lt;/p&gt;",'
        ' "hiringOrganization": "Example Institute", "baseSalary": {"currency": "EUR",'
        ' "value": {"minValue": 0, "maxValue": 50000, "unitText": "YEAR"}}}</script></html>'
    )

    result = extract(page, STRUCTURED_RECORD)

    assert isinstance(result, Extracted)
    [posting] = result.postings
    assert posting.company == "Example Institute"
    assert posting.description == "Waves & wakes"
    assert posting.remuneration == Remuneration(
        currency="EUR", low=Decimal(0), high=Decimal(50000), per="YEAR"
    )


def test_an_item_within_another_is_part_of_that_posting_and_not_a_second_one() -> None:
    page = written_page(
        "<html><body><ul><li><h3>Rigger</h3><div class='body'>You rig.<ul>"
        "<li>Heights are no trouble to you.</li></ul></div></li></ul></body></html>"
    )

    selectors = Selectors(item="li", role="h3", description=".body")

    result = extract(page, by_selectors(selectors, company="Tidewater Instruments"))

    assert isinstance(result, Extracted)
    assert [posting.role for posting in result.postings] == ["Rigger"]
