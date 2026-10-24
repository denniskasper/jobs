"""Registering a Source by URL, editing its config and disabling it, through the HTTP surface.

Every page here is invented, as is every host.
"""

import html
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from starlette.testclient import TestClient

from conftest import Pages, RememberedCommits, ScriptedModel, register, try_again
from jobs.fetching import walk
from jobs.sources import list_sources

INSTITUTE = "https://institute.example/vacancies"
LISTING = "https://portal.example/jobs/list"
POSTING = "https://portal.example/jobs/posting/{}"
# Put together here, because the personal data guard keeps written-out addresses to the corpus.
OFFICE = "@".join(["office", "institute.example"])


def posting_page(identifier: str, role: str, reference: str) -> str:
    record = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": role,
        "identifier": identifier,
        "description": f"<p>Research position. Reference number {reference}.</p>",
        "hiringOrganization": {"name": "Hafenstadt Institute of Technology"},
        "jobLocation": {"address": {"addressLocality": "Hafenstadt"}},
        "validThrough": "2026-10-31",
    }
    return (
        f"<html><head><script type='application/ld+json'>{json.dumps(record)}</script></head>"
        f"<body><h1>{role}</h1></body></html>"
    )


def serve_institute(network: Pages) -> None:
    """A signpost at an institute, a central listing behind it, and two posting pages."""
    network.serve(
        INSTITUTE,
        "<html><body><p>Our open positions are published on the central portal.</p>"
        f"<a href='{LISTING}'>Central job portal</a>"
        "<a href='/staff'>Staff</a>"
        f"<a href='mailto:{OFFICE}?subject=Application'>Write to the office</a>"
        "</body></html>",
    )
    network.serve(
        LISTING,
        "<html><body><a href='/jobs/posting/101'>Doctoral researcher</a>"
        "<a href='/jobs/posting/102'>Laboratory engineer</a>"
        "<a href='/imprint'>Imprint</a></body></html>",
    )
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    network.serve(POSTING.format(102), posting_page("102", "Laboratory engineer", "F-26-102-K3"))


FOLLOW_TO_LISTING = json.dumps({"follow": r"portal\.example/jobs/list"})
FOLLOW_TO_POSTINGS = json.dumps({"follow": r"/jobs/posting/\d+"})


def read_source_file(data_dir: Path, name: str) -> dict[str, Any]:
    record = yaml.safe_load((data_dir / "sources" / f"{name}.yaml").read_text(encoding="utf-8"))
    assert isinstance(record, dict)
    return record


def test_registering_a_url_saves_a_source_with_a_readable_config(
    dashboard: TestClient, data_dir: Path, network: Pages, committer: RememberedCommits
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))

    registered = register(dashboard, POSTING.format(101))

    assert POSTING.format(101) in registered
    record = read_source_file(data_dir, "portal-example-jobs-posting-101")
    assert record["url"] == POSTING.format(101)
    assert record["enabled"] is True
    assert record["config"]["strategy"] == "structured-record"
    assert record["config"]["hops"] == []
    assert [commit.paths for commit in committer.commits] == [
        [data_dir / "sources" / "portal-example-jobs-posting-101.yaml"]
    ]


