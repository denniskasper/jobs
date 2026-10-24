"""A Revision steered: a message typed on the Application's page, the proposal revised by the
answer to it, an answer that only asks back, and a Revision that is rejected.

Every Application, Brief, body and answer here is invented.
"""

import html
import re
from pathlib import Path

import yaml
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

SHORTER = (
    "**Application as Research Engineer**\n\n"
    "Dear Dr. Musterfrau,\n\n"
    "towing tanks are where I learned to measure.\n\n"
    "Kind regards"
)
REVISED = f"Shorter, as you asked.\n\n<letter>\n{SHORTER}\n</letter>\n"


def say(dashboard: TestClient, message: str) -> str:
    """Type a message into the box the page offers, send it, and give back the page that leads
    to."""
    (action,) = re.findall(
        r'<form[^>]*action="([^"]+)"[^>]*>(?:(?!</form>).)*<textarea[^>]*name="message"',
        dashboard.get(PAGE).text,
        re.DOTALL,
    )
    said: str = dashboard.post(html.unescape(action), data={"message": message}).text
    return said


def reject(dashboard: TestClient, page: str) -> str:
    """Press Reject under the difference a page shows, and give back the page it leads to."""
    (action,) = re.findall(
        r'<form[^>]*action="([^"]+)"[^>]*>(?:(?!</form>).)*>Reject<', page, re.DOTALL
    )
    rejected: str = dashboard.post(html.unescape(action)).text
    return rejected


def waiting_in(data_dir: Path) -> list[str]:
    """Every body the Conversation file holds as waiting, wherever it holds one."""
    kept = yaml.safe_load(conversation_path(data_dir).read_text(encoding="utf-8"))
    return [value for key, value in kept.items() if key != "messages"]


def marked_waiting_in(data_dir: Path) -> int:
    """How many answers the Conversation file marks as carrying the Revision that waits."""
    kept = yaml.safe_load(conversation_path(data_dir).read_text(encoding="utf-8"))
    return sum(each.get("revision") == "waiting" for each in kept["messages"])


