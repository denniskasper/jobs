"""A first Revision of a Letter: asked for with a button on the Application's page, fetched in the
background, shown as a difference, and written to the Letter when accepted.

Every Application, Posting, Brief, Notes and answer here is invented.
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

FOLDER = "tuhh/2026-09-01-research-engineer"
# An Application with nothing written down about it, and the one most tests ask about: it holds
# no Posting, and a first version is asked for only where there are Notes then.
BARE = "company: TUHH\nrole: Research Engineer\ndate: 2026-09-01\nstatus: todo\n"
RECORD = BARE + "notes: I want to work on test rigs.\n"
PAGE = f"/applications/{FOLDER}"

BRIEF = "I write plainly and in the first person. I never claim to have led a team."

BODY = (
    "**Application as Research Engineer**\n\n"
    "Dear Dr. Musterfrau,\n\n"
    "towing tanks are where I learned to measure before I believe.\n\n"
    "Kind regards"
)
ANSWER = f"Here is a first version. I left out the salary.\n\n<letter>\n{BODY}\n</letter>\n"


def write_brief(data_dir: Path, text: str = BRIEF) -> None:
    """Place the Brief in the data directory, as the applicant does: by hand."""
    (data_dir / "brief.md").write_text(text, encoding="utf-8")


def ask_for_a_first_version(dashboard: TestClient) -> str:
    """Press the button the page offers, and give back the page it leads to."""
    opened = dashboard.get(PAGE).text
    (action,) = re.findall(
        r'<form[^>]*action="([^"]+)"[^>]*>(?:(?!</form>).)*Ask for a first version',
        opened,
        re.DOTALL,
    )
    asked: str = dashboard.post(html.unescape(action)).text
    return asked


def accept(dashboard: TestClient, page: str) -> str:
    """Press Accept under the difference a page shows, and give back the page it leads to."""
    (action,) = re.findall(
        r'<form[^>]*action="([^"]+)"[^>]*>(?:(?!</form>).)*>Accept<', page, re.DOTALL
    )
    accepted: str = dashboard.post(html.unescape(action)).text
    return accepted


def part(page: str, title: str) -> str:
    """One of the three parts of the page, as the words a reader gets."""
    (found,) = re.findall(
        rf'<section[^>]*aria-labelledby="{title}-title">(.*?)</section>', page, re.DOTALL
    )
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", found)).split())


def difference_on(page: str) -> list[list[tuple[str, str]]] | None:
    """The difference a page shows, paragraph for paragraph: what was added, what was removed
    and what stayed, in order. None when the page shows none."""
    found = re.search(r'<div class="difference[^"]*"[^>]*>(.*?)</div>', page, re.DOTALL)
    if not found:
        return None
    paragraphs = []
    for paragraph in re.findall(r"<p>(.*?)</p>", found[1], re.DOTALL):
        spans = re.findall(r"<(ins|del)>(.*?)</\1>|([^<]+)", paragraph, re.DOTALL)
        paragraphs.append(
            [
                (
                    {"ins": "added", "del": "removed", "": "same"}[tag],
                    html.unescape(re.sub(r"<[^>]+>", "", text or same)),
                )
                for tag, text, same in spans
            ]
        )
    return paragraphs


def letter_path(data_dir: Path) -> Path:
    return data_dir / "applications" / FOLDER / "letter.md"


def conversation_path(data_dir: Path) -> Path:
    return data_dir / "applications" / FOLDER / "conversation.yaml"


def test_without_a_brief_asking_is_refused_and_the_page_says_what_to_write(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)

    refused = dashboard.post(f"{PAGE}/letter/ask")

    assert refused.status_code == 422
    assert "There is no Brief" in refused.text
    assert "brief.md" in refused.text
    fetch_next_answer(dashboard)
    assert model.asked == []
    assert not conversation_path(data_dir).exists()
    # Nothing was asked, so it can be once the Brief is written.
    assert "Ask for a first version" in dashboard.get(PAGE).text


def test_a_brief_with_nothing_in_it_is_no_brief(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir, " \n")

    refused = dashboard.post(f"{PAGE}/letter/ask")

    assert refused.status_code == 422
    assert "There is no Brief" in refused.text


def test_asking_shows_the_request_as_under_way_and_asks_the_model_nothing_yet(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    committer: RememberedCommits,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    model.answers.append(ANSWER)

    asked = ask_for_a_first_version(dashboard)

    assert model.asked == []
    assert "An answer is under way" in part(asked, "conversation")
    assert "A first version was asked for." in part(asked, "conversation")
    assert "Ask for a first version" not in asked
    assert not letter_path(data_dir).exists()
    assert committer.commits == []


def test_a_first_version_cannot_be_asked_for_twice(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    model.answers += [ANSWER, ANSWER]
    ask_for_a_first_version(dashboard)

    refused = dashboard.post(f"{PAGE}/letter/ask")

    assert refused.status_code == 422
    assert "already been asked for" in refused.text
    fetch_next_answer(dashboard)
    fetch_next_answer(dashboard)
    assert len(model.asked) == 1


def test_the_prompt_carries_the_brief_the_notes_and_the_posting(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, BARE + "notes: Mention the towing tank in Hafenstadt.\n")
    (data_dir / "applications" / FOLDER / "posting.yaml").write_text(
        "company: TUHH\nrole: Research Engineer\nlocation: Hafenstadt\n"
        "description: You build test rigs for towing tanks.\n",
        encoding="utf-8",
    )
    write_brief(data_dir)
    model.answers.append(ANSWER)
    ask_for_a_first_version(dashboard)

    fetch_next_answer(dashboard)

    (prompt,) = model.asked
    assert BRIEF in prompt
    assert "Mention the towing tank in Hafenstadt." in prompt
    assert "You build test rigs for towing tanks." in prompt
    assert "Research Engineer" in prompt


def test_the_prompt_carries_neither_the_employers_address_nor_a_date(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    clock: SettableClock,
) -> None:
    write_application(
        FOLDER, BARE + "address: |-\n  Am Beispielkai 7\n  21073 Hafenstadt\nnotes: Be brief.\n"
    )
    (data_dir / "applications" / FOLDER / "posting.yaml").write_text(
        "company: TUHH\nrole: Research Engineer\ndeadline: 2026-10-15\nposted: 2026-08-20\n"
        "description: You build test rigs.\n",
        encoding="utf-8",
    )
    write_brief(data_dir)
    model.answers.append(ANSWER)
    ask_for_a_first_version(dashboard)

    fetch_next_answer(dashboard)

    (prompt,) = model.asked
    assert "Beispielkai" not in prompt
    assert "21073" not in prompt
    assert str(clock.now.year) not in prompt


def test_an_answer_shows_its_message_and_its_body_as_a_difference_against_nothing(
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

    page = dashboard.get(PAGE).text
    assert "Here is a first version. I left out the salary." in part(page, "conversation")
    assert "under way" not in part(page, "conversation")
    assert difference_on(page) == [
        [("added", "Application as Research Engineer")],
        [("added", "Dear Dr. Musterfrau,")],
        [("added", "towing tanks are where I learned to measure before I believe.")],
        [("added", "Kind regards")],
    ]
    # The delimiters are the code's, and never the reader's.
    assert "<letter>" not in html.unescape(page)
    assert not letter_path(data_dir).exists()
    assert [commit.paths for commit in committer.commits] == [[conversation_path(data_dir)]]
    assert BODY in yaml.safe_load(conversation_path(data_dir).read_text(encoding="utf-8")).values()


def test_a_first_version_reads_as_the_letter_it_would_make_with_nothing_marked(
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

    page = dashboard.get(PAGE).text

    # Everything in it is new, so marking it says nothing: it is set as it will read.
    assert '<div class="difference first" data-citable>' in page
    assert "<strong>Application as Research Engineer</strong>" in page
    assert "**" not in part(page, "letter")
    assert "A first version is waiting" in part(page, "letter")


def test_a_models_message_reads_as_the_markdown_it_was_written_in(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    model.answers.append(
        "What I did:\n\n- **The hook**: test rigs.\n- <b>No markup</b> runs.\n\n"
        f"<letter>\n{BODY}\n</letter>\n"
    )
    ask_for_a_first_version(dashboard)
    fetch_next_answer(dashboard)

    page = dashboard.get(PAGE).text

    assert "<li><strong>The hook</strong>: test rigs.</li>" in page
    assert "&lt;b&gt;No markup&lt;/b&gt; runs." in page
    assert "**" not in part(page, "conversation")


def test_accepting_writes_the_letter_and_commits_it_with_the_conversation(
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

    accepted = accept(dashboard, dashboard.get(PAGE).text)

    assert letter_path(data_dir).read_text(encoding="utf-8") == BODY + "\n"
    assert [sorted(commit.paths) for commit in committer.commits[1:]] == [
        [conversation_path(data_dir), letter_path(data_dir)]
    ]
    # The Conversation keeps what became of the Revision, and no longer its text.
    kept = conversation_path(data_dir).read_text(encoding="utf-8")
    assert "towing tanks are where" not in kept
    assert "accepted" in kept
    assert "Here is a first version." in kept
    assert difference_on(accepted) is None
    assert "towing tanks are where I learned to measure before I believe." in part(
        accepted, "letter"
    )
    assert "Accepted" in part(accepted, "conversation")
    assert "Ask for a first version" not in accepted


def test_accepting_when_no_revision_waits_is_refused_and_writes_nothing(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    committer: RememberedCommits,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)

    refused = dashboard.post(f"{PAGE}/letter/accept")

    assert refused.status_code == 422
    assert "No Revision is waiting" in refused.text
    assert not letter_path(data_dir).exists()
    assert committer.commits == []


def test_an_accepted_letter_that_could_not_be_committed_says_so_and_is_written(
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
    committer.commit_failure = "the disk is full"

    accepted = dashboard.post(f"{PAGE}/letter/accept")

    assert accepted.status_code == 500
    assert "could not be committed: the disk is full" in accepted.text
    assert letter_path(data_dir).read_text(encoding="utf-8") == BODY + "\n"


def test_a_difference_is_at_word_level_within_the_paragraphs_of_the_letter_as_it_stands(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)
    letter_path(data_dir).write_text(
        "Dear Dr. Musterfrau,\n\nI build test rigs for towing tanks.\n\nI like boats.\n\n"
        "Kind regards\n",
        encoding="utf-8",
    )
    conversation_path(data_dir).write_text(
        "messages:\n"
        "- from: applicant\n  text: Say more about the rigs.\n"
        "- from: model\n  text: Done.\n  revision: waiting\n"
        "waiting: |-\n"
        "  Dear Dr. Musterfrau,\n\n"
        "  I build and calibrate test rigs for wave tanks.\n\n"
        "  Kind regards\n\n"
        "  P.S. I sail.\n",
        encoding="utf-8",
    )

    page = dashboard.get(PAGE).text

    assert difference_on(page) == [
        [("same", "Dear Dr. Musterfrau,")],
        [
            ("same", "I build "),
            ("added", "and calibrate "),
            ("same", "test rigs for "),
            ("removed", "towing "),
            ("added", "wave "),
            ("same", "tanks."),
        ],
        [("removed", "I like boats.")],
        [("same", "Kind regards")],
        [("added", "P.S. I sail.")],
    ]


def test_a_model_that_cannot_be_asked_leaves_the_request_without_an_answer(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    committer: RememberedCommits,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    ask_for_a_first_version(dashboard)

    fetch_next_answer(dashboard)

    page = dashboard.get(PAGE).text
    assert "under way" not in part(page, "conversation")
    assert "No answer came" in part(page, "conversation")
    assert difference_on(page) is None
    assert not letter_path(data_dir).exists()
    assert committer.commits == []


def test_an_answer_whose_body_cannot_be_told_from_its_message_proposes_nothing(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    committer: RememberedCommits,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    model.answers.append(f"Here it is.\n\n<letter>\n{BODY}\n")
    ask_for_a_first_version(dashboard)

    fetch_next_answer(dashboard)

    page = dashboard.get(PAGE).text
    assert difference_on(page) is None
    assert "Here it is." not in page
    assert committer.commits == []


def test_a_conversation_that_cannot_be_read_says_why_and_the_page_still_opens(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    conversation_path(data_dir).write_text("messages: [unclosed\n", encoding="utf-8")

    page = dashboard.get(PAGE)
    refused = dashboard.post(f"{PAGE}/letter/ask")

    assert page.status_code == 200
    assert "not valid YAML" in part(page.text, "conversation")
    assert "Ask for a first version" not in page.text
    assert refused.status_code == 422
    assert conversation_path(data_dir).read_text(encoding="utf-8") == "messages: [unclosed\n"


def test_asking_about_a_folder_that_names_no_application_is_not_found(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)

    for action in ("ask", "accept"):
        missing = dashboard.post(f"/applications/tuhh/2026-09-01-gone/letter/{action}")

        assert missing.status_code == 404
    assert not (data_dir / "applications" / "tuhh" / "2026-09-01-gone").exists()


def test_with_a_runner_the_answer_is_fetched_without_being_asked_for(
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

    asked = ask_for_a_first_version(dashboard)

    assert len(model.asked) == 1
    assert difference_on(asked) is not None
