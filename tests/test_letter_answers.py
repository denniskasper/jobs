"""An answer about a Letter that never came, a sitting that outlives the service, and a page that
shows an answer when it arrives.

Every Application, Brief and answer here is invented.
"""

import html
import re
from pathlib import Path

import pytest
import yaml
from starlette.applications import Starlette
from starlette.testclient import TestClient

from conftest import (
    Pages,
    RememberedCommits,
    ScriptedModel,
    SettableClock,
    WriteApplication,
    fetch_next_answer,
)
from jobs.web import create_app
from test_fetching import no_chromium
from test_letter_revision import (
    ANSWER,
    BODY,
    FOLDER,
    PAGE,
    RECORD,
    accept,
    ask_for_a_first_version,
    conversation_path,
    difference_on,
    letter_path,
    part,
    write_brief,
)
from test_registrations import TABBED, marker_on
from test_waiting import in_the_browser

OTHER_FOLDER = "anker/2026-09-02-naval-architect"
OTHER_BARE = "company: Anker\nrole: Naval Architect\ndate: 2026-09-02\nstatus: todo\n"
OTHER_RECORD = OTHER_BARE + "notes: I want to draw hulls.\n"
OTHER_PAGE = f"/applications/{OTHER_FOLDER}"

# The few lines with which an Application's page asks for its own state, by the name it gives them.
KEEPS_UP = 'id="keeps-up"'

# The note of an answer under way, marked as busy so that it turns while it waits.
BUSY = re.compile(r'<p[^>]*aria-busy="true"[^>]*>\s*An answer is under way')


def send_again(dashboard: TestClient, page: str) -> str:
    """Press "Send again" under the Conversation a page shows, and give back the page it leads
    to."""
    (action,) = re.findall(
        r'<form[^>]*action="([^"]+)"[^>]*>(?:(?!</form>).)*>Send again<', page, re.DOTALL
    )
    sent: str = dashboard.post(html.unescape(action)).text
    return sent


def kept(data_dir: Path) -> list[dict[str, str]]:
    """The messages the Conversation file holds."""
    messages: list[dict[str, str]] = yaml.safe_load(
        conversation_path(data_dir).read_text(encoding="utf-8")
    )["messages"]
    return messages


