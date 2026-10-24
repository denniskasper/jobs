"""A Letter that was sent: once its Application is no longer `todo` it can be read and not
changed, by nothing the page offers and by no request, until the Application is set back.

Every Application, Brief, body and answer here is invented.
"""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from conftest import PDF, RememberedCommits, ScriptedModel, WriteApplication, fetch_next_answer
from jobs.applications import Status
from test_application_page import field_on
from test_letter_answers import send_again
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
from test_letter_steering import REVISED, SHORTER, a_first_revision_waits, say

# Every Status an Application has left `todo` for.
LEFT = [each.value for each in Status if each is not Status.TODO]

# Everything that would change a Letter or ask a model about it, as a stranger to the page would
# send it.
REQUESTS: dict[str, dict[str, str]] = {
    "ask": {},
    "say": {"message": "Make it shorter."},
    "again": {},
    "accept": {},
    "reject": {},
    "edit": {"body": "Something else entirely."},
}


def set_status(dashboard: TestClient, status: str) -> None:
    changed = dashboard.post(f"{PAGE}/status", data={"status": status})
    assert changed.status_code == 200


def everything_in(data_dir: Path) -> dict[Path, bytes]:
    """Every file in the data directory with what it holds."""
    return {path: path.read_bytes() for path in sorted(data_dir.rglob("*")) if path.is_file()}