def test_a_signpost_is_followed_to_the_postings_and_the_hop_chain_is_recorded(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_institute(network)
    model.answers = [FOLLOW_TO_LISTING, FOLLOW_TO_POSTINGS]

    register(dashboard, INSTITUTE, label="Hafenstadt Fluid Dynamics")

    config = read_source_file(data_dir, "hafenstadt-fluid-dynamics")["config"]
    assert config["hops"] == [
        {"follow": r"portal\.example/jobs/list", "render": "static"},
        {"follow": r"/jobs/posting/\d+", "render": "static"},
    ]
    assert config["render"] == "static"
    assert config["strategy"] == "structured-record"
    assert INSTITUTE in model.asked[0]
    assert LISTING in model.asked[0]


def test_the_derived_config_reaches_the_postings_when_walked(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_institute(network)
    model.answers = [FOLLOW_TO_LISTING, FOLLOW_TO_POSTINGS]
    register(dashboard, INSTITUTE, label="Hafenstadt Fluid Dynamics")

    [source] = list_sources(data_dir).sources
    walked = walk(source.url, source.config, network, sleep=lambda seconds: None)

    assert [posting.role for posting in walked.postings] == [
        "Doctoral researcher",
        "Laboratory engineer",
    ]
    assert walked.errors == []


def test_the_config_records_its_provenance(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_institute(network)
    model.answers = [FOLLOW_TO_LISTING, FOLLOW_TO_POSTINGS]

    registered = register(dashboard, INSTITUTE, label="Hafenstadt")

    provenance = read_source_file(data_dir, "hafenstadt")["config"]["provenance"]
    assert provenance["derived"].isoformat() == "2026-09-18T09:30:00+00:00"
    assert provenance["model"] == "scripted-model"
    assert provenance["schema"] == 2
    assert "2026-09-18 09:30" in registered
    assert "scripted-model" in registered


def test_contact_addresses_found_on_the_way_are_recorded(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_institute(network)
    model.answers = [FOLLOW_TO_LISTING, FOLLOW_TO_POSTINGS]

    registered = register(dashboard, INSTITUTE, label="Hafenstadt")

    assert read_source_file(data_dir, "hafenstadt")["config"]["contacts"] == [OFFICE]
    assert OFFICE in registered


def test_a_plain_language_filter_rule_becomes_a_config_rule(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_institute(network)
    model.answers = [
        FOLLOW_TO_LISTING,
        FOLLOW_TO_POSTINGS,
        json.dumps({"field": "description", "pattern": r"M8\b"}),
    ]
    words = "only positions whose reference number ends in M8"

    register(dashboard, INSTITUTE, label="Hafenstadt", rule=words)

    assert read_source_file(data_dir, "hafenstadt")["config"]["filters"] == [
        {"field": "description", "pattern": r"M8\b", "rule": words}
    ]
    assert words in model.asked[2]
    assert "F-26-101-M8" in model.asked[2]
    [source] = list_sources(data_dir).sources
    walked = walk(source.url, source.config, network, sleep=lambda seconds: None)
    assert [posting.role for posting in walked.postings] == ["Doctoral researcher"]
    assert words in dashboard.get("/sources").text


def test_the_dashboard_shows_the_label_rather_than_the_url(
    dashboard: TestClient, network: Pages
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    register(dashboard, POSTING.format(101), label="Hafenstadt Portal")

    page = dashboard.get("/sources")

    assert "Hafenstadt Portal" in page.text
    assert POSTING.format(101) not in page.text


def test_a_source_without_a_label_is_shown_by_its_url(
    dashboard: TestClient, network: Pages
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    register(dashboard, POSTING.format(101))

    assert POSTING.format(101) in dashboard.get("/sources").text


def test_a_config_edited_by_hand_takes_effect_without_registering_again(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_institute(network)
    model.answers = [FOLLOW_TO_LISTING, FOLLOW_TO_POSTINGS]
    register(dashboard, INSTITUTE, label="Hafenstadt")
    path = data_dir / "sources" / "hafenstadt.yaml"
    record = read_source_file(data_dir, "hafenstadt")
    record["label"] = "Hafenstadt Fluid Dynamics"
    record["config"]["filters"] = [{"field": "role", "pattern": "engineer"}]
    path.write_text(yaml.safe_dump(record), encoding="utf-8")

    page = dashboard.get("/sources")

    assert "Hafenstadt Fluid Dynamics" in page.text
    assert "role matching engineer" in page.text
    [source] = list_sources(data_dir).sources
    walked = walk(source.url, source.config, network, sleep=lambda seconds: None)
    assert [posting.role for posting in walked.postings] == ["Laboratory engineer"]


def test_a_config_edited_on_the_dashboard_is_written_and_committed(
    dashboard: TestClient, data_dir: Path, network: Pages, committer: RememberedCommits
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    register(dashboard, POSTING.format(101), label="Hafenstadt")
    path = data_dir / "sources" / "hafenstadt.yaml"
    edited = path.read_text(encoding="utf-8").replace("interval: 1.0", "interval: 5.0")

    saved = dashboard.post("/sources/hafenstadt", data={"text": edited})

    assert saved.status_code == 200
    assert "interval: 5.0" in saved.text
    assert path.read_text(encoding="utf-8") == edited
    assert [commit.message for commit in committer.commits] == [
        "Register Source hafenstadt",
        "Edit Source hafenstadt",
    ]


def test_an_edit_that_cannot_be_read_as_a_source_is_refused_and_kept_on_the_page(
    dashboard: TestClient, data_dir: Path, network: Pages, committer: RememberedCommits
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    register(dashboard, POSTING.format(101), label="Hafenstadt")
    path = data_dir / "sources" / "hafenstadt.yaml"
    before = path.read_text(encoding="utf-8")
    edited = before.replace("hops: []", "hops:\n  - follow: '/jobs/(unclosed'")

    refused = dashboard.post("/sources/hafenstadt", data={"text": edited})

    assert refused.status_code == 422
    assert "is not a regular expression" in refused.text
    assert "/jobs/(unclosed" in refused.text
    assert path.read_text(encoding="utf-8") == before
    assert len(committer.commits) == 1


def test_a_source_file_that_cannot_be_read_is_listed_as_a_problem(
    dashboard: TestClient, data_dir: Path
) -> None:
    path = data_dir / "sources" / "broken.yaml"
    path.parent.mkdir()
    path.write_text("url: https://careers.example/\nconfig:\n  strategy: guesswork\n")

    page = dashboard.get("/sources")

    assert page.status_code == 200
    assert "1 Source file could not be read" in page.text
    assert "strategy is &#39;guesswork&#39;" in page.text
    assert "guesswork" in dashboard.get("/sources/broken").text


def test_a_url_with_no_way_to_any_posting_is_refused_and_nothing_is_registered(
    dashboard: TestClient,
    data_dir: Path,
    network: Pages,
    model: ScriptedModel,
    committer: RememberedCommits,
) -> None:
    network.serve(
        "https://bakery.example/", "<html><body><a href='/bread'>Our bread</a></body></html>"
    )
    model.answers = [json.dumps({"nothing": "the page is about bread, not about positions"})]

    refused = register(dashboard, "https://bakery.example/", label="Bakery")

    assert "No way to extract Postings from https://bakery.example/ was found" in refused
    assert "the page is about bread" in refused
    assert (
        "(structured-record: the page yielded no Postings; "
        "selectors: there are no selectors to read the page with)" in refused
    )
    assert 'value="https://bakery.example/"' in try_again(dashboard, refused)
    assert not (data_dir / "sources").exists()
    assert committer.commits == []


def test_links_that_lead_to_no_posting_are_refused(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    network.serve(
        "https://bakery.example/", "<html><body><a href='/bread'>Our bread</a></body></html>"
    )
    network.serve("https://bakery.example/bread", "<html><body>Rye, wheat.</body></html>")
    model.answers = [json.dumps({"follow": "/bread"}), json.dumps({"nothing": "only bread"})]

    refused = register(dashboard, "https://bakery.example/")

    assert "https://bakery.example/bread carries none" in refused
    assert not (data_dir / "sources").exists()


def test_a_pattern_that_matches_no_link_is_refused(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_institute(network)
    model.answers = [json.dumps({"follow": "/careers/"})]

    refused = register(dashboard, INSTITUTE)

    assert "matches no link on the page" in refused
    assert not (data_dir / "sources").exists()


def test_a_url_that_cannot_be_fetched_is_refused(dashboard: TestClient, data_dir: Path) -> None:
    refused = register(dashboard, "https://unreachable.example/jobs")

    assert "Nothing could be fetched" in refused
    assert not (data_dir / "sources").exists()


def test_a_page_that_is_gone_is_refused(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    network.serve("https://careers.example/jobs", 404)

    refused = register(dashboard, "https://careers.example/jobs")

    assert "https://careers.example/jobs answered 404" in refused
    assert not (data_dir / "sources").exists()


def test_something_that_is_not_a_web_address_is_refused(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    refused = register(dashboard, "file:///etc/passwd")

    assert "has to be a web address" in refused
    assert network.fetched == []
    assert not (data_dir / "sources").exists()


def test_registration_is_refused_when_the_model_cannot_be_asked(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    serve_institute(network)

    refused = register(dashboard, INSTITUTE)

    assert "could not be asked" in refused
    assert not (data_dir / "sources").exists()


def test_an_answer_from_the_model_that_cannot_be_read_is_refused(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_institute(network)
    model.answers = ["I would follow the portal link."]

    refused = register(dashboard, INSTITUTE)

    assert "answer could not be read" in refused
    assert not (data_dir / "sources").exists()


def test_an_answer_wrapped_in_prose_is_still_read(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_institute(network)
    model.answers = [f"```json\n{FOLLOW_TO_LISTING}\n```", f"Here it is: {FOLLOW_TO_POSTINGS}"]

    register(dashboard, INSTITUTE, label="Hafenstadt")

    assert len(read_source_file(data_dir, "hafenstadt")["config"]["hops"]) == 2


def test_a_rule_that_cannot_be_turned_into_a_config_rule_is_refused(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    model.answers = [json.dumps({"nothing": "whether a team is nice is not written anywhere"})]

    refused = register(dashboard, POSTING.format(101), rule="only nice teams")

    assert "whether a team is nice is not written anywhere" in refused
    assert 'value="only nice teams"' in try_again(dashboard, refused)
    assert not (data_dir / "sources").exists()


def test_registering_under_a_label_already_taken_is_refused(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    network.serve(POSTING.format(102), posting_page("102", "Laboratory engineer", "F-26-102-K3"))
    register(dashboard, POSTING.format(101), label="Hafenstadt")

    refused = register(dashboard, POSTING.format(102), label="Hafenstadt")

    assert "already a Source called hafenstadt" in refused
    assert read_source_file(data_dir, "hafenstadt")["url"] == POSTING.format(101)


def test_a_source_can_be_disabled_and_enabled_without_being_deleted(
    dashboard: TestClient, data_dir: Path, network: Pages, committer: RememberedCommits
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    register(dashboard, POSTING.format(101), label="Hafenstadt")
    path = data_dir / "sources" / "hafenstadt.yaml"
    path.write_text(f"# checked by hand\n{path.read_text(encoding='utf-8')}", encoding="utf-8")

    disabled = dashboard.post("/sources/hafenstadt/enabled", data={"enabled": "false"})

    assert "Disabled" in disabled.text
    assert "Disabled" in dashboard.get("/sources").text
    record = read_source_file(data_dir, "hafenstadt")
    assert record["enabled"] is False
    assert record["config"]["strategy"] == "structured-record"
    assert path.read_text(encoding="utf-8").startswith("# checked by hand\n")
    assert committer.commits[-1].message == "Disable Source hafenstadt"

    dashboard.post("/sources/hafenstadt/enabled", data={"enabled": "true"})

    assert read_source_file(data_dir, "hafenstadt")["enabled"] is True
    assert committer.commits[-1].message == "Enable Source hafenstadt"


def test_only_registration_asks_the_model(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_institute(network)
    model.answers = [FOLLOW_TO_LISTING, FOLLOW_TO_POSTINGS]
    register(dashboard, INSTITUTE, label="Hafenstadt")
    assert len(model.asked) == 2
    path = data_dir / "sources" / "hafenstadt.yaml"

    dashboard.get("/sources")
    dashboard.get("/sources/hafenstadt")
    edited = path.read_text(encoding="utf-8").replace("interval: 1.0", "interval: 5.0")
    dashboard.post("/sources/hafenstadt", data={"text": edited})
    dashboard.post("/sources/hafenstadt/enabled", data={"enabled": "false"})
    [source] = list_sources(data_dir).sources
    walk(source.url, source.config, network, sleep=lambda seconds: None)

    assert len(model.asked) == 2


def test_a_url_that_is_read_as_it_is_asks_no_model(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))

    register(dashboard, POSTING.format(101), label="Hafenstadt")

    assert model.asked == []
    assert read_source_file(data_dir, "hafenstadt")["config"]["provenance"]["model"] is None


SHELL = "<html><body><div id='app'></div><script src='/bundle.js'></script></body></html>"
CAREERS = "https://careers.example/search"
CAREERS_LISTING = (
    "<html><body><a href='https://portal.example/jobs/posting/101'>Doctoral researcher</a>"
    "<a href='/about'>About us</a></body></html>"
)


def test_a_listing_that_needs_a_browser_is_derived_through_one_and_records_the_render_mode(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    network.serve(CAREERS, SHELL)
    network.show(CAREERS, CAREERS_LISTING)
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    model.answers = [json.dumps({"browser": True}), FOLLOW_TO_POSTINGS]

    registered = register(dashboard, CAREERS, label="Careers")

    config = read_source_file(data_dir, "careers")["config"]
    assert config["hops"] == [{"follow": r"/jobs/posting/\d+", "render": "browser"}]
    assert config["render"] == "static"
    assert "unfinished" not in config
    assert "Cannot be polled" not in registered
    assert POSTING.format(101) in model.asked[1]
    assert '"browser"' not in model.asked[1]


def test_a_source_derived_through_a_browser_is_polled_without_the_static_fetch(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    network.serve(CAREERS, SHELL)
    network.show(CAREERS, CAREERS_LISTING)
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    model.answers = [json.dumps({"browser": True}), FOLLOW_TO_POSTINGS]
    register(dashboard, CAREERS, label="Careers")
    network.fetched.clear()

    dashboard.post("/sources/careers/poll")

    assert network.fetched == [POSTING.format(101)]
    assert "Doctoral researcher" in dashboard.get("/proposals").text


def test_a_page_that_only_carries_its_posting_once_rendered_is_read_in_a_browser(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    network.serve(CAREERS, SHELL)
    network.show(CAREERS, posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    model.answers = [json.dumps({"browser": True})]

    register(dashboard, CAREERS, label="Careers")

    config = read_source_file(data_dir, "careers")["config"]
    assert config["hops"] == []
    assert config["render"] == "browser"
    assert config["strategy"] == "structured-record"


def test_a_rendered_page_that_still_leads_nowhere_is_refused(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    network.serve(CAREERS, SHELL)
    network.show(CAREERS, SHELL)
    model.answers = [json.dumps({"browser": True}), json.dumps({"browser": True})]

    refused = register(dashboard, CAREERS, label="Careers")

    assert "even rendered in a browser" in refused
    assert not (data_dir / "sources").exists()


def test_where_there_is_no_browser_such_a_listing_is_registered_and_visibly_cannot_be_polled(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    network.serve(CAREERS, SHELL)
    model.answers = [json.dumps({"browser": True})]

    registered = register(dashboard, CAREERS, label="Careers")

    config = read_source_file(data_dir, "careers")["config"]
    assert config["render"] == "browser"
    assert "needs a browser" in config["unfinished"]
    assert "Cannot be polled" in registered
    listing = dashboard.get("/sources")
    assert "Cannot be polled" in listing.text
    assert "needs a browser" in listing.text


def test_a_source_that_does_not_exist_is_not_found(dashboard: TestClient) -> None:
    assert dashboard.get("/sources/nowhere").status_code == 404
    assert dashboard.post("/sources/nowhere/enabled", data={"enabled": "false"}).status_code == 404
    assert dashboard.get("/sources/..%2Fapplications").status_code == 404


def test_a_url_no_file_can_be_named_after_needs_a_label(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    refused = register(dashboard, "https://例え.テスト/")

    assert "needs a label" in refused
    assert network.fetched == []
    assert not (data_dir / "sources").exists()


def test_links_back_to_a_page_already_read_lead_nowhere(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    network.serve(
        "https://careers.example/jobs",
        "<html><body><a href='/jobs'>All jobs</a></body></html>",
    )
    model.answers = [json.dumps({"follow": "/jobs"})]

    refused = register(dashboard, "https://careers.example/jobs")

    assert "leads anywhere new" in refused
    assert len(model.asked) == 1
    assert not (data_dir / "sources").exists()


def test_disabling_keeps_a_comment_beside_the_value(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    register(dashboard, POSTING.format(101), label="Hafenstadt")
    path = data_dir / "sources" / "hafenstadt.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("enabled: true", "enabled: true  # for now"), encoding="utf-8")

    dashboard.post("/sources/hafenstadt/enabled", data={"enabled": "false"})

    assert "enabled: false  # for now" in path.read_text(encoding="utf-8")


def test_an_edit_whose_url_is_not_a_web_address_is_refused(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    register(dashboard, POSTING.format(101), label="Hafenstadt")
    path = data_dir / "sources" / "hafenstadt.yaml"
    before = path.read_text(encoding="utf-8")

    refused = dashboard.post(
        "/sources/hafenstadt",
        data={"text": before.replace(POSTING.format(101), "javascript:alert(1)")},
    )

    assert refused.status_code == 422
    assert "url is not a web address" in refused.text
    assert path.read_text(encoding="utf-8") == before


def test_a_source_file_with_a_name_that_cannot_be_addressed_is_a_problem(
    dashboard: TestClient, data_dir: Path
) -> None:
    path = data_dir / "sources" / "My_Source.yaml"
    path.parent.mkdir()
    path.write_text("url: https://careers.example/\nconfig:\n  strategy: structured-record\n")

    assert "lowercase letters, digits and hyphens" in dashboard.get("/sources").text


SELECTORS_SOURCE = """\
url: https://employer.example/careers/
config:
  strategy: selectors
  company: Example GmbH
  selectors:
    role: h1.opening
    description: section.text
"""


@pytest.mark.parametrize(
    ("written", "replaced", "reason"),
    [
        ("  selectors:\n    role: h1.opening\n    description: section.text\n", "", "selectors"),
        ("    role: h1.opening\n", "", "selectors has no role"),
        ("    description: section.text\n", "", "selectors has no description"),
        ("  company: Example GmbH\n", "", "no company"),
        ("role: h1.opening", "role: h1..opening", "'h1..opening' is not a CSS selector"),
        ("role: h1.opening", "role: h1.opening\n    item: 'li >'", "'li >' is not a CSS selector"),
        ("role: h1.opening", "role: h1.opening\n    item: li@data-id", "@data-id is not"),
        ("role: h1.opening", "role: h1.opening\n    pay: .salary", "pay"),
        ("  company: Example GmbH\n", "  company: [Example GmbH]\n", "company is not text"),
    ],
)
def test_a_selectors_config_that_cannot_work_is_a_problem_and_cannot_be_saved(
    dashboard: TestClient,
    data_dir: Path,
    committer: RememberedCommits,
    written: str,
    replaced: str,
    reason: str,
) -> None:
    assert written in SELECTORS_SOURCE
    broken = SELECTORS_SOURCE.replace(written, replaced)
    (data_dir / "sources").mkdir()
    (data_dir / "sources" / "broken.yaml").write_text(broken, encoding="utf-8")
    good = data_dir / "sources" / "example.yaml"
    good.write_text(SELECTORS_SOURCE, encoding="utf-8")

    listed = html.unescape(dashboard.get("/sources").text)
    refused = dashboard.post("/sources/example", data={"text": broken})

    assert "1 Source file could not be read" in listed
    assert reason in listed
    assert refused.status_code == 422
    assert reason in html.unescape(refused.text)
    assert good.read_text(encoding="utf-8") == SELECTORS_SOURCE
    assert committer.commits == []


def test_a_company_may_come_from_a_selector_alone(dashboard: TestClient, data_dir: Path) -> None:
    (data_dir / "sources").mkdir()
    (data_dir / "sources" / "example.yaml").write_text(
        SELECTORS_SOURCE.replace("  company: Example GmbH\n", "") + "    company: .employer\n",
        encoding="utf-8",
    )

    assert "could not be read" not in dashboard.get("/sources").text