def a_first_revision_waits(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication, model: ScriptedModel
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    model.answers.append(ANSWER)
    ask_for_a_first_version(dashboard)
    fetch_next_answer(dashboard)


def test_a_message_shows_at_once_and_is_answered_later(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    committer: RememberedCommits,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    model.answers.append(REVISED)

    said = say(dashboard, "Shorter, and do not say believe.")

    conversation = part(said, "conversation")
    assert "Shorter, and do not say believe." in conversation
    assert "An answer is under way" in conversation
    assert len(model.asked) == 1
    assert "Shorter, and do not say believe." in conversation_path(data_dir).read_text(
        encoding="utf-8"
    )
    # On disk, and committed with its answer.
    assert len(committer.commits) == 1

    fetch_next_answer(dashboard)

    conversation = part(dashboard.get(PAGE).text, "conversation")
    assert "Shorter, as you asked." in conversation
    assert "under way" not in conversation
    assert [each.paths for each in committer.commits[1:]] == [[conversation_path(data_dir)]]


def test_the_prompt_for_a_message_carries_the_letter_and_everything_said_before(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    accept(dashboard, dashboard.get(PAGE).text)
    model.answers += ["Which rig do you mean?", REVISED]
    say(dashboard, "Name the rig I built.")
    fetch_next_answer(dashboard)
    say(dashboard, "The one in Hafenstadt.")

    fetch_next_answer(dashboard)

    prompt = model.asked[-1]
    assert BODY in prompt
    for earlier in (
        "A first version was asked for.",
        "Here is a first version. I left out the salary.",
        "Name the rig I built.",
        "Which rig do you mean?",
        "The one in Hafenstadt.",
    ):
        assert earlier in prompt


def test_a_message_while_a_revision_waits_revises_it_against_the_letter_as_it_stands(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    model.answers.append(REVISED)
    say(dashboard, "Shorter, and do not say believe.")

    fetch_next_answer(dashboard)

    # The proposal before it is in the prompt, since the model remembers nothing.
    assert "to measure before I believe." in model.asked[-1]
    page = dashboard.get(PAGE).text
    # Against the Letter, which is still empty, and not against the proposal before it.
    assert difference_on(page) == [
        [("added", "**Application as Research Engineer**")],
        [("added", "Dear Dr. Musterfrau,")],
        [("added", "towing tanks are where I learned to measure.")],
        [("added", "Kind regards")],
    ]
    assert waiting_in(data_dir) == [SHORTER]
    assert marked_waiting_in(data_dir) == 1
    assert "before I believe" not in conversation_path(data_dir).read_text(encoding="utf-8")
    assert not letter_path(data_dir).exists()
    # Of the two answers that carried a body, one still waits.
    conversation = part(page, "conversation")
    assert conversation.count("Revision: Waiting") == 1
    assert conversation.count("Revision: Revised") == 1


def test_accepting_a_revised_revision_writes_the_latest_body(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    model.answers.append(REVISED)
    say(dashboard, "Shorter, and do not say believe.")
    fetch_next_answer(dashboard)

    accept(dashboard, dashboard.get(PAGE).text)

    assert letter_path(data_dir).read_text(encoding="utf-8") == SHORTER + "\n"


def test_with_a_letter_accepted_a_message_yields_a_difference_against_that_letter(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    accept(dashboard, dashboard.get(PAGE).text)
    model.answers.append(REVISED)
    say(dashboard, "Shorter, and do not say believe.")

    fetch_next_answer(dashboard)

    assert difference_on(dashboard.get(PAGE).text) == [
        [("same", "**Application as Research Engineer**")],
        [("same", "Dear Dr. Musterfrau,")],
        [
            ("same", "towing tanks are where I learned to "),
            ("removed", "measure before I believe."),
            ("added", "measure."),
        ],
        [("same", "Kind regards")],
    ]
    assert letter_path(data_dir).read_text(encoding="utf-8") == BODY + "\n"


def test_an_answer_without_a_body_shows_as_a_message_and_a_waiting_revision_stays(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    before = difference_on(dashboard.get(PAGE).text)
    model.answers.append("Which year did you build the rig?")
    say(dashboard, "Say when I built the rig.")

    fetch_next_answer(dashboard)

    page = dashboard.get(PAGE).text
    assert "Which year did you build the rig?" in part(page, "conversation")
    assert difference_on(page) == before
    assert waiting_in(data_dir) == [BODY]
    assert part(page, "conversation").count("Revision:") == 1


def test_an_answer_without_a_body_proposes_nothing_when_nothing_waits(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    model.answers.append("What should the Letter be about?")
    say(dashboard, "Write me something.")

    fetch_next_answer(dashboard)

    page = dashboard.get(PAGE).text
    assert "What should the Letter be about?" in part(page, "conversation")
    assert difference_on(page) is None
    assert ">Accept<" not in page
    assert waiting_in(data_dir) == []


def test_an_answer_whose_body_is_the_letter_as_it_stands_proposes_nothing(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    accept(dashboard, dashboard.get(PAGE).text)
    model.answers.append(f"I would leave it as it is.\n\n<letter>\n{BODY}\n</letter>\n")
    say(dashboard, "Is it good?")

    fetch_next_answer(dashboard)

    page = dashboard.get(PAGE).text
    assert "I would leave it as it is." in part(page, "conversation")
    assert difference_on(page) is None
    assert ">Accept<" not in page
    assert waiting_in(data_dir) == []
    assert "Revision: Waiting" not in part(page, "conversation")


def test_rejecting_leaves_the_letter_untouched_and_drops_the_body_in_one_commit(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    committer: RememberedCommits,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)

    rejected = reject(dashboard, dashboard.get(PAGE).text)

    assert not letter_path(data_dir).exists()
    assert [each.paths for each in committer.commits[1:]] == [[conversation_path(data_dir)]]
    kept = conversation_path(data_dir).read_text(encoding="utf-8")
    assert "towing tanks are where" not in kept
    assert waiting_in(data_dir) == []
    assert "rejected" in kept
    assert difference_on(rejected) is None
    assert ">Accept<" not in rejected
    assert "Revision: Rejected" in part(rejected, "conversation")
    assert "There is no Letter yet." in part(rejected, "letter")


def test_rejecting_keeps_the_letter_that_was_accepted_before(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    accept(dashboard, dashboard.get(PAGE).text)
    model.answers.append(REVISED)
    say(dashboard, "Shorter.")
    fetch_next_answer(dashboard)

    rejected = reject(dashboard, dashboard.get(PAGE).text)

    assert letter_path(data_dir).read_text(encoding="utf-8") == BODY + "\n"
    assert "to measure before I believe." in part(rejected, "letter")


def test_the_prompt_after_a_rejection_says_the_revision_was_rejected(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    reject(dashboard, dashboard.get(PAGE).text)
    model.answers.append(REVISED)
    say(dashboard, "Try once more, shorter.")

    fetch_next_answer(dashboard)

    prompt = model.asked[-1]
    assert "rejected" in prompt
    # What was rejected is of no further use, and is not handed back.
    assert "before I believe" not in prompt


def test_rejecting_when_no_revision_waits_is_refused_and_commits_nothing(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    committer: RememberedCommits,
) -> None:
    write_application(FOLDER, RECORD)

    refused = dashboard.post(f"{PAGE}/letter/reject")

    assert refused.status_code == 422
    assert "No Revision is waiting" in refused.text
    assert committer.commits == []


def test_a_message_with_nothing_in_it_is_refused_and_asks_nothing(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)

    refused = dashboard.post(f"{PAGE}/letter/say", data={"message": " \r\n"})

    assert refused.status_code == 422
    assert not conversation_path(data_dir).exists()
    fetch_next_answer(dashboard)
    assert model.asked == []


def test_a_message_without_a_brief_is_refused_and_stays_in_the_box(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)

    refused = dashboard.post(f"{PAGE}/letter/say", data={"message": "Mention the towing tank."})

    assert refused.status_code == 422
    assert "There is no Brief" in refused.text
    assert not conversation_path(data_dir).exists()
    # Refused, and not lost: it does not have to be typed again.
    assert re.search(
        r'<textarea[^>]*name="message"[^>]*>Mention the towing tank\.</textarea>', refused.text
    )


def test_no_message_can_be_sent_while_an_answer_is_under_way(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    asked = ask_for_a_first_version(dashboard)
    kept = conversation_path(data_dir).read_text(encoding="utf-8")

    refused = dashboard.post(f"{PAGE}/letter/say", data={"message": "And shorter."})

    assert 'name="message"' not in asked
    assert refused.status_code == 422
    assert "under way" in refused.text
    assert conversation_path(data_dir).read_text(encoding="utf-8") == kept


def test_a_message_can_follow_one_that_was_never_answered(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    ask_for_a_first_version(dashboard)
    fetch_next_answer(dashboard)
    model.answers.append(ANSWER)

    say(dashboard, "Open with the towing tank.")
    fetch_next_answer(dashboard)

    assert "A first version was asked for." in model.asked[-1]
    assert "Open with the towing tank." in model.asked[-1]
    page = dashboard.get(PAGE).text
    assert "No answer came" not in page
    assert difference_on(page) is not None


def test_saying_or_rejecting_in_a_folder_that_names_no_application_is_not_found(
    dashboard: TestClient, data_dir: Path
) -> None:
    write_brief(data_dir)

    for action in ("say", "reject"):
        missing = dashboard.post(
            f"/applications/tuhh/2026-09-01-gone/letter/{action}", data={"message": "Hello."}
        )

        assert missing.status_code == 404
    assert not (data_dir / "applications" / "tuhh" / "2026-09-01-gone").exists()


def test_an_answer_whose_body_is_the_one_that_waits_is_no_second_revision(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    before = difference_on(dashboard.get(PAGE).text)
    model.answers.append(f"I would keep it.\n\n<letter>\n{BODY}\n</letter>\n")
    say(dashboard, "Is that the best you can do?")

    fetch_next_answer(dashboard)

    page = dashboard.get(PAGE).text
    assert "I would keep it." in part(page, "conversation")
    assert difference_on(page) == before
    assert waiting_in(data_dir) == [BODY]
    assert marked_waiting_in(data_dir) == 1
    assert "Revision: Revised" not in part(page, "conversation")


def test_a_revision_rejected_while_it_was_being_revised_is_not_proposed_again(
    data_dir: Path,
    clock: SettableClock,
    network: Pages,
    committer: RememberedCommits,
    write_application: WriteApplication,
) -> None:
    class RejectedMeanwhile(ScriptedModel):
        """A model that takes so long over its second answer that the applicant rejects what
        it is building on before that answer has come."""

        def ask(self, prompt: str) -> str:
            answer = super().ask(prompt)
            if len(self.asked) == 2:
                reject(dashboard, dashboard.get(PAGE).text)
            return answer

    model = RejectedMeanwhile()
    dashboard = TestClient(
        create_app(
            data_dir=data_dir,
            clock=clock,
            network=network,
            committer=committer,
            model=model,
            sleep=lambda seconds: None,
        )
    )
    a_first_revision_waits(dashboard, data_dir, write_application, model)
    model.answers.append(REVISED)
    say(dashboard, "Shorter.")

    fetch_next_answer(dashboard)

    # It was asked to revise what waited then, and was not told of a rejection.
    assert "rejected" not in model.asked[-1]
    page = dashboard.get(PAGE).text
    conversation = part(page, "conversation")
    assert "Shorter, as you asked." in conversation
    assert "Revision: Rejected" in conversation
    assert "Revision: Dropped" in conversation
    assert difference_on(page) is None
    assert waiting_in(data_dir) == []
    assert "to measure." not in conversation_path(data_dir).read_text(encoding="utf-8")


def test_with_a_runner_a_message_is_answered_without_being_asked_for(
    data_dir: Path,
    clock: SettableClock,
    network: Pages,
    committer: RememberedCommits,
    model: ScriptedModel,
    write_application: WriteApplication,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    model.answers.append(ANSWER)
    dashboard = TestClient(
        create_app(
            data_dir=data_dir,
            clock=clock,
            network=network,
            committer=committer,
            model=model,
            sleep=lambda seconds: None,
            # Handed the work, this one does it on the spot.
            runner=lambda work: work(),
        )
    )

    said = say(dashboard, "Open with the towing tank.")

    assert "Open with the towing tank." in model.asked[0]
    assert "Here is a first version." in part(said, "conversation")
    assert difference_on(said) is not None