def a_letter_was_accepted(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication, model: ScriptedModel
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    accept(dashboard, dashboard.get(PAGE).text)


@pytest.mark.parametrize("status", LEFT)
def test_a_letter_that_was_sent_is_shown_with_its_conversation_and_nothing_to_change_it(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    status: str,
) -> None:
    a_letter_was_accepted(dashboard, data_dir, write_application, model)

    set_status(dashboard, status)

    page = dashboard.get(PAGE).text
    assert "towing tanks are where I learned to measure" in part(page, "letter")
    assert dashboard.get(f"{PAGE}/letter.pdf").content == PDF
    conversation = part(page, "conversation")
    assert "Here is a first version." in conversation
    assert "Revision: Accepted" in conversation
    assert 'name="body"' not in page
    assert 'name="message"' not in page
    for button in ("Ask for a first version", ">Accept<", ">Reject<", ">Send<", ">Send again<"):
        assert button not in page
    assert "no longer changed" in part(page, "letter")


@pytest.mark.parametrize("status", LEFT)
def test_without_a_letter_a_first_version_cannot_be_asked_for_once_the_status_changed(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication, status: str
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    assert "Ask for a first version" in dashboard.get(PAGE).text

    set_status(dashboard, status)

    page = dashboard.get(PAGE).text
    assert "There is no Letter" in part(page, "letter")
    assert "Ask for a first version" not in page
    assert 'name="body"' not in page
    assert 'name="message"' not in page


@pytest.mark.parametrize("request_name", REQUESTS)
def test_every_request_to_change_a_sent_letter_is_refused_and_says_why(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    committer: RememberedCommits,
    request_name: str,
) -> None:
    # Each request in the one state in which it is carried out while `todo`, so that the Status
    # alone is what refuses it.
    if request_name == "ask":
        write_application(FOLDER, RECORD)
        write_brief(data_dir)
    elif request_name == "again":
        write_application(FOLDER, RECORD)
        write_brief(data_dir)
        ask_for_a_first_version(dashboard)
        fetch_next_answer(dashboard)
        assert ">Send again<" in dashboard.get(PAGE).text
    else:
        a_first_revision_waits(dashboard, data_dir, write_application, model)
    set_status(dashboard, "closed")
    before, commits, asked = everything_in(data_dir), len(committer.commits), len(model.asked)
    model.answers.append(REVISED)

    refused = dashboard.post(f"{PAGE}/letter/{request_name}", data=REQUESTS[request_name])

    assert refused.status_code == 422
    assert "This Application is Closed" in refused.text
    assert "can be read and no longer changed" in refused.text
    # Nor does the page that says so offer what it refused.
    assert 'name="body"' not in refused.text
    assert 'name="message"' not in refused.text
    fetch_next_answer(dashboard)
    assert len(model.asked) == asked
    assert everything_in(data_dir) == before
    assert len(committer.commits) == commits


def test_a_revision_waiting_when_the_status_changed_is_shown_and_cannot_be_decided(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    waiting = difference_on(dashboard.get(PAGE).text)
    assert waiting is not None

    set_status(dashboard, "waiting")

    page = dashboard.get(PAGE).text
    assert difference_on(page) == waiting
    assert "Revision: Waiting" in part(page, "conversation")
    assert "undecided" in part(page, "letter")
    assert "until you accept" not in part(page, "letter")
    assert ">Accept<" not in page
    assert ">Reject<" not in page
    assert 'name="body"' not in page
    # Its preview is a way to read it, and changes nothing.
    assert dashboard.get(f"{PAGE}/letter/revision.pdf").content == PDF
    assert "undecided" in part(dashboard.get(f"{PAGE}?view=preview").text, "letter")
    assert not letter_path(data_dir).exists()


def test_notes_and_the_address_of_a_sent_letter_can_still_be_saved(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    a_letter_was_accepted(dashboard, data_dir, write_application, model)
    set_status(dashboard, "interview")

    saved = dashboard.post(
        PAGE, data={"notes": "They called on Monday.", "address": "Am Beispielkai 7"}
    )

    assert saved.status_code == 200
    assert field_on(saved.text, "notes") == "They called on Monday."
    assert field_on(saved.text, "address") == "Am Beispielkai 7"


def test_back_at_todo_the_letter_can_be_revised_as_before(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    a_letter_was_accepted(dashboard, data_dir, write_application, model)
    set_status(dashboard, "waiting")

    set_status(dashboard, "todo")

    model.answers.append(REVISED)
    say(dashboard, "Make it shorter.")
    fetch_next_answer(dashboard)
    page = dashboard.get(PAGE).text
    assert difference_on(page) is not None
    assert 'name="body"' in page
    accept(dashboard, page)
    assert letter_path(data_dir).read_text(encoding="utf-8") == SHORTER + "\n"


def test_a_revision_that_waited_through_a_change_of_status_can_be_decided_back_at_todo(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    set_status(dashboard, "waiting")

    set_status(dashboard, "todo")

    accept(dashboard, dashboard.get(PAGE).text)
    assert letter_path(data_dir).read_text(encoding="utf-8") == BODY + "\n"


def test_an_answer_asked_for_before_the_status_changed_is_not_fetched(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    ask_for_a_first_version(dashboard)
    set_status(dashboard, "waiting")
    model.answers.append(ANSWER)
    before = conversation_path(data_dir).read_bytes()

    fetch_next_answer(dashboard)

    assert model.asked == []
    assert conversation_path(data_dir).read_bytes() == before
    page = dashboard.get(PAGE).text
    conversation = part(page, "conversation")
    assert "No answer came: the Application was Waiting when its turn came" in conversation
    assert ">Send again<" not in page
    # The message is still there to be sent again once the Letter can be revised.
    set_status(dashboard, "todo")
    send_again(dashboard, dashboard.get(PAGE).text)
    fetch_next_answer(dashboard)
    assert difference_on(dashboard.get(PAGE).text) is not None


def test_an_answer_that_comes_after_the_status_changed_is_not_kept(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    committer: RememberedCommits,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    waiting = difference_on(dashboard.get(PAGE).text)
    say(dashboard, "Make it shorter.")
    model.answers.append(REVISED)
    ask = model.ask

    def sent_meanwhile(prompt: str) -> str:
        set_status(dashboard, "waiting")
        return ask(prompt)

    monkeypatch.setattr(model, "ask", sent_meanwhile)
    before, commits = conversation_path(data_dir).read_bytes(), len(committer.commits)

    fetch_next_answer(dashboard)

    assert conversation_path(data_dir).read_bytes() == before
    # The change of Status is the one commit since.
    assert [each.message for each in committer.commits[commits:]] == [
        f"Change Status of {FOLDER} from todo to waiting"
    ]
    page = dashboard.get(PAGE).text
    # The Revision that waited is the one that still does.
    assert difference_on(page) == waiting
    assert "the Application was Waiting when the answer came" in part(page, "conversation")


def test_an_empty_message_about_a_sent_letter_is_refused_for_the_status(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    a_letter_was_accepted(dashboard, data_dir, write_application, model)
    set_status(dashboard, "waiting")

    refused = dashboard.post(f"{PAGE}/letter/say", data={"message": " "})

    assert refused.status_code == 422
    assert "can be read and no longer changed" in refused.text