def test_a_model_that_is_unavailable_leaves_the_message_unanswered_with_the_reason(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    ask_for_a_first_version(dashboard)

    fetch_next_answer(dashboard)

    conversation = part(dashboard.get(PAGE).text, "conversation")
    assert "A first version was asked for." in conversation
    assert "No answer came: no model is available." in conversation
    assert "Send again" in conversation


def test_an_answer_that_cannot_be_read_leaves_the_message_unanswered_with_the_reason(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    model.answers.append(f"Here it is.\n\n<letter>\n{BODY}\n")
    ask_for_a_first_version(dashboard)

    fetch_next_answer(dashboard)

    conversation = part(dashboard.get(PAGE).text, "conversation")
    assert "No answer came: the answer does not hold one body" in conversation
    assert "Send again" in conversation


def test_the_reason_for_a_failure_is_not_in_the_conversation_file(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    ask_for_a_first_version(dashboard)
    asked = conversation_path(data_dir).read_text(encoding="utf-8")

    fetch_next_answer(dashboard)

    # Nothing is recorded about the attempt: the file is as it was when the message went in.
    assert conversation_path(data_dir).read_text(encoding="utf-8") == asked
    assert "no model is available" not in asked


def test_sending_again_asks_with_the_same_message_and_its_answer_makes_it_answered(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    committer: RememberedCommits,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    ask_for_a_first_version(dashboard)
    fetch_next_answer(dashboard)

    sent = send_again(dashboard, dashboard.get(PAGE).text)

    # Taken on like the first time: under way, the reason gone, and no model asked in the request.
    assert "An answer is under way" in part(sent, "conversation")
    assert "No answer came" not in sent
    assert "Send again" not in sent
    assert len(model.asked) == 1

    model.answers.append(ANSWER)
    fetch_next_answer(dashboard)

    assert model.asked[1] == model.asked[0]
    page = dashboard.get(PAGE).text
    assert "Here is a first version." in part(page, "conversation")
    assert "No answer came" not in page
    assert "Send again" not in page
    assert difference_on(page) is not None
    # The message once, as it was, and its answer after it.
    assert [each["from"] for each in kept(data_dir)] == ["applicant", "model"]
    # Committed with its answer, and not before.
    assert [each.paths for each in committer.commits] == [[conversation_path(data_dir)]]


def test_an_answer_under_way_shows_itself_busy_until_it_has_come(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)

    asked = ask_for_a_first_version(dashboard)

    assert BUSY.search(asked)

    model.answers.append(ANSWER)
    fetch_next_answer(dashboard)

    assert not BUSY.search(dashboard.get(PAGE).text)


def test_a_message_that_was_answered_or_is_under_way_cannot_be_sent_again(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)

    nothing_asked = dashboard.post(f"{PAGE}/letter/again")
    ask_for_a_first_version(dashboard)
    under_way = dashboard.post(f"{PAGE}/letter/again")
    model.answers.append(ANSWER)
    fetch_next_answer(dashboard)
    answered = dashboard.post(f"{PAGE}/letter/again")
    fetch_next_answer(dashboard)

    assert [nothing_asked.status_code, under_way.status_code, answered.status_code] == [422] * 3
    assert "already under way" in under_way.text
    assert len(model.asked) == 1
    assert [each["from"] for each in kept(data_dir)] == ["applicant", "model"]


def test_sending_again_in_a_folder_that_names_no_application_is_not_found(
    dashboard: TestClient,
) -> None:
    assert dashboard.post("/applications/tuhh/2026-09-01-gone/letter/again").status_code == 404


@pytest.fixture
def new_app(
    data_dir: Path,
    clock: SettableClock,
    network: Pages,
    committer: RememberedCommits,
    model: ScriptedModel,
) -> Starlette:
    """The service started again on the same data directory: nothing it kept in memory is left."""
    return create_app(
        data_dir=data_dir,
        clock=clock,
        network=network,
        committer=committer,
        model=model,
        sleep=lambda seconds: None,
    )


def test_a_message_under_way_when_the_service_restarts_is_unanswered_without_a_reason(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    new_app: Starlette,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    ask_for_a_first_version(dashboard)
    restarted = TestClient(new_app)

    page = restarted.get(PAGE).text

    conversation = part(page, "conversation")
    assert "A first version was asked for." in conversation
    assert "under way" not in conversation
    assert "No answer came." in conversation
    # Nothing fetches what the service that is gone had taken on.
    fetch_next_answer(restarted)
    assert model.asked == []

    model.answers.append(ANSWER)
    send_again(restarted, page)
    fetch_next_answer(restarted)

    assert "Here is a first version." in part(restarted.get(PAGE).text, "conversation")


def test_a_reason_does_not_outlive_the_service(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    new_app: Starlette,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    ask_for_a_first_version(dashboard)
    fetch_next_answer(dashboard)
    assert "no model is available" in dashboard.get(PAGE).text

    page = TestClient(new_app).get(PAGE).text

    assert "No answer came." in part(page, "conversation")
    assert "no model is available" not in page


def test_a_waiting_revision_shows_with_its_difference_after_a_restart_and_can_be_accepted(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    new_app: Starlette,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    model.answers.append(ANSWER)
    ask_for_a_first_version(dashboard)
    fetch_next_answer(dashboard)
    before = dashboard.get(PAGE).text
    restarted = TestClient(new_app)

    page = restarted.get(PAGE).text

    conversation = part(page, "conversation")
    assert "A first version was asked for." in conversation
    assert "Here is a first version. I left out the salary." in conversation
    assert "No answer came" not in conversation
    shown = difference_on(page)
    assert shown is not None
    assert shown == difference_on(before)
    assert not letter_path(data_dir).exists()

    accept(restarted, page)

    assert letter_path(data_dir).read_text(encoding="utf-8") == BODY + "\n"


def test_two_messages_for_two_letters_are_answered_in_the_order_sent_one_at_a_time(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_application(OTHER_FOLDER, OTHER_BARE + "notes: Mention the Kiel yard.\n")
    write_brief(data_dir)
    # The one whose folder sorts last is asked first, so the order is the order asked.
    dashboard.post(f"{OTHER_PAGE}/letter/ask")
    dashboard.post(f"{PAGE}/letter/ask")
    model.answers += ["For Anker.\n\n<letter>\nAhoy.\n</letter>\n", ANSWER]

    fetch_next_answer(dashboard)

    assert len(model.asked) == 1
    assert "Mention the Kiel yard." in model.asked[0]
    assert "For Anker." in part(dashboard.get(OTHER_PAGE).text, "conversation")
    assert "An answer is under way" in part(dashboard.get(PAGE).text, "conversation")

    fetch_next_answer(dashboard)

    assert len(model.asked) == 2
    assert "Mention the Kiel yard." not in model.asked[1]
    assert "Here is a first version." in part(dashboard.get(PAGE).text, "conversation")
    assert "For Anker." not in dashboard.get(PAGE).text


def test_a_message_sent_again_takes_its_turn_behind_what_was_asked_before_it(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_application(OTHER_FOLDER, OTHER_RECORD)
    write_brief(data_dir)
    dashboard.post(f"{PAGE}/letter/ask")
    fetch_next_answer(dashboard)
    dashboard.post(f"{OTHER_PAGE}/letter/ask")
    send_again(dashboard, dashboard.get(PAGE).text)
    model.answers += ["For Anker.\n\n<letter>\nAhoy.\n</letter>\n", ANSWER]

    fetch_next_answer(dashboard)

    assert "For Anker." in part(dashboard.get(OTHER_PAGE).text, "conversation")
    assert "An answer is under way" in part(dashboard.get(PAGE).text, "conversation")


def test_the_tabs_show_nothing_about_answers(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    ask_for_a_first_version(dashboard)

    # One under way, and then one that never came.
    for _ in range(2):
        for path in [*TABBED, PAGE]:
            page = dashboard.get(path).text
            assert marker_on(page) is None
            assert "tab-marker" not in page.split("</head>")[1]
        fetch_next_answer(dashboard)


def test_the_page_asks_for_its_own_state_only_while_an_answer_is_waiting_or_under_way(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_application(OTHER_FOLDER, OTHER_RECORD)
    write_brief(data_dir)
    assert KEEPS_UP not in dashboard.get(PAGE).text

    # Waiting behind another Letter's, and then under way.
    dashboard.post(f"{OTHER_PAGE}/letter/ask")
    assert KEEPS_UP in ask_for_a_first_version(dashboard)
    fetch_next_answer(dashboard)
    assert KEEPS_UP in dashboard.get(PAGE).text
    # The other Letter's page has nothing left to wait for: its answer never came.
    assert KEEPS_UP not in dashboard.get(OTHER_PAGE).text

    model.answers.append(ANSWER)
    fetch_next_answer(dashboard)

    assert KEEPS_UP not in dashboard.get(PAGE).text
    for path in TABBED:
        assert KEEPS_UP not in dashboard.get(path).text


def test_a_page_with_a_refusal_does_not_ask_for_its_own_state(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    ask_for_a_first_version(dashboard)

    # Not reached by GET: asking for this address again would not be asking for the page.
    refused = dashboard.post(f"{PAGE}/letter/ask")

    assert refused.status_code == 422
    assert KEEPS_UP not in refused.text


def test_the_page_shows_an_answer_when_it_arrives_but_never_under_the_users_hands(
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    new_app: Starlette,
) -> None:
    if reason := no_chromium():
        pytest.skip(reason)
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    dashboard = TestClient(new_app)
    ask_for_a_first_version(dashboard)
    with in_the_browser(new_app) as (page, origin):
        asked_for: list[str] = []
        # What the service is asked for: a typeface fetched from elsewhere is not a turn.
        page.on(
            "request",
            lambda request: (
                asked_for.append(request.url) if request.url.startswith(origin) else None
            ),
        )
        # The page's own clock, so that a turn passes when the test says so.
        # Held before the page loads, however long that takes, and before its first turn.
        page.clock.install(time=0)
        page.clock.pause_at(2)
        page.goto(f"{origin}{PAGE}")
        asked_for.clear()
        model.answers.append(ANSWER)
        fetch_next_answer(dashboard)

        # With a field focused a turn passes, and the page asks for nothing.
        page.fill("#notes", "Mention the towing tank.")
        page.clock.run_for(3000)
        assert asked_for == []
        assert "An answer is under way" in (page.text_content("#conversation") or "")

        # Left alone, the next turn brings the answer and its difference.
        page.evaluate("document.activeElement.blur()")
        page.clock.run_for(3000)
        page.wait_for_selector("#letter .difference", state="attached")

        assert asked_for == [f"{origin}{PAGE}"]
        assert "Here is a first version." in (page.text_content("#conversation") or "")
        assert "towing tanks are where I learned" in (page.text_content("#letter") or "")
        # Only the Letter and its Conversation were brought up to date: what was typed and not
        # saved is still there, and the page was never left.
        assert page.input_value("#notes") == "Mention the towing tank."
        assert page.url == f"{origin}{PAGE}"

        # Nothing is waiting any more, so no further turn asks.
        page.clock.run_for(9000)
        assert asked_for == [f"{origin}{PAGE}"]

        # What was brought in works like what was loaded.
        page.click("#letter button:has-text('Accept')")
        page.wait_for_selector("#letter .letter-body", state="attached")
    assert letter_path(data_dir).read_text(encoding="utf-8") == BODY + "\n"


# What the page is told when it goes out of sight or comes back, as a browser tells it.
SHOWN_AS = """shown => {
    Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => shown});
    document.dispatchEvent(new Event('visibilitychange'));
}"""
NEW = "#conversation .message.new"


def test_answers_that_came_since_the_last_visit_are_marked_new_and_the_tab_says_so(
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    new_app: Starlette,
) -> None:
    if reason := no_chromium():
        pytest.skip(reason)
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    dashboard = TestClient(new_app)
    ask_for_a_first_version(dashboard)
    model.answers.append(ANSWER)
    fetch_next_answer(dashboard)
    with in_the_browser(new_app) as (page, origin):
        # The first visit has nothing to compare with: nothing is new.
        page.goto(f"{origin}{PAGE}")
        title = page.title()
        assert page.locator(NEW).count() == 0

        # Away from the page, a message is sent and answered.
        dashboard.post(f"{PAGE}/letter/say", data={"message": "Shorter, please."})
        model.answers.append("Shorter now.")
        fetch_next_answer(dashboard)

        # Back again: the answer is new, and neither the earlier one nor what was said is.
        page.goto(f"{origin}{PAGE}")
        assert page.locator(NEW).all_text_contents() == [
            page.locator("#conversation .message").last.text_content()
        ]
        assert "Shorter now." in (page.locator(NEW).text_content() or "")
        assert page.title() == title

        # Seen once, it is not new on the next visit.
        page.goto(f"{origin}{PAGE}")
        assert page.locator(NEW).count() == 0

        # An answer that arrives while the page is out of sight marks its tab until it is seen.
        page.clock.install(time=0)
        page.clock.pause_at(2)
        dashboard.post(f"{PAGE}/letter/say", data={"message": "And warmer."})
        page.goto(f"{origin}{PAGE}")
        page.evaluate(SHOWN_AS, "hidden")
        model.answers.append("Warmer now.")
        fetch_next_answer(dashboard)
        page.clock.run_for(3000)
        page.wait_for_selector(NEW, state="attached")
        assert page.title() == f"● {title}"
        assert "favicon-new.svg" in (page.get_attribute("link[rel=icon]", "href") or "")

        page.evaluate(SHOWN_AS, "visible")
        assert page.title() == title
        assert "favicon-new.svg" not in (page.get_attribute("link[rel=icon]", "href") or "")
        assert "Warmer now." in (page.locator(NEW).text_content() or "")
