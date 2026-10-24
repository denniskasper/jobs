"""Polling Sources and keeping the Postings they find, through the HTTP surface.

Every page here is invented, as is every host.
"""

import html
import json
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from starlette.testclient import TestClient

from conftest import NOW, Pages, RememberedCommits, ScriptedModel, SettableClock
from jobs.polling import Schedule
from jobs.web import create_app

LISTING = "https://portal.example/jobs/list"
POSTING = "https://portal.example/jobs/posting/{}"


def record(role: str, identifier: str | None = None, **fields: Any) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": role,
        **({"identifier": identifier} if identifier else {}),
        "description": "<p>Research position in experimental fluid dynamics.</p>",
        "hiringOrganization": {"name": "Hafenstadt Institute of Technology"},
        "jobLocation": {"address": {"addressLocality": "Hafenstadt"}},
        **fields,
    }


def page_of(*records: dict[str, Any]) -> str:
    scripts = "".join(
        f"<script type='application/ld+json'>{json.dumps(each)}</script>" for each in records
    )
    return f"<html><head>{scripts}</head><body><h1>Open positions</h1></body></html>"


def listing_of(*numbers: int) -> str:
    links = "".join(f"<a href='/jobs/posting/{number}'>Position</a>" for number in numbers)
    return f"<html><body>{links}</body></html>"


def write_source(
    data_dir: Path,
    name: str,
    url: str = LISTING,
    *,
    hops: bool = True,
    extra: str = "",
    strategy: str = "structured-record",
) -> None:
    """Place a Source on disk the way a human would: by writing the file."""
    chain = "\n  hops:\n    - follow: /jobs/posting/\\d+" if hops else ""
    path = data_dir / "sources" / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"url: {url}\n{extra}config:\n  strategy: {strategy}{chain}\n", encoding="utf-8"
    )


def kept(data_dir: Path, source: str) -> list[dict[str, Any]]:
    """The Postings kept for a Source, as they are on disk."""
    files = sorted((data_dir / "postings" / source).glob("*.yaml"))
    return [yaml.safe_load(file.read_text(encoding="utf-8")) for file in files]


def test_a_poll_triggered_by_hand_keeps_the_postings_under_their_source(
    dashboard: TestClient, data_dir: Path, network: Pages, committer: RememberedCommits
) -> None:
    write_source(data_dir, "hafenstadt")
    network.serve(LISTING, listing_of(101, 102))
    network.serve(POSTING.format(101), page_of(record("Doctoral researcher", "101")))
    network.serve(POSTING.format(102), page_of(record("Laboratory engineer", "102")))

    polled = dashboard.post("/sources/hafenstadt/poll")

    assert polled.status_code == 200
    assert "Doctoral researcher" in polled.text
    assert "Laboratory engineer" in polled.text
    postings = kept(data_dir, "hafenstadt")
    assert sorted(posting["role"] for posting in postings) == [
        "Doctoral researcher",
        "Laboratory engineer",
    ]
    assert {posting["company"] for posting in postings} == {"Hafenstadt Institute of Technology"}
    [commit] = committer.commits
    assert set(sorted((data_dir / "postings" / "hafenstadt").glob("*.yaml"))) <= set(commit.paths)


def serve_two_postings(network: Pages) -> None:
    network.serve(LISTING, listing_of(101, 102))
    network.serve(POSTING.format(101), page_of(record("Doctoral researcher", "101")))
    network.serve(POSTING.format(102), page_of(record("Laboratory engineer", "102")))


