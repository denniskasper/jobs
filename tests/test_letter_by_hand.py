"""A Letter edited by hand: its body changed in a text box on the Application's page, written and
committed at once, noted in the Conversation, and the end of a Revision that waited.

Every Application, Brief, body and answer here is invented.
"""

import html
import re
from pathlib import Path
from typing import cast

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from conftest import RememberedCommits, ScriptedModel, WriteApplication, fetch_next_answer
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
from test_waiting import in_the_browser

BY_HAND = "**Application as Research Engineer**\n\nDear Dr. Musterfrau,\n\nI measure.\n\nRegards"


def box_on(page: str) -> str:
    """What the text box for the Letter holds on a page."""
    (held,) = re.findall(r'<textarea[^>]*name="body"[^>]*>(.*?)</textarea>', page, re.DOTALL)
    return html.unescape(held)


def save_by_hand(dashboard: TestClient, body: str) -> str:
    """Put a body into the text box the page offers, save it, and give back the page that leads
    to."""
    (action,) = re.findall(
        r'<form[^>]*action="([^"]+)"[^>]*>(?:(?!</form>).)*<textarea[^>]*name="body"',
        dashboard.get(PAGE).text,
        re.DOTALL,
    )
    # A browser hands in the lines of a text box ended the way a form ends them.
    saved: str = dashboard.post(
        html.unescape(action), data={"body": body.replace("\n", "\r\n")}
    ).text
    return saved


def test_a_letter_written_by_hand_alone_is_shown_and_is_one_commit(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    committer: RememberedCommits,
) -> None:
    write_application(FOLDER, RECORD)
    assert box_on(dashboard.get(PAGE).text) == ""

    saved = save_by_hand(dashboard, BY_HAND)

    assert letter_path(data_dir).read_text(encoding="utf-8") == BY_HAND + "\n"
    assert [sorted(commit.paths) for commit in committer.commits] == [
        [conversation_path(data_dir), letter_path(data_dir)]
    ]
    assert "I measure." in part(saved, "letter")
    # The user wrote it, so they have seen it: no difference, and nothing to accept.
    assert difference_on(saved) is None
    assert ">Accept<" not in saved
    assert box_on(saved) == BY_HAND
    assert "The Letter was edited by hand." in part(saved, "conversation")
    # Nothing was asked of a model, and nothing is waiting for one.
    assert "No answer came" not in saved
    assert "under way" not in part(saved, "conversation")
    fetch_next_answer(dashboard)
    assert model.asked == []


