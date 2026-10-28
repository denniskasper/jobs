"""The proposal list: the rule filter, its reasons, the daily cap and ageing out, through the HTTP
surface.

Every Posting here is invented, as is every host.
"""

import html
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from starlette.testclient import TestClient

from conftest import NOW, Pages, RememberedCommits, SettableClock
from jobs.postings import KeptPosting, mark
from jobs.proposals import Proposals
from jobs.relevance import Verdict
from test_polling import LISTING, POSTING, kept, listing_of, page_of, record, write_source

DAY = timedelta(days=1)


def write_posting(data_dir: Path, name: str, source: str = "hafenstadt", **fields: Any) -> Path:
    """Place a Posting on disk the way a poll keeps it."""
    record = {
        "company": "Hafenstadt Institute of Technology",
        "role": name.replace("-", " ").capitalize(),
        "location": "Hafenstadt",
        "url": f"https://portal.example/jobs/posting/{name}",
        "first_seen": NOW - timedelta(hours=1),
        "last_seen": NOW - timedelta(hours=1),
        "description": "Research position in experimental fluid dynamics.",
        **fields,
    }
    path = data_dir / "postings" / source / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    return path


def write_settings(data_dir: Path, text: str) -> None:
    (data_dir / "proposals.yaml").write_text(text, encoding="utf-8")


def on_disk(path: Path) -> dict[str, Any]:
    record = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(record, dict)
    return record


def test_a_kept_posting_is_proposed_with_what_deciding_takes(
    dashboard: TestClient, data_dir: Path
) -> None:
    write_posting(
        data_dir,
        "doctoral-researcher",
        deadline=datetime(2026, 10, 31).date(),
        description="Research position in experimental fluid dynamics. " * 40,
    )

    page = dashboard.get("/proposals")

    assert page.status_code == 200
    assert "Hafenstadt Institute of Technology" in page.text
    assert "Doctoral researcher" in page.text
    assert "2026-10-31" in page.text
    assert 'href="https://portal.example/jobs/posting/doctoral-researcher"' in page.text
    assert 'type="checkbox"' in page.text
    assert 'value="hafenstadt/doctoral-researcher"' in page.text
    assert "no rules are set" in page.text


def test_a_proposal_gives_everything_kept_of_its_posting_beside_it(
    dashboard: TestClient, data_dir: Path
) -> None:
    write_posting(
        data_dir,
        "doctoral-researcher",
        identifier="F-26-101-M8",
        posted=datetime(2026, 9, 1).date(),
        deadline=datetime(2026, 10, 31).date(),
        remuneration={"currency": "EUR", "low": "52000", "high": "61000.50", "per": "YEAR"},
    )
    write_posting(data_dir, "laboratory-engineer", remuneration={"low": "4200", "high": "4200"})

    page = dashboard.get("/proposals")

    assert "F-26-101-M8" in page.text
    assert "2026-09-01" in page.text
    assert "EUR 52000 to 61000.50 per year" in page.text
    assert ">4200<" in page.text
    assert 'href="http://testserver/sources/hafenstadt"' in page.text
    # When it was first found, which is not when it was first shown.
    assert "2026-09-18 08:30" in page.text


def test_the_whole_description_is_there_to_open_behind_a_short_extract(
    dashboard: TestClient, data_dir: Path
) -> None:
    sentences = [f"Sentence number {number} of the description." for number in range(60)]
    write_posting(data_dir, "doctoral-researcher", description="\n\n".join(sentences))

    page = dashboard.get("/proposals")

    assert "<details" in page.text
    assert page.text.count("Sentence number 0 of") == 2
    assert page.text.count("Sentence number 59 of") == 1


def test_a_short_description_is_given_whole_with_nothing_to_open(
    dashboard: TestClient, data_dir: Path
) -> None:
    write_posting(data_dir, "doctoral-researcher")

    page = dashboard.get("/proposals")

    assert page.text.count("Research position in experimental fluid dynamics.") == 1
    assert "<details" not in page.text


def test_with_no_cap_set_every_posting_is_shown_at_once(
    dashboard: TestClient, data_dir: Path
) -> None:
    paths = [write_posting(data_dir, f"position-{number}") for number in range(12)]

    page = dashboard.get("/proposals")

    assert page.text.count('type="checkbox"') == 12
    assert "held back" not in page.text
    assert all("first_shown" in on_disk(path) for path in paths)