def test_polling_an_unchanged_source_again_creates_no_new_postings(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt")
    serve_two_postings(network)
    dashboard.post("/sources/hafenstadt/poll")
    files = sorted((data_dir / "postings" / "hafenstadt").iterdir())

    dashboard.post("/sources/hafenstadt/poll")

    assert sorted((data_dir / "postings" / "hafenstadt").iterdir()) == files
    assert "2 Postings, 0 new, 0 changed" in " ".join(
        dashboard.get("/sources/hafenstadt").text.split()
    )


def test_a_posting_whose_content_changed_is_updated_in_place(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt")
    serve_two_postings(network)
    dashboard.post("/sources/hafenstadt/poll")
    network.serve(
        POSTING.format(101),
        page_of(record("Doctoral researcher (extended)", "101", validThrough="2026-11-30")),
    )

    dashboard.post("/sources/hafenstadt/poll")

    postings = kept(data_dir, "hafenstadt")
    assert sorted(posting["role"] for posting in postings) == [
        "Doctoral researcher (extended)",
        "Laboratory engineer",
    ]
    assert "1 changed" in dashboard.get("/sources/hafenstadt").text


def test_what_else_a_posting_file_holds_survives_a_poll(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt", POSTING.format(101), hops=False)
    network.serve(POSTING.format(101), page_of(record("Doctoral researcher", "101")))
    dashboard.post("/sources/hafenstadt/poll")
    [file] = (data_dir / "postings" / "hafenstadt").iterdir()
    file.write_text(file.read_text(encoding="utf-8") + "note: ask about the lab\n", "utf-8")

    dashboard.post("/sources/hafenstadt/poll")

    [posting] = kept(data_dir, "hafenstadt")
    assert posting["note"] == "ask about the lab"


def test_the_published_identifier_identifies_a_posting_even_when_its_url_moves(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt")
    network.serve(LISTING, listing_of(101))
    network.serve(POSTING.format(101), page_of(record("Doctoral researcher", "F-26-101")))
    dashboard.post("/sources/hafenstadt/poll")
    network.serve(LISTING, listing_of(977))
    network.serve(POSTING.format(977), page_of(record("Doctoral researcher", "F-26-101")))

    dashboard.post("/sources/hafenstadt/poll")

    [posting] = kept(data_dir, "hafenstadt")
    assert posting["identity"] == {"by": "identifier", "value": "F-26-101"}
    assert posting["url"] == POSTING.format(977)


def test_without_an_identifier_the_posting_url_identifies_a_posting(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt")
    network.serve(LISTING, listing_of(101))
    network.serve(POSTING.format(101), page_of(record("Doctoral researcher")))
    dashboard.post("/sources/hafenstadt/poll")
    network.serve(
        LISTING, "<html><body><a href='/jobs/posting/101?utm_source=mail#apply'>x</a></body></html>"
    )
    network.serve(
        POSTING.format(101) + "?utm_source=mail", page_of(record("Doctoral researcher, reworded"))
    )

    dashboard.post("/sources/hafenstadt/poll")

    [posting] = kept(data_dir, "hafenstadt")
    assert posting["identity"] == {"by": "url", "value": POSTING.format(101)}
    assert posting["role"] == "Doctoral researcher, reworded"


def test_with_no_identifier_and_no_url_of_its_own_a_posting_is_identified_by_what_it_says(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt", LISTING, hops=False)
    network.serve(LISTING, page_of(record("Doctoral researcher"), record("Laboratory engineer")))
    dashboard.post("/sources/hafenstadt/poll")
    network.serve(
        LISTING,
        page_of(
            record("Laboratory  ENGINEER", validThrough="2026-11-30"),
            record("Doctoral researcher"),
            record("Workshop technician"),
        ),
    )

    dashboard.post("/sources/hafenstadt/poll")

    postings = kept(data_dir, "hafenstadt")
    assert sorted(posting["role"] for posting in postings) == [
        "Doctoral researcher",
        "Laboratory  ENGINEER",
        "Workshop technician",
    ]
    assert {posting["identity"]["by"] for posting in postings} == {"content"}


def test_the_same_position_found_through_two_sources_is_two_postings(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt")
    write_source(data_dir, "institute", "https://institute.example/vacancies")
    serve_two_postings(network)
    network.serve("https://institute.example/vacancies", listing_of_absolute(101))

    dashboard.post("/poll")

    assert len(kept(data_dir, "hafenstadt")) == 2
    [posting] = kept(data_dir, "institute")
    assert posting["identity"] == {"by": "identifier", "value": "101"}


def listing_of_absolute(*numbers: int) -> str:
    links = "".join(f"<a href='{POSTING.format(number)}'>Position</a>" for number in numbers)
    return f"<html><body>{links}</body></html>"


def test_a_deadline_is_kept_and_a_posting_past_it_is_visibly_expired(
    dashboard: TestClient, data_dir: Path, network: Pages, clock: SettableClock
) -> None:
    write_source(data_dir, "hafenstadt", POSTING.format(101), hops=False)
    network.serve(
        POSTING.format(101),
        page_of(record("Doctoral researcher", "101", validThrough="2026-10-31")),
    )
    dashboard.post("/sources/hafenstadt/poll")

    [posting] = kept(data_dir, "hafenstadt")
    assert posting["deadline"].isoformat() == "2026-10-31"
    assert "2026-10-31" in dashboard.get("/sources/hafenstadt").text
    clock.now = NOW.replace(month=10, day=31)
    assert "Expired" not in dashboard.get("/sources/hafenstadt").text
    clock.now = NOW.replace(month=11, day=1)
    assert "Expired" in dashboard.get("/sources/hafenstadt").text


def test_first_seen_and_last_seen_are_recorded_and_first_shown_is_not(
    dashboard: TestClient, data_dir: Path, network: Pages, clock: SettableClock
) -> None:
    write_source(data_dir, "hafenstadt", POSTING.format(101), hops=False)
    network.serve(POSTING.format(101), page_of(record("Doctoral researcher", "101")))
    dashboard.post("/sources/hafenstadt/poll")
    clock.now = NOW + timedelta(days=3)

    dashboard.post("/sources/hafenstadt/poll")

    [posting] = kept(data_dir, "hafenstadt")
    assert posting["first_seen"] == NOW
    assert posting["last_seen"] == NOW + timedelta(days=3)
    assert "first_shown" not in posting


def test_what_the_site_said_is_kept_beside_the_posting(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt", POSTING.format(101), hops=False)
    network.serve(POSTING.format(101), page_of(record("Doctoral researcher", "101")))

    dashboard.post("/sources/hafenstadt/poll")

    [posting] = kept(data_dir, "hafenstadt")
    assert posting["raw"] == record("Doctoral researcher", "101")


def test_a_posting_the_sources_filter_rules_removed_is_kept_with_the_reason(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt")
    path = data_dir / "sources" / "hafenstadt.yaml"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "  filters:\n    - field: role\n      pattern: researcher\n",
        encoding="utf-8",
    )
    serve_two_postings(network)

    dashboard.post("/sources/hafenstadt/poll")

    reasons = {
        posting["role"]: posting.get("rejected", "") for posting in kept(data_dir, "hafenstadt")
    }
    assert reasons["Doctoral researcher"] == ""
    assert "does not match researcher" in reasons["Laboratory engineer"]


def test_one_source_failing_does_not_keep_the_others_from_being_polled(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "a-broken", "https://gone.example/jobs")
    write_source(data_dir, "hafenstadt")
    serve_two_postings(network)

    polled = dashboard.post("/poll")

    assert polled.status_code == 200
    assert len(kept(data_dir, "hafenstadt")) == 2
    assert "no network is available" in dashboard.get("/sources/a-broken").text


def test_a_source_naming_a_model_as_its_strategy_is_a_problem_and_is_never_polled(
    dashboard: TestClient, data_dir: Path, network: Pages, committer: RememberedCommits
) -> None:
    write_source(data_dir, "by-a-model", strategy="model")
    path = data_dir / "sources" / "by-a-model.yaml"
    before = path.read_text(encoding="utf-8")
    serve_two_postings(network)

    dashboard.post("/poll")
    refused = dashboard.post("/sources/by-a-model/poll")

    assert refused.status_code == 422
    assert network.fetched == []
    listed = dashboard.get("/sources").text
    assert "1 Source file could not be read" in listed
    assert "strategy is &#39;model&#39;" in listed
    assert path.read_text(encoding="utf-8") == before
    assert not (data_dir / "postings").exists()
    assert committer.commits == []


CAREERS = "https://employer.example/careers/"
OPENING = "https://employer.example/careers/opening/{}"


def opening_page(role: str, reference: str) -> str:
    return f"""<html><body>
<header><h1 class="site">Careers at Example</h1></header>
<main>
  <h1 class="opening">{role}</h1>
  <p class="meta">
    <span class="place">Hafenstadt</span>
    <span class="reference">{reference}</span>
    <time class="published" datetime="2026-09-01">1 September</time>
    <span class="closes">Apply by 31.10.2026</span>
  </p>
  <section class="text"><h2>Your tasks</h2><p>You run the wave flume.</p></section>
  <section class="text"><h2>Your profile</h2><p>A degree in engineering.</p></section>
</main>
</body></html>"""


SELECTORS = """\
  company: Example GmbH
  selectors:
    role: h1.opening
    description: section.text
    location: .meta .place
    identifier: .reference
    posted: time.published@datetime
    deadline: .closes
"""


def write_selectors_source(data_dir: Path, name: str) -> None:
    """A Source read by selectors, written by hand."""
    path = data_dir / "sources" / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"url: {CAREERS}\nconfig:\n  strategy: selectors\n"
        f"  hops:\n    - follow: /careers/opening/\\d+\n{SELECTORS}",
        encoding="utf-8",
    )


def serve_two_openings(network: Pages) -> None:
    network.serve(
        CAREERS,
        "<html><body><a href='opening/1'>One</a><a href='opening/2'>Two</a></body></html>",
    )
    network.serve(OPENING.format(1), opening_page("Doctoral researcher", "EX-1"))
    network.serve(OPENING.format(2), opening_page("Laboratory engineer", "EX-2"))


def test_a_source_written_by_hand_with_selectors_is_polled_and_its_postings_proposed(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_selectors_source(data_dir, "example")
    serve_two_openings(network)

    polled = dashboard.post("/sources/example/poll")

    assert polled.status_code == 200
    researcher, engineer = kept(data_dir, "example")
    assert (researcher["role"], engineer["role"]) == ("Doctoral researcher", "Laboratory engineer")
    assert researcher["company"] == "Example GmbH"
    assert researcher["location"] == "Hafenstadt"
    assert researcher["identifier"] == "EX-1"
    assert researcher["posted"].isoformat() == "2026-09-01"
    assert researcher["deadline"].isoformat() == "2026-10-31"
    assert researcher["description"] == (
        "Your tasks You run the wave flume. Your profile A degree in engineering."
    )
    assert researcher["raw"]["role"] == '<h1 class="opening">Doctoral researcher</h1>'
    proposals = dashboard.get("/proposals").text
    for shown in ("Doctoral researcher", "Laboratory engineer", "Example GmbH", "Hafenstadt"):
        assert shown in proposals
    for shown in ("EX-1", "2026-09-01", "2026-10-31", "You run the wave flume."):
        assert shown in proposals


def test_a_second_poll_by_selectors_writes_over_the_same_files(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_selectors_source(data_dir, "example")
    serve_two_openings(network)
    dashboard.post("/sources/example/poll")
    files = sorted((data_dir / "postings" / "example").iterdir())

    dashboard.post("/sources/example/poll")

    assert sorted((data_dir / "postings" / "example").iterdir()) == files
    assert "2 Postings, 0 new, 0 changed" in " ".join(
        dashboard.get("/sources/example").text.split()
    )


def test_a_poll_by_selectors_asks_no_model(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    write_selectors_source(data_dir, "example")
    serve_two_openings(network)

    dashboard.post("/sources/example/poll")

    assert len(kept(data_dir, "example")) == 2
    assert model.asked == []


def test_a_page_the_selectors_find_no_role_on_is_an_error_and_never_a_posting(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_selectors_source(data_dir, "example")
    serve_two_openings(network)
    network.serve(OPENING.format(2), "<html><body><h1>We have moved</h1></body></html>")

    dashboard.post("/sources/example/poll")

    [posting] = kept(data_dir, "example")
    assert posting["role"] == "Doctoral researcher"
    shown = html.unescape(dashboard.get("/sources/example").text)
    assert OPENING.format(2) in shown
    assert "the page has no role: the selector 'h1.opening' found none" in shown


def test_a_disabled_source_is_not_polled(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt", extra="enabled: false\n")
    serve_two_postings(network)

    dashboard.post("/poll")
    refused = dashboard.post("/sources/hafenstadt/poll")

    assert network.fetched == []
    assert refused.status_code == 422
    assert "disabled" in refused.text


def test_a_poll_asks_no_model(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    write_source(data_dir, "hafenstadt")
    serve_two_postings(network)

    dashboard.post("/sources/hafenstadt/poll")

    assert len(kept(data_dir, "hafenstadt")) == 2
    assert model.asked == []


def test_two_postings_that_would_become_one_are_reported_and_not_merged_quietly(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt", LISTING, hops=False)
    network.serve(
        LISTING,
        page_of(
            record("Doctoral researcher", validThrough="2026-10-31"),
            record("Doctoral researcher", validThrough="2026-11-30"),
        ),
    )

    dashboard.post("/sources/hafenstadt/poll")

    [posting] = kept(data_dir, "hafenstadt")
    assert posting["deadline"].isoformat() == "2026-10-31"
    assert "only the first was kept" in dashboard.get("/sources/hafenstadt").text


def test_a_record_a_page_carries_twice_is_one_posting_and_no_trouble(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt", LISTING, hops=False)
    network.serve(LISTING, page_of(record("Doctoral researcher"), record("Doctoral researcher")))

    dashboard.post("/sources/hafenstadt/poll")

    assert len(kept(data_dir, "hafenstadt")) == 1
    assert "only the first was kept" not in dashboard.get("/sources/hafenstadt").text


def until(holds: Callable[[], bool], patience: float = 5.0) -> bool:
    """Whether something the schedule does in its own time came about."""
    end = time.monotonic() + patience
    while not holds() and time.monotonic() < end:
        time.sleep(0.01)
    return holds()


def test_sources_are_polled_on_a_schedule_with_nobody_asking(
    data_dir: Path,
    clock: SettableClock,
    network: Pages,
    committer: RememberedCommits,
    model: ScriptedModel,
) -> None:
    write_source(data_dir, "hafenstadt")
    network.serve(LISTING, listing_of(101))
    network.serve(POSTING.format(101), page_of(record("Doctoral researcher", "101")))
    app = create_app(
        data_dir=data_dir,
        clock=clock,
        network=network,
        committer=committer,
        model=model,
        sleep=lambda seconds: None,
        schedule=Schedule(every=timedelta(hours=24), check_every=0.01),
    )

    with TestClient(app):
        assert until(lambda: len(kept(data_dir, "hafenstadt")) == 1)
        assert until(lambda: len(committer.commits) == 1)
        serve_two_postings(network)
        clock.now = NOW + timedelta(hours=23)
        fetched = len(network.fetched)
        time.sleep(0.1)
        assert len(network.fetched) == fetched

        clock.now = NOW + timedelta(hours=24)
        assert until(lambda: len(kept(data_dir, "hafenstadt")) == 2)


def test_a_restart_does_not_poll_a_source_again_before_it_is_due(
    dashboard: TestClient,
    data_dir: Path,
    clock: SettableClock,
    network: Pages,
    committer: RememberedCommits,
    model: ScriptedModel,
) -> None:
    write_source(data_dir, "hafenstadt")
    serve_two_postings(network)
    dashboard.post("/sources/hafenstadt/poll")
    fetched = len(network.fetched)
    restarted = create_app(
        data_dir=data_dir,
        clock=clock,
        network=network,
        committer=committer,
        model=model,
        sleep=lambda seconds: None,
        schedule=Schedule(every=timedelta(hours=24), check_every=0.01),
    )

    with TestClient(restarted):
        time.sleep(0.1)

    assert len(network.fetched) == fetched


def test_a_poll_record_touched_by_hand_does_not_stop_the_schedule(
    data_dir: Path,
    clock: SettableClock,
    network: Pages,
    committer: RememberedCommits,
    model: ScriptedModel,
) -> None:
    write_source(data_dir, "hafenstadt")
    serve_two_postings(network)
    (data_dir / "polls").mkdir()
    (data_dir / "polls" / "hafenstadt.yaml").write_text("polled: 2026-09-01 10:00:00\n", "utf-8")
    app = create_app(
        data_dir=data_dir,
        clock=clock,
        network=network,
        committer=committer,
        model=model,
        sleep=lambda seconds: None,
        schedule=Schedule(every=timedelta(hours=24), check_every=0.01),
    )

    with TestClient(app):
        assert until(lambda: len(kept(data_dir, "hafenstadt")) == 2)
