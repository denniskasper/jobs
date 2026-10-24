"""A Registration from handing in the URL to its end, through the HTTP surface: Register answers
at once, and the Sources page says how it went whenever the user comes to look.

Every page here is invented, as is every host.
"""

import html
import json
import re
import threading
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from starlette.testclient import TestClient

from conftest import (
    Pages,
    RememberedCommits,
    ScriptedModel,
    SettableClock,
    run_next_registration,
    try_again,
)
from jobs.registrations import on_a_daemon_thread
from jobs.web import create_app
from test_sources import CAREERS, POSTING, SHELL, posting_page


def hand_in(dashboard: TestClient, url: str, label: str = "", rule: str = "") -> None:
    """Press Register and look at nothing yet: the Sources page it leads to is not asked for."""
    dashboard.post(
        "/sources", data={"url": url, "label": label, "rule": rule}, follow_redirects=False
    )


def test_register_answers_at_once_with_a_redirect_to_the_sources_page(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    answered = dashboard.post(
        "/sources",
        data={"url": POSTING.format(101), "label": "Hafenstadt", "rule": ""},
        follow_redirects=False,
    )

    assert answered.status_code == 303
    assert answered.headers["location"] == "http://testserver/sources"
    assert network.fetched == []
    assert model.asked == []
    assert not (data_dir / "sources").exists()


def registrations_on(page: str) -> list[str]:
    """The lines of the list under "Register a Source", as the words a reader gets."""
    found = re.search(r'<ul id="registrations"[^>]*>(.*?)</ul>', page, re.DOTALL)
    if not found:
        return []
    return [
        " ".join(html.unescape(re.sub(r"<[^>]+>", " ", line)).split())
        for line in re.findall(r"<li\b[^>]*>(.*?)</li>", found[1], re.DOTALL)
    ]


def test_a_registration_handed_in_is_listed_as_not_yet_finished(dashboard: TestClient) -> None:
    hand_in(dashboard, POSTING.format(101), label="Hafenstadt")
    hand_in(dashboard, POSTING.format(102))

    # Looking elsewhere, and looking twice, loses nothing.
    assert dashboard.get("/").status_code == 200
    dashboard.get("/sources")
    page = dashboard.get("/sources").text

    assert registrations_on(page) == [
        "Hafenstadt Waiting next in line",
        f"{POSTING.format(102)} Waiting 1 ahead",
    ]
    assert page.index('id="registrations"') < page.index('action="http://testserver/sources"')
    assert page.index("Register a Source") < page.index('id="registrations"')


def test_a_registration_that_has_run_ends_in_a_committed_source_and_says_so_once(
    dashboard: TestClient, data_dir: Path, network: Pages, committer: RememberedCommits
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    hand_in(dashboard, POSTING.format(101), label="Hafenstadt")

    run_next_registration(dashboard)

    path = data_dir / "sources" / "hafenstadt.yaml"
    assert [(commit.message, commit.paths) for commit in committer.commits] == [
        ("Register Source hafenstadt", [path])
    ]
    assert [each for each in data_dir.rglob("*") if each.is_file()] == [path]
    # Applications in between: it is the Sources page that counts as having shown it.
    assert dashboard.get("/").status_code == 200
    page = dashboard.get("/sources").text
    assert registrations_on(page) == ["Hafenstadt Registered Check its config"]
    assert 'href="http://testserver/sources/hafenstadt">Check its config' in page
    assert registrations_on(dashboard.get("/sources").text) == []
    assert "Hafenstadt" in dashboard.get("/sources").text


def test_what_needs_neither_network_nor_model_is_refused_on_the_form_at_once(
    dashboard: TestClient, data_dir: Path, network: Pages
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    hand_in(dashboard, POSTING.format(101), label="Hafenstadt")
    run_next_registration(dashboard)
    dashboard.get("/sources")
    network.fetched.clear()

    for url, label, reason in [
        ("file:///etc/passwd", "Harbour", "has to be a web address"),
        ("https://例え.テスト/", "", "needs a label"),
        (POSTING.format(102), "Hafenstadt", "already a Source called hafenstadt"),
    ]:
        refused = dashboard.post("/sources", data={"url": url, "label": label, "rule": "M8 only"})

        assert refused.status_code in (409, 422)
        assert reason in refused.text
        assert f'value="{url}"' in refused.text
        assert 'value="M8 only"' in refused.text
        assert registrations_on(refused.text) == []
        assert registrations_on(dashboard.get("/sources").text) == []
    assert network.fetched == []


def test_with_the_real_runner_a_registration_ends_in_a_source_by_itself(
    data_dir: Path,
    clock: SettableClock,
    network: Pages,
    committer: RememberedCommits,
    model: ScriptedModel,
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    ended = threading.Event()

    def runner(work: Callable[[], None]) -> None:
        """The real one, which also says when what it was handed is done."""

        def work_and_say_so() -> None:
            work()
            ended.set()

        on_a_daemon_thread(work_and_say_so)

    dashboard = TestClient(
        create_app(
            data_dir=data_dir,
            clock=clock,
            network=network,
            committer=committer,
            model=model,
            sleep=lambda seconds: None,
            runner=runner,
        )
    )

    hand_in(dashboard, POSTING.format(101), label="Hafenstadt")

    assert ended.wait(timeout=10.0)
    assert (data_dir / "sources" / "hafenstadt.yaml").exists()
    assert registrations_on(dashboard.get("/sources").text) == [
        "Hafenstadt Registered Check its config"
    ]


BAKERY = "https://bakery.example/"
NO_WAY = "No way to extract Postings from https://bakery.example/ was found"


def refuse_the_bakery(dashboard: TestClient, network: Pages, model: ScriptedModel) -> None:
    """Hand in a URL that leads to no Posting, with a label and a filter rule, and explore it."""
    network.serve(BAKERY, "<html><body><a href='/bread'>Our bread</a></body></html>")
    model.answers.append(json.dumps({"nothing": "the page is about bread, not about positions"}))
    hand_in(dashboard, BAKERY, label="Bakery & Sons", rule='only "rye" positions')
    run_next_registration(dashboard)


def test_a_registration_refused_in_the_background_is_shown_with_its_reason(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    refuse_the_bakery(dashboard, network, model)
    # The model has no answer left, so it cannot be asked.
    network.serve(POSTING.format(101), "<html><body><a href='/about'>About</a></body></html>")
    hand_in(dashboard, POSTING.format(101), label="Hafenstadt")
    run_next_registration(dashboard)
    # No browser to read this one in, so there is no Posting to try the rule on.
    network.serve(CAREERS, SHELL)
    model.answers = [json.dumps({"browser": True})]
    hand_in(dashboard, CAREERS, label="Careers", rule="only nice teams")
    run_next_registration(dashboard)

    bakery, hafenstadt, careers = registrations_on(dashboard.get("/sources").text)

    assert bakery.startswith("Bakery & Sons Refused")
    assert NO_WAY in bakery
    assert "the page is about bread" in bakery
    assert hafenstadt.startswith("Hafenstadt Refused")
    assert "could not be asked" in hafenstadt
    assert careers.startswith("Careers Refused")
    assert "no Posting could be read to try it on" in careers
    assert list(data_dir.iterdir()) == []


def test_a_refused_registration_stays_however_many_pages_are_visited(
    dashboard: TestClient, network: Pages, model: ScriptedModel
) -> None:
    refuse_the_bakery(dashboard, network, model)

    for path in ["/sources", "/", "/sources", "/proposals", "/documents", "/sources"]:
        assert dashboard.get(path).status_code == 200

    (bakery,) = registrations_on(dashboard.get("/sources").text)
    assert NO_WAY in bakery


def clear_of(page: str, title: str) -> str:
    """Where the Clear button of the Registration with this title hands its form in."""
    found = re.search(
        rf'<li\b[^>]*>\s*<span class="registration-title">{re.escape(html.escape(title))}</span>'
        r'.*?<form method="post" action="([^"]+)"',
        page,
        re.DOTALL,
    )
    assert found
    return found[1]


def test_a_source_that_could_not_be_committed_is_registered_and_says_so_until_cleared(
    dashboard: TestClient, network: Pages, committer: RememberedCommits
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    committer.commit_failure = "the index is locked"
    hand_in(dashboard, POSTING.format(101), label="Hafenstadt")

    run_next_registration(dashboard)

    # Not refused: the Source was written, and trying again would find its name taken.
    dashboard.get("/sources")
    page = dashboard.get("/sources").text
    (hafenstadt,) = registrations_on(page)
    assert hafenstadt.startswith("Hafenstadt Registered Check its config Clear")
    assert "hafenstadt was written but could not be committed: the index is locked" in hafenstadt
    assert "Try again" not in page
    assert "Hafenstadt" in dashboard.get("/sources/hafenstadt").text

    dashboard.post(clear_of(page, "Hafenstadt"))

    assert registrations_on(dashboard.get("/sources").text) == []


def test_clear_removes_that_one_registration_and_leaves_the_others(
    dashboard: TestClient, network: Pages, model: ScriptedModel
) -> None:
    refuse_the_bakery(dashboard, network, model)
    hand_in(dashboard, "https://unreachable.example/jobs", label="Unreachable")
    run_next_registration(dashboard)
    hand_in(dashboard, POSTING.format(102), label="Still waiting")
    page = dashboard.get("/sources").text

    cleared = dashboard.post(clear_of(page, "Bakery & Sons"), follow_redirects=False)

    assert cleared.status_code == 303
    assert cleared.headers["location"] == "http://testserver/sources"
    unreachable, waiting = registrations_on(dashboard.get("/sources").text)
    assert unreachable.startswith("Unreachable Refused")
    assert waiting == "Still waiting Waiting next in line"


def test_clearing_what_is_not_there_does_no_harm(
    dashboard: TestClient, network: Pages, model: ScriptedModel
) -> None:
    refuse_the_bakery(dashboard, network, model)
    hand_in(dashboard, "https://unreachable.example/jobs", label="Unreachable")
    run_next_registration(dashboard)
    clear = clear_of(dashboard.get("/sources").text, "Bakery & Sons")
    dashboard.post(clear)

    again = dashboard.post(clear, follow_redirects=False)
    never_was = dashboard.post("/registrations/4711/clear", follow_redirects=False)

    assert again.status_code == never_was.status_code == 303
    (unreachable,) = registrations_on(dashboard.get("/sources").text)
    assert unreachable.startswith("Unreachable Refused")


def test_one_that_has_not_ended_cannot_be_cleared(dashboard: TestClient) -> None:
    hand_in(dashboard, POSTING.format(101), label="Hafenstadt")
    page = dashboard.get("/sources").text
    assert "Clear" not in page
    assert "Try again" not in page

    # Its identifier guessed: there is no cancelling, and hiding the line would be a lie.
    for guessed in range(5):
        dashboard.post(f"/registrations/{guessed}/clear")

    assert registrations_on(dashboard.get("/sources").text) == ["Hafenstadt Waiting next in line"]


def test_try_again_shows_the_form_as_it_was_entered_and_does_nothing_else(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    refuse_the_bakery(dashboard, network, model)
    network.fetched.clear()

    form = try_again(dashboard, dashboard.get("/sources").text)

    assert f'name="url" type="url" required value="{BAKERY}"' in form
    assert 'name="label" value="Bakery &amp; Sons"' in form
    assert 'name="rule" value="only &#34;rye&#34; positions"' in form
    assert "Not registered" not in form
    # Nothing is registered until Register is pressed, and the refusal is still there.
    assert network.fetched == []
    assert list(data_dir.iterdir()) == []
    (bakery,) = registrations_on(form)
    assert NO_WAY in bakery


HOSTS = {"Anker": "https://anker.example/", "Boje": "https://boje.example/"}


def test_three_registrations_are_explored_in_the_order_handed_in_and_never_two_at_once(
    dashboard: TestClient, network: Pages
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    looked: list[list[str]] = []
    # From inside every fetch: which one is under way, and what the others are told.
    network.when_fetched = lambda url: looked.append(
        registrations_on(dashboard.get("/sources").text)
    )
    hand_in(dashboard, HOSTS["Anker"], label="Anker")
    hand_in(dashboard, POSTING.format(101), label="Hafenstadt")
    hand_in(dashboard, HOSTS["Boje"], label="Boje")

    for _ in range(3):
        run_next_registration(dashboard)

    assert [url for url in network.fetched if url in (*HOSTS.values(), POSTING.format(101))] == [
        HOSTS["Anker"],
        POSTING.format(101),
        HOSTS["Boje"],
    ]
    assert looked[0] == [
        "Anker Under way for 0 s",
        "Hafenstadt Waiting 1 ahead",
        "Boje Waiting 2 ahead",
    ]
    assert all(sum("Under way" in line for line in lines) == 1 for lines in looked)
    # The refused one in front held nobody up, and the one behind was told it had moved up.
    hafenstadt = [lines for lines in looked if "Hafenstadt Under way for 0 s" in lines][0]
    assert hafenstadt[0].startswith("Anker Refused")
    assert hafenstadt[2] == "Boje Waiting 1 ahead"
    assert "Hafenstadt Registered Check its config" in looked[-1]
    # That look was the user seeing it, so only the refused ones are left.
    anker, boje = registrations_on(dashboard.get("/sources").text)
    assert anker.startswith("Anker Refused")
    assert boje.startswith("Boje Refused")


def test_the_one_under_way_shows_for_how_long(
    dashboard: TestClient, network: Pages, clock: SettableClock
) -> None:
    looked: list[list[str]] = []

    def look_now_and_later(url: str) -> None:
        for passed in [timedelta(seconds=42), timedelta(seconds=83), timedelta(0)]:
            looked.append(registrations_on(dashboard.get("/sources").text))
            clock.now += passed

    network.when_fetched = look_now_and_later
    hand_in(dashboard, HOSTS["Anker"], label="Anker")
    # Waiting is not running: only what passes once it has started counts.
    clock.now += timedelta(minutes=7)

    run_next_registration(dashboard)

    assert looked[:3] == [
        ["Anker Under way for 0 s"],
        ["Anker Under way for 42 s"],
        ["Anker Under way for 2 min 5 s"],
    ]


def test_a_name_that_a_registration_not_yet_ended_claims_is_taken(
    dashboard: TestClient, network: Pages
) -> None:
    def refused_with_the_input_kept(url: str) -> None:
        refused = dashboard.post("/sources", data={"url": url, "label": "anker", "rule": "M8 only"})
        assert refused.status_code == 409
        assert "A Registration that has not ended yet already claims the name anker" in refused.text
        assert f'value="{url}"' in refused.text
        assert 'value="M8 only"' in refused.text
        assert len(registrations_on(refused.text)) == 1

    hand_in(dashboard, HOSTS["Anker"], label="Anker")
    # While it waits: another URL under the same name, and Register pressed twice.
    refused_with_the_input_kept(HOSTS["Boje"])
    refused_with_the_input_kept(HOSTS["Anker"])
    # While it is under way.
    network.when_fetched = lambda url: refused_with_the_input_kept(HOSTS["Boje"])

    run_next_registration(dashboard)

    assert network.fetched == [HOSTS["Anker"]]
    network.when_fetched = None
    # It was refused, so nothing claims the name any more.
    hand_in(dashboard, HOSTS["Boje"], label="anker")
    anker, again = registrations_on(dashboard.get("/sources").text)
    assert anker.startswith("Anker Refused")
    assert again == "anker Waiting next in line"


def test_with_the_real_runner_several_handed_in_all_end_without_anything_else_asked_for(
    data_dir: Path,
    clock: SettableClock,
    network: Pages,
    committer: RememberedCommits,
    model: ScriptedModel,
) -> None:
    for number in (101, 102, 103):
        network.serve(
            POSTING.format(number),
            posting_page(str(number), "Doctoral researcher", f"F-26-{number}-M8"),
        )
    ended = threading.Semaphore(0)

    def runner(work: Callable[[], None]) -> None:
        def work_and_say_so() -> None:
            work()
            ended.release()

        on_a_daemon_thread(work_and_say_so)

    dashboard = TestClient(
        create_app(
            data_dir=data_dir,
            clock=clock,
            network=network,
            committer=committer,
            model=model,
            sleep=lambda seconds: None,
            runner=runner,
        )
    )

    # The first is held in its fetch. Were two explored at once, another would get to its own.
    held, let_go, another_started = threading.Event(), threading.Event(), threading.Event()

    def hold_the_first(url: str) -> None:
        if url == POSTING.format(101):
            held.set()
            let_go.wait(timeout=10.0)
        else:
            another_started.set()

    network.when_fetched = hold_the_first

    for number, label in [(101, "Anker"), (102, "Boje"), (103, "Hafenstadt")]:
        hand_in(dashboard, POSTING.format(number), label=label)

    assert held.wait(timeout=10.0)
    assert not another_started.wait(timeout=0.3)
    assert registrations_on(dashboard.get("/sources").text) == [
        "Anker Under way for 0 s",
        "Boje Waiting 1 ahead",
        "Hafenstadt Waiting 2 ahead",
    ]
    let_go.set()
    assert all(ended.acquire(timeout=10.0) for _ in range(3))
    assert [commit.message for commit in committer.commits] == [
        "Register Source anker",
        "Register Source boje",
        "Register Source hafenstadt",
    ]
    assert registrations_on(dashboard.get("/sources").text) == [
        "Anker Registered Check its config",
        "Boje Registered Check its config",
        "Hafenstadt Registered Check its config",
    ]


# The few lines that reload the Sources page, by the name the page gives them.
RELOADS = 'id="reloads-itself"'


def test_the_sources_page_reloads_itself_only_while_one_is_waiting_or_under_way(
    dashboard: TestClient, network: Pages, model: ScriptedModel
) -> None:
    carried: list[bool] = []
    network.when_fetched = lambda url: carried.append(RELOADS in dashboard.get("/sources").text)
    assert RELOADS not in dashboard.get("/sources").text

    hand_in(dashboard, HOSTS["Anker"], label="Anker")
    assert RELOADS in dashboard.get("/sources").text

    # Anker is explored, from inside which it is under way, and the bakery still waits.
    refuse_the_bakery(dashboard, network, model)
    assert carried == [True]
    assert RELOADS in dashboard.get("/sources").text
    run_next_registration(dashboard)

    # Two refused ones are left to read, and an idle page stays idle.
    assert len(registrations_on(dashboard.get("/sources").text)) == 2
    assert RELOADS not in dashboard.get("/sources").text


def test_no_other_page_reloads_itself(
    dashboard: TestClient, network: Pages, model: ScriptedModel
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    hand_in(dashboard, POSTING.format(101), label="Hafenstadt")
    run_next_registration(dashboard)
    hand_in(dashboard, HOSTS["Anker"], label="Anker")

    assert RELOADS in dashboard.get("/sources").text
    for path in ["/", "/proposals", "/documents", "/sources/hafenstadt"]:
        page = dashboard.get(path)
        assert page.status_code == 200
        assert RELOADS not in page.text
    # A Source that is not there is answered with the Sources page, which is not at its address.
    assert RELOADS not in dashboard.get("/sources/nowhere").text


def test_a_page_with_a_refusal_on_the_form_does_not_reload_itself(dashboard: TestClient) -> None:
    hand_in(dashboard, HOSTS["Anker"], label="Anker")

    # Not reached by GET, and it holds what was entered, which a reload would take away.
    refused = dashboard.post("/sources", data={"url": "anker", "label": "Anker", "rule": ""})

    assert "Not registered." in refused.text
    assert RELOADS not in refused.text


def test_a_reload_the_page_made_by_itself_does_not_count_as_seen(
    dashboard: TestClient, network: Pages
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    hand_in(dashboard, POSTING.format(101), label="Hafenstadt")
    hand_in(dashboard, HOSTS["Anker"], label="Anker")
    run_next_registration(dashboard)

    # Turn after turn with perhaps nobody looking, because the second is still waiting.
    for _ in range(3):
        page = dashboard.get("/sources", params={"by-itself": "1"}).text
        assert registrations_on(page)[0] == "Hafenstadt Registered Check its config"
        assert marker_on(page) == NEUTRAL

    # The user comes to look: shown once more, and gone after that.
    assert registrations_on(dashboard.get("/sources").text)[0].startswith("Hafenstadt Registered")
    assert registrations_on(dashboard.get("/sources").text) == ["Anker Waiting next in line"]


TABBED = ["/", "/proposals", "/proposals/left-out", "/documents", "/sources"]
# The dot of the badge it borrows, and its words for a screen reader.
NEUTRAL = ("closed", "A Registration is waiting or under way, or has ended in a Source")
ALERT = ("todo", "A Registration has ended with something to read")


def marker_on(page: str) -> tuple[str, str] | None:
    """The marker the Sources tab carries: its dot, and its words for a screen reader."""
    (tab,) = re.findall(r'<a class="tab[^"]*"[^>]*>\s*Sources(.*?)</a>', page, re.DOTALL)
    found = re.search(
        r'<span class="badge badge-(\w+) tab-marker">\s*'
        r'<span class="visually-hidden">([^<]+)</span>\s*</span>',
        tab,
    )
    return (found[1], html.unescape(found[2]).strip()) if found else None


def test_with_a_registration_waiting_or_under_way_every_page_marks_the_sources_tab(
    dashboard: TestClient, network: Pages
) -> None:
    under_way: list[tuple[str, str] | None] = []
    network.when_fetched = lambda url: under_way.extend(
        marker_on(dashboard.get(path).text) for path in TABBED
    )
    hand_in(dashboard, HOSTS["Anker"], label="Anker")

    for path in TABBED:
        assert marker_on(dashboard.get(path).text) == NEUTRAL

    run_next_registration(dashboard)
    assert under_way == [NEUTRAL] * len(TABBED)


def test_one_registered_and_not_yet_seen_marks_the_tab_until_the_sources_page_is_shown(
    dashboard: TestClient, network: Pages
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    hand_in(dashboard, POSTING.format(101), label="Hafenstadt")
    run_next_registration(dashboard)

    assert marker_on(dashboard.get("/").text) == NEUTRAL

    # The page that shows it to the user is the one that says so: the tab has nothing to add.
    looked = dashboard.get("/sources").text
    assert registrations_on(looked) == ["Hafenstadt Registered Check its config"]
    assert marker_on(looked) is None
    assert marker_on(dashboard.get("/").text) is None


def test_a_refusal_not_cleared_marks_the_tab_as_something_to_read_whatever_else_is_going_on(
    dashboard: TestClient, network: Pages, model: ScriptedModel
) -> None:
    refuse_the_bakery(dashboard, network, model)
    under_way: list[tuple[str, str] | None] = []
    network.when_fetched = lambda url: under_way.append(marker_on(dashboard.get("/").text))

    for path in TABBED:
        assert marker_on(dashboard.get(path).text) == ALERT

    hand_in(dashboard, HOSTS["Anker"], label="Anker")
    assert marker_on(dashboard.get("/").text) == ALERT
    run_next_registration(dashboard)
    assert under_way == [ALERT]


def test_a_source_that_could_not_be_committed_marks_the_tab_until_the_last_is_cleared(
    dashboard: TestClient, network: Pages, model: ScriptedModel, committer: RememberedCommits
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    committer.commit_failure = "the index is locked"
    hand_in(dashboard, POSTING.format(101), label="Hafenstadt")
    run_next_registration(dashboard)
    for path in [*TABBED, "/sources/hafenstadt"]:
        assert marker_on(dashboard.get(path).text) == ALERT
    refuse_the_bakery(dashboard, network, model)

    # Seeing it is not clearing it: there is still something to read.
    page = dashboard.get("/sources").text
    assert marker_on(page) == ALERT
    dashboard.post(clear_of(page, "Bakery & Sons"))
    assert marker_on(dashboard.get("/").text) == ALERT

    dashboard.post(clear_of(page, "Hafenstadt"))

    for path in TABBED:
        assert marker_on(dashboard.get(path).text) is None


def test_with_no_registrations_the_tabs_are_as_they_were(dashboard: TestClient) -> None:
    for path in TABBED:
        page = dashboard.get(path).text
        assert marker_on(page) is None
        assert re.search(r'<a class="tab[^"]*"[^>]*>Sources</a>', page)
        assert "tab-marker" not in page.split("</head>")[1]


def test_the_turn_after_which_the_page_stops_reloading_itself_counts_as_seen(
    dashboard: TestClient, network: Pages
) -> None:
    network.serve(POSTING.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    hand_in(dashboard, POSTING.format(101), label="Hafenstadt")
    assert RELOADS in dashboard.get("/sources").text
    run_next_registration(dashboard)

    # The last turn: it shows how the last one ended and carries no timer, so no other follows
    # for the user to be shown it on. Whoever has the page open is looking at it.
    last = dashboard.get("/sources", params={"by-itself": "1"}).text
    assert registrations_on(last) == ["Hafenstadt Registered Check its config"]
    assert RELOADS not in last

    assert marker_on(dashboard.get("/").text) is None
    assert registrations_on(dashboard.get("/sources").text) == []