def test_the_box_holds_the_letter_as_it_stands(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    model.answers.append(ANSWER)
    ask_for_a_first_version(dashboard)
    fetch_next_answer(dashboard)
    # A Revision that waits is not the Letter.
    assert box_on(dashboard.get(PAGE).text) == ""

    accepted = accept(dashboard, dashboard.get(PAGE).text)

    assert box_on(accepted) == BODY


def test_saving_a_body_that_is_unchanged_writes_and_commits_nothing(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    committer: RememberedCommits,
) -> None:
    write_application(FOLDER, RECORD)
    save_by_hand(dashboard, "")
    assert not letter_path(data_dir).exists()
    assert not conversation_path(data_dir).exists()
    save_by_hand(dashboard, BY_HAND)
    kept = conversation_path(data_dir).read_text(encoding="utf-8")

    # The same words, with the empty lines a text box leaves around them.
    saved = save_by_hand(dashboard, f"\n{BY_HAND}\n\n")

    assert len(committer.commits) == 1
    assert letter_path(data_dir).read_text(encoding="utf-8") == BY_HAND + "\n"
    assert conversation_path(data_dir).read_text(encoding="utf-8") == kept
    assert part(saved, "conversation").count("The Letter was edited by hand.") == 1


def test_a_hand_edit_drops_a_waiting_revision_from_the_page_and_the_conversation_file(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    committer: RememberedCommits,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    model.answers.append(ANSWER)
    ask_for_a_first_version(dashboard)
    fetch_next_answer(dashboard)
    assert difference_on(dashboard.get(PAGE).text) is not None

    saved = save_by_hand(dashboard, BY_HAND)

    assert difference_on(saved) is None
    assert ">Accept<" not in saved
    assert "towing tanks are where" not in saved
    assert "I measure." in part(saved, "letter")
    kept = conversation_path(data_dir).read_text(encoding="utf-8")
    assert "towing tanks are where" not in kept
    assert "waiting" not in kept
    # What the model said about it stays, and so does what became of it.
    assert "Here is a first version." in kept
    assert "Dropped" in part(saved, "conversation")
    assert [sorted(commit.paths) for commit in committer.commits[1:]] == [
        [conversation_path(data_dir), letter_path(data_dir)]
    ]
    # There is nothing left to accept.
    assert dashboard.post(f"{PAGE}/letter/accept").status_code == 422
    assert letter_path(data_dir).read_text(encoding="utf-8") == BY_HAND + "\n"


def test_the_next_prompt_carries_the_note_and_the_edited_letter(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    ask_for_a_first_version(dashboard)

    # Edited while the request waits for its turn: it is still the one to be answered.
    saved = save_by_hand(dashboard, BY_HAND)
    assert "under way" in part(saved, "conversation")
    model.answers.append(ANSWER)
    fetch_next_answer(dashboard)

    (prompt,) = model.asked
    assert "The Letter was edited by hand." in prompt
    assert "I measure." in prompt
    assert "Here is a first version." in part(dashboard.get(PAGE).text, "conversation")


def test_a_letter_emptied_by_hand_can_have_a_first_version_asked_for(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    save_by_hand(dashboard, BY_HAND)
    assert "Ask for a first version" not in dashboard.get(PAGE).text

    emptied = save_by_hand(dashboard, "")

    assert "There is no Letter yet." in part(emptied, "letter")
    asked = ask_for_a_first_version(dashboard)
    # The notes are still there, under what was asked after them.
    assert part(asked, "conversation").count("The Letter was edited by hand.") == 2
    assert "under way" in part(asked, "conversation")


def test_a_hand_edit_that_could_not_be_committed_says_so_and_is_written(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    committer: RememberedCommits,
) -> None:
    write_application(FOLDER, RECORD)
    committer.commit_failure = "the disk is full"

    saved = dashboard.post(f"{PAGE}/letter/edit", data={"body": BY_HAND})

    assert saved.status_code == 500
    assert "could not be committed: the disk is full" in saved.text
    assert box_on(saved.text) == BY_HAND
    assert letter_path(data_dir).read_text(encoding="utf-8") == BY_HAND + "\n"


def test_a_hand_edit_refused_keeps_what_was_typed_in_the_box(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)
    conversation_path(data_dir).write_text("messages: 7\n", encoding="utf-8")

    refused = dashboard.post(f"{PAGE}/letter/edit", data={"body": BY_HAND})

    assert refused.status_code == 422
    assert "Nothing was written" in refused.text
    assert not letter_path(data_dir).exists()
    assert box_on(refused.text) == BY_HAND


def test_editing_by_hand_in_a_folder_that_names_no_application_is_not_found(
    dashboard: TestClient,
) -> None:
    assert dashboard.post(f"{PAGE}/letter/edit", data={"body": BY_HAND}).status_code == 404


def test_an_answer_does_not_arrive_under_an_open_box(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    if reason := no_chromium():
        pytest.skip(reason)
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    ask_for_a_first_version(dashboard)
    with in_the_browser(cast(Starlette, dashboard.app)) as (page, origin):
        # The page's own clock, so that a turn passes when the test says so.
        page.clock.install(time=0)
        page.goto(f"{origin}{PAGE}")
        page.clock.pause_at(2)
        model.answers.append(ANSWER)
        fetch_next_answer(dashboard)

        # The box is part of what an answer replaces, so while it is open nothing is replaced,
        # focused or not.
        page.click("#letter summary")
        page.fill("#body", BY_HAND)
        page.evaluate("document.activeElement.blur()")
        page.clock.run_for(3000)
        assert page.input_value("#body") == BY_HAND
        assert "An answer is under way" in (page.text_content("#conversation") or "")

        # Closed again, the next turn brings the answer.
        page.click("#letter summary")
        page.clock.run_for(3000)
        page.wait_for_selector("#letter .difference", state="attached")


def test_a_revision_made_while_the_letter_was_edited_by_hand_does_not_wait(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    ask_for_a_first_version(dashboard)

    def answer_while_the_letter_is_edited(prompt: str) -> str:
        save_by_hand(dashboard, BY_HAND)
        return ANSWER

    monkeypatch.setattr(model, "ask", answer_while_the_letter_is_edited)
    fetch_next_answer(dashboard)

    # The model wrote against a Letter that is no longer the one that stands.
    page = dashboard.get(PAGE).text
    assert difference_on(page) is None
    assert ">Accept<" not in page
    assert "Here is a first version." in part(page, "conversation")
    assert "Dropped" in part(page, "conversation")
    assert "towing tanks are where" not in conversation_path(data_dir).read_text(encoding="utf-8")
    assert letter_path(data_dir).read_text(encoding="utf-8") == BY_HAND + "\n"