def test_the_first_time_a_posting_appears_is_written_on_it_and_committed(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    path = write_posting(data_dir, "doctoral-researcher")

    dashboard.get("/proposals")

    assert on_disk(path)["first_shown"] == NOW
    [commit] = committer.commits
    assert commit.message == "Show 1 Posting"
    assert commit.paths == [path]


def test_first_shown_is_never_written_over(
    dashboard: TestClient, data_dir: Path, clock: SettableClock, committer: RememberedCommits
) -> None:
    path = write_posting(data_dir, "doctoral-researcher")
    dashboard.get("/proposals")

    clock.now = NOW + timedelta(days=3)
    page = dashboard.get("/proposals")

    assert "Doctoral researcher" in page.text
    assert on_disk(path)["first_shown"] == NOW
    assert len(committer.commits) == 1


def test_a_moment_already_written_is_not_written_over(data_dir: Path) -> None:
    path = write_posting(data_dir, "doctoral-researcher", first_shown=NOW)

    written = mark(data_dir, "hafenstadt", "doctoral-researcher", "first_shown", NOW + DAY)

    assert written is None
    assert on_disk(path)["first_shown"] == NOW


def test_what_was_shown_on_earlier_days_takes_no_room_under_todays_cap(
    dashboard: TestClient, data_dir: Path, clock: SettableClock
) -> None:
    write_settings(data_dir, "cap: 1\n")
    write_posting(data_dir, "position-a", first_seen=NOW - timedelta(hours=3))
    write_posting(data_dir, "position-b", first_seen=NOW - timedelta(hours=2))
    dashboard.get("/proposals")

    clock.now = NOW + DAY
    page = dashboard.get("/proposals")

    # Yesterday's is still undecided and stays, beside the one the cap lets through today.
    assert "Position a" in page.text
    assert "Position b" in page.text


def test_the_list_is_capped_per_day_and_the_rest_is_held_back_unshown(
    dashboard: TestClient, data_dir: Path
) -> None:
    write_settings(data_dir, "cap: 2\n")
    paths = [write_posting(data_dir, f"position-{number}") for number in range(5)]

    page = dashboard.get("/proposals")

    assert page.text.count('type="checkbox"') == 2
    assert "3 more are held back" in page.text
    assert sum("first_shown" in on_disk(path) for path in paths) == 2
    # Asking again the same day lets no more through.
    assert dashboard.get("/proposals").text.count('type="checkbox"') == 2
    assert sum("first_shown" in on_disk(path) for path in paths) == 2


def test_dismissing_todays_proposals_makes_no_room_under_the_cap(
    dashboard: TestClient, data_dir: Path
) -> None:
    write_settings(data_dir, "cap: 1\n")
    write_posting(data_dir, "position-a", first_seen=NOW - timedelta(hours=3))
    held_back = write_posting(data_dir, "position-b", first_seen=NOW - timedelta(hours=2))
    dashboard.get("/proposals")

    dashboard.post("/proposals/hafenstadt/position-a/dismiss")

    assert "first_shown" not in on_disk(held_back)


def test_what_was_held_back_comes_before_what_was_found_since(
    dashboard: TestClient, data_dir: Path, clock: SettableClock
) -> None:
    write_settings(data_dir, "cap: 1\n")
    write_posting(data_dir, "position-b", first_seen=NOW - timedelta(hours=3))
    held_back = write_posting(
        data_dir, "position-c", source="zweistadt", first_seen=NOW - timedelta(hours=2)
    )
    dashboard.get("/proposals")

    clock.now = NOW + timedelta(days=1)
    # Neither its name nor its Source puts what was found since behind what was held back.
    found_since = write_posting(data_dir, "position-a", first_seen=clock.now)
    page = dashboard.get("/proposals")

    assert "Position c" in page.text
    assert "Position a" not in page.text
    assert on_disk(held_back)["first_shown"] == clock.now
    assert "first_shown" not in on_disk(found_since)


def test_an_unticked_posting_ages_out_a_period_after_it_was_first_shown(
    dashboard: TestClient, data_dir: Path, clock: SettableClock
) -> None:
    write_settings(data_dir, "age_out_days: 7\n")
    # Found long ago, which starts no clock.
    path = write_posting(data_dir, "doctoral-researcher", first_seen=NOW - timedelta(days=60))
    assert 'Ages out</dt><dd class="mono">2026-09-25' in dashboard.get("/proposals").text

    clock.now = NOW + timedelta(days=6, hours=23)
    assert "Doctoral researcher" in dashboard.get("/proposals").text

    clock.now = NOW + timedelta(days=7)
    assert "Doctoral researcher" not in dashboard.get("/proposals").text
    # It stays in the record, and can be looked at.
    assert on_disk(path)["role"] == "Doctoral researcher"
    left_out = dashboard.get("/proposals/left-out")
    assert "Doctoral researcher" in left_out.text
    assert "Aged out" in left_out.text
    assert "within 7 days of 2026-09-18, when it was first shown" in left_out.text


def test_a_posting_held_back_for_longer_than_the_period_is_still_shown(
    dashboard: TestClient, data_dir: Path, clock: SettableClock
) -> None:
    write_settings(data_dir, "cap: 1\nage_out_days: 2\n")
    write_posting(data_dir, "position-a", first_seen=NOW - timedelta(hours=3))
    write_posting(data_dir, "position-b", first_seen=NOW - timedelta(hours=2))
    write_posting(data_dir, "position-c", first_seen=NOW - timedelta(hours=1))
    dashboard.get("/proposals")
    clock.now = NOW + timedelta(days=1)
    dashboard.get("/proposals")

    clock.now = NOW + timedelta(days=5)
    page = dashboard.get("/proposals")

    assert "Position c" in page.text
    assert 'Ages out</dt><dd class="mono">2026-09-25' in page.text


def test_a_dismissed_posting_leaves_the_list_and_the_decision_is_recorded(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    path = write_posting(data_dir, "doctoral-researcher")
    dashboard.get("/proposals")

    dismissed = dashboard.post("/proposals/hafenstadt/doctoral-researcher/dismiss")

    assert dismissed.status_code == 200
    assert "Doctoral researcher" not in dismissed.text
    assert on_disk(path)["dismissed"] == NOW
    assert committer.commits[-1].message == (
        "Dismiss Doctoral researcher at Hafenstadt Institute of Technology"
    )
    assert committer.commits[-1].paths == [path]
    left_out = dashboard.get("/proposals/left-out")
    assert "Doctoral researcher" in left_out.text
    assert "Dismissed on 2026-09-18" in left_out.text


def test_dismissing_a_posting_that_is_not_proposed_is_refused(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    write_posting(data_dir, "doctoral-researcher")

    refused = dashboard.post("/proposals/hafenstadt/no-such-posting/dismiss")

    assert refused.status_code == 404
    assert "No Posting hafenstadt/no-such-posting is proposed" in refused.text
    assert committer.commits == []


def test_the_global_rules_remove_postings_and_say_why(
    dashboard: TestClient, data_dir: Path
) -> None:
    write_settings(
        data_dir,
        "locations: [Hafenstadt, remote]\ninclude: [fluid dynamics]\nexclude: [sales]\n",
    )
    write_posting(data_dir, "doctoral-researcher")
    elsewhere = write_posting(data_dir, "laboratory-engineer", location="Bergdorf")
    write_posting(data_dir, "sales-engineer", description="Fluid dynamics sales.")
    write_posting(data_dir, "accountant", description="Bookkeeping for the institute.")

    page = dashboard.get("/proposals")

    assert page.text.count('type="checkbox"') == 1
    assert "it mentions fluid dynamics and its location matches Hafenstadt" in page.text
    # What the rules removed was never shown.
    assert "first_shown" not in on_disk(elsewhere)
    left_out = dashboard.get("/proposals/left-out").text
    assert "its location, Bergdorf, is none of Hafenstadt, remote" in left_out
    assert "it mentions sales, which is excluded" in left_out
    assert "it mentions none of fluid dynamics" in left_out


def test_what_a_sources_filter_rules_removed_is_not_proposed_and_shows_their_reason(
    dashboard: TestClient, data_dir: Path
) -> None:
    write_posting(
        data_dir, "laboratory-engineer", rejected="its identifier, 102, does not match M8$"
    )

    assert "Laboratory engineer" not in dashboard.get("/proposals").text
    left_out = dashboard.get("/proposals/left-out").text
    assert "Laboratory engineer" in left_out
    assert "its identifier, 102, does not match M8$" in left_out


def test_relaxing_a_rule_brings_back_what_it_removed(dashboard: TestClient, data_dir: Path) -> None:
    write_settings(data_dir, "locations: [Hafenstadt]\n")
    write_posting(data_dir, "laboratory-engineer", location="Bergdorf")
    assert "Laboratory engineer" not in dashboard.get("/proposals").text

    write_settings(data_dir, "locations: [Hafenstadt, Bergdorf]\n")

    assert "Laboratory engineer" in dashboard.get("/proposals").text


def test_a_posting_past_its_deadline_is_not_proposed(dashboard: TestClient, data_dir: Path) -> None:
    path = write_posting(data_dir, "doctoral-researcher", deadline=(NOW - timedelta(days=1)).date())

    assert "Doctoral researcher" not in dashboard.get("/proposals").text
    assert "first_shown" not in on_disk(path)
    assert "Its Deadline passed on 2026-09-17" in dashboard.get("/proposals/left-out").text


def test_settings_that_cannot_be_read_are_reported_and_nothing_is_shown(
    dashboard: TestClient, data_dir: Path
) -> None:
    write_settings(data_dir, "cap: many\n")
    path = write_posting(data_dir, "doctoral-researcher")

    page = dashboard.get("/proposals")

    assert "proposals.yaml" in page.text
    assert "cap is not a whole number" in page.text
    assert "first_shown" not in on_disk(path)


def test_looking_at_what_is_not_proposed_shows_nothing_for_the_first_time(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    path = write_posting(data_dir, "doctoral-researcher")

    dashboard.get("/proposals/left-out")

    assert "first_shown" not in on_disk(path)
    assert committer.commits == []


def test_a_poll_leaves_first_shown_where_it_is(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    write_source(data_dir, "hafenstadt")
    network.serve(LISTING, listing_of(101))
    network.serve(POSTING.format(101), page_of(record("Doctoral researcher", "101")))
    dashboard.post("/sources/hafenstadt/poll")
    dashboard.get("/proposals")

    dashboard.post("/sources/hafenstadt/poll")

    [posting] = kept(data_dir, "hafenstadt")
    assert posting["first_shown"] == NOW
    assert "Doctoral researcher" in dashboard.get("/proposals").text


def test_the_proposals_are_a_tab_beside_the_others(dashboard: TestClient) -> None:
    assert 'href="http://testserver/proposals"' in dashboard.get("/").text


# Every page with tabs but the one that shows the list.
ELSEWHERE = ["/", "/proposals/left-out", "/documents", "/sources"]


def new_on(page: str) -> tuple[str, str] | None:
    """What the Proposals tab says is new: the number it shows, and its words for a screen
    reader."""
    (tab,) = re.findall(r'<a class="tab[^"]*"[^>]*>\s*Proposals(.*?)</a>', page, re.DOTALL)
    found = re.search(
        r'<span class="badge badge-todo tab-marker tab-count">\s*'
        r'<span aria-hidden="true">(\d+)</span>\s*'
        r'<span class="visually-hidden">([^<]+)</span>\s*</span>',
        tab,
    )
    return (found[1], html.unescape(found[2]).strip()) if found else None


def test_what_the_list_would_show_for_the_first_time_is_counted_on_its_tab(
    dashboard: TestClient, data_dir: Path
) -> None:
    first = write_posting(data_dir, "doctoral-researcher")
    write_posting(data_dir, "laboratory-engineer")

    for path in ELSEWHERE:
        assert new_on(dashboard.get(path).text) == ("2", "2 new Proposals")
    # Counting them shows none of them.
    assert "first_shown" not in on_disk(first)

    # The page that shows them is the one that says so: the tab has nothing to add.
    assert new_on(dashboard.get("/proposals").text) is None
    for path in ELSEWHERE:
        assert new_on(dashboard.get(path).text) is None


def test_one_new_proposal_is_counted_in_the_singular(dashboard: TestClient, data_dir: Path) -> None:
    write_posting(data_dir, "doctoral-researcher")

    assert new_on(dashboard.get("/").text) == ("1", "1 new Proposal")


def test_what_the_cap_holds_back_is_not_new_until_the_day_it_would_be_shown(
    dashboard: TestClient, data_dir: Path, clock: SettableClock
) -> None:
    write_settings(data_dir, "cap: 1\n")
    write_posting(data_dir, "doctoral-researcher")
    write_posting(data_dir, "laboratory-engineer")

    assert new_on(dashboard.get("/").text) == ("1", "1 new Proposal")
    dashboard.get("/proposals")
    assert new_on(dashboard.get("/").text) is None

    clock.now = NOW + DAY
    assert new_on(dashboard.get("/").text) == ("1", "1 new Proposal")


def test_what_the_rules_remove_is_not_new(dashboard: TestClient, data_dir: Path) -> None:
    write_settings(data_dir, "exclude: [sales]\n")
    write_posting(data_dir, "sales-engineer", description="Fluid dynamics sales.")

    page = dashboard.get("/").text

    assert new_on(page) is None
    assert re.search(r'<a class="tab[^"]*"[^>]*>Proposals</a>', page)


class OnlyEngineers:
    """Stands for the scorer of a later spec: anything that judges a kept Posting."""

    def judge(self, posting: KeptPosting) -> Verdict:
        if "engineer" in posting.role:
            return Verdict(True, "it scored 0.9 against the profile")
        return Verdict(False, "it scored 0.1 against the profile")


def test_a_scorer_takes_the_place_of_the_rules_without_the_list_changing(
    data_dir: Path, clock: SettableClock, committer: RememberedCommits
) -> None:
    write_posting(data_dir, "laboratory-engineer")
    write_posting(data_dir, "accountant")

    listing = Proposals(data_dir, committer, clock, relevance=OnlyEngineers()).show()

    [proposal] = listing.proposals
    assert proposal.posting.role == "Laboratory engineer"
    assert proposal.reason == "it scored 0.9 against the profile"
    [left_out] = listing.left_out
    assert left_out.reason.endswith("it scored 0.1 against the profile")
