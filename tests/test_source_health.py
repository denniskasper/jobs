"""Source health: what the polls of a Source say about whether it still works, through the HTTP
surface.

Every page here is invented, as is every host.
"""

import html
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from conftest import Pages, RememberedCommits, ScriptedModel, SettableClock
from jobs.fetching import Response
from jobs.web import create_app
from test_polling import (
    LISTING,
    OPENING,
    POSTING,
    kept,
    listing_of,
    page_of,
    record,
    serve_two_openings,
    serve_two_postings,
    write_selectors_source,
    write_source,
)

EMPTY = "<html><body><h1>Open positions</h1><p>None at the moment.</p></body></html>"


def shown(page: str) -> str:
    """What a page says, with the markup's line breaks taken out of the sentences."""
    return " ".join(page.split())


def test_a_source_that_finds_nothing_on_consecutive_polls_is_flagged_unhealthy(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt", hops=False)
    network.serve(LISTING, EMPTY)

    for _ in range(3):
        dashboard.post("/sources/hafenstadt/poll")

    assert "Unhealthy" in dashboard.get("/sources").text
    page = shown(dashboard.get("/sources/hafenstadt").text)
    assert "Unhealthy" in page
    assert "no posting on 3 consecutive polls" in page.casefold()


def test_how_many_empty_polls_flag_a_source_is_set_in_its_config(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt", hops=False)
    (data_dir / "sources" / "hafenstadt.yaml").write_text(
        f"url: {LISTING}\nconfig:\n  strategy: structured-record\n  unhealthy_after: 2\n",
        encoding="utf-8",
    )
    network.serve(LISTING, EMPTY)

    dashboard.post("/sources/hafenstadt/poll")
    once = dashboard.get("/sources").text
    dashboard.post("/sources/hafenstadt/poll")
    twice = dashboard.get("/sources").text

    assert "Unhealthy" not in once
    assert "Unhealthy" in twice


def test_a_quiet_source_is_told_apart_from_an_unhealthy_one(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt", hops=False)
    network.serve(LISTING, EMPTY)

    dashboard.post("/sources/hafenstadt/poll")

    page = shown(dashboard.get("/sources/hafenstadt").text)
    assert "Quiet" in page
    assert "Unhealthy" not in page
    assert "flagged unhealthy after 3" in page
    assert "Quiet" in dashboard.get("/sources").text


def test_a_source_that_fails_to_fetch_is_flagged_at_once_and_the_error_is_shown(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt")
    network.serve(LISTING, 503)

    dashboard.post("/sources/hafenstadt/poll")

    listed = shown(dashboard.get("/sources").text)
    assert "Unhealthy" in listed
    assert "503 Service Unavailable" in listed
    page = shown(dashboard.get("/sources/hafenstadt").text)
    assert "Unhealthy" in page
    assert "503 Service Unavailable" in page


MOVED = "<html><body><h1>We have moved</h1></body></html>"


def test_a_selectors_source_with_a_page_that_yields_no_role_is_flagged_at_once(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_selectors_source(data_dir, "example")
    serve_two_openings(network)
    network.serve(OPENING.format(2), MOVED)

    dashboard.post("/sources/example/poll")

    assert "Unhealthy" in shown(dashboard.get("/sources").text)
    page = html.unescape(shown(dashboard.get("/sources/example").text))
    assert "Unhealthy" in page
    assert (
        "employer.example/careers/opening/2 could not be read: "
        "the page has no role: the selector 'h1.opening' found none"
    ) in page
    assert [posting["role"] for posting in kept(data_dir, "example")] == ["Doctoral researcher"]


def test_a_selectors_source_that_is_read_again_is_no_longer_flagged(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_selectors_source(data_dir, "example")
    serve_two_openings(network)
    network.serve(OPENING.format(2), MOVED)
    dashboard.post("/sources/example/poll")
    serve_two_openings(network)

    dashboard.post("/sources/example/poll")

    assert "Unhealthy" not in dashboard.get("/sources").text
    page = dashboard.get("/sources/example").text
    assert "Unhealthy" not in page
    assert "Healthy" in page


def test_a_malformed_structured_record_on_one_page_does_not_flag_its_source(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt")
    network.serve(LISTING, listing_of(1, 2))
    network.serve(POSTING.format(1), page_of(record("Doctoral researcher", "HIT-1")))
    network.serve(POSTING.format(2), page_of(record("Laboratory engineer", validThrough="soon")))

    dashboard.post("/sources/hafenstadt/poll")

    page = shown(dashboard.get("/sources/hafenstadt").text)
    assert "not a date" in page
    assert "Unhealthy" not in page
    assert "Healthy" in page
    assert "Unhealthy" not in dashboard.get("/sources").text


def test_a_rate_limit_can_be_told_from_a_page_that_is_gone(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "limited")
    write_source(data_dir, "moved", "https://moved.example/jobs")
    network.serve(LISTING, 429)
    network.serve("https://moved.example/jobs", 404)

    dashboard.post("/poll")

    assert "rate limit" in dashboard.get("/sources/limited").text
    assert "no longer there" in dashboard.get("/sources/moved").text


def test_a_source_that_recovers_on_its_own_is_no_longer_flagged(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt")
    network.serve(LISTING, 503)
    dashboard.post("/sources/hafenstadt/poll")
    serve_two_postings(network)

    dashboard.post("/sources/hafenstadt/poll")

    page = dashboard.get("/sources/hafenstadt").text
    assert "Unhealthy" not in page
    assert "Healthy" in page
    assert "Unhealthy" not in dashboard.get("/sources").text


def test_health_survives_a_restart(
    dashboard: TestClient,
    data_dir: Path,
    network: Pages,
    clock: SettableClock,
    committer: RememberedCommits,
    model: ScriptedModel,
) -> None:
    write_source(data_dir, "hafenstadt", hops=False)
    network.serve(LISTING, EMPTY)
    for _ in range(2):
        dashboard.post("/sources/hafenstadt/poll")

    restarted = TestClient(
        create_app(
            data_dir=data_dir,
            clock=clock,
            network=network,
            committer=committer,
            model=model,
            sleep=lambda seconds: None,
        )
    )
    before = restarted.get("/sources").text
    restarted.post("/sources/hafenstadt/poll")

    assert "Quiet" in before
    assert "Unhealthy" in restarted.get("/sources").text


def test_every_source_shows_when_it_was_last_polled_and_what_it_found(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt")
    write_source(data_dir, "empty", "https://empty.example/jobs", hops=False)
    write_source(data_dir, "waiting", "https://waiting.example/jobs", extra="enabled: false\n")
    serve_two_postings(network)
    network.serve("https://empty.example/jobs", EMPTY)

    dashboard.post("/poll")

    listed = shown(dashboard.get("/sources").text)
    assert "2026-09-18 09:30, 2 Postings" in listed
    assert "2026-09-18 09:30, 0 Postings" in listed
    assert "never" in listed


def test_a_poll_that_fails_outright_is_flagged_at_once(
    dashboard: TestClient, data_dir: Path, network: Pages, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_source(data_dir, "hafenstadt", hops=False)

    def fall_over(url: str) -> Response:
        raise RuntimeError("something nobody foresaw")

    monkeypatch.setattr(network, "fetch", fall_over)

    dashboard.post("/sources/hafenstadt/poll")

    page = shown(dashboard.get("/sources/hafenstadt").text)
    assert "Unhealthy" in page
    assert "something nobody foresaw" in page


def test_a_poll_recorded_before_polls_were_counted_still_says_it_was_empty(
    dashboard: TestClient, data_dir: Path
) -> None:
    write_source(data_dir, "hafenstadt")
    (data_dir / "polls").mkdir()
    (data_dir / "polls" / "hafenstadt.yaml").write_text(
        "polled: 2026-09-17 09:30:00+00:00\npostings: 0\nrejected: 0\nnew: 0\nchanged: 0\n"
        "errors: []\n",
        encoding="utf-8",
    )

    assert "Quiet" in dashboard.get("/sources").text
