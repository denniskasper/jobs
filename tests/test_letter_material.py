"""What a Letter is written from: the Brief, the resume and the example Letters the applicant
placed in the data directory, and the Notes where an Application holds no Posting.

Every Application, Posting, Brief, resume, example and answer here is invented.
"""

from pathlib import Path

from starlette.testclient import TestClient

from conftest import RememberedCommits, ScriptedModel, WriteApplication, fetch_next_answer
from test_letter_revision import (
    ANSWER,
    BARE,
    FOLDER,
    PAGE,
    RECORD,
    ask_for_a_first_version,
    conversation_path,
    difference_on,
    part,
    write_brief,
)

RESUME = "# Erika Musterfrau\n\n- 2019 to 2024: test engineer at Beispielwerft, Hafenstadt\n"


def write_resume(data_dir: Path, text: str = RESUME) -> None:
    """Place the resume in the data directory, as the applicant does: by hand."""
    (data_dir / "resume.md").write_text(text, encoding="utf-8")


def write_posting(data_dir: Path) -> None:
    (data_dir / "applications" / FOLDER / "posting.yaml").write_text(
        "company: TUHH\nrole: Research Engineer\n"
        "description: You build test rigs for towing tanks.\n",
        encoding="utf-8",
    )


def test_with_a_resume_in_the_data_directory_the_prompt_carries_it(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_posting(data_dir)
    write_brief(data_dir)
    write_resume(data_dir)
    model.answers.append(ANSWER)
    ask_for_a_first_version(dashboard)

    fetch_next_answer(dashboard)

    (prompt,) = model.asked
    assert "2019 to 2024: test engineer at Beispielwerft, Hafenstadt" in prompt


def write_example(data_dir: Path, name: str, body: str) -> None:
    """Place the body of a Letter the applicant picked as an example, as they do: by hand."""
    (data_dir / "examples").mkdir(exist_ok=True)
    (data_dir / "examples" / name).write_text(body, encoding="utf-8")


def test_with_example_letters_in_the_data_directory_the_prompt_carries_each_of_them(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_posting(data_dir)
    write_brief(data_dir)
    write_example(data_dir, "beispielwerft.md", "Dear Mr. Mustermann,\n\nI weld what I draw.\n")
    write_example(data_dir, "hafenamt.md", "Dear Ms. Beispiel,\n\nI count ships for a living.\n")
    model.answers.append(ANSWER)
    ask_for_a_first_version(dashboard)

    fetch_next_answer(dashboard)

    (prompt,) = model.asked
    assert "I weld what I draw." in prompt
    assert "I count ships for a living." in prompt


def test_without_a_resume_the_page_names_it_as_missing_and_asking_works(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_posting(data_dir)
    write_brief(data_dir)
    write_example(data_dir, "beispielwerft.md", "Dear Mr. Mustermann,\n\nI weld what I draw.\n")
    model.answers.append(ANSWER)

    opened = dashboard.get(PAGE).text
    ask_for_a_first_version(dashboard)
    fetch_next_answer(dashboard)

    assert "resume.md" in part(opened, "letter")
    assert "examples" not in part(opened, "letter")
    assert difference_on(dashboard.get(PAGE).text) is not None


def test_with_no_examples_the_page_names_them_as_missing_and_asking_works(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_posting(data_dir)
    write_brief(data_dir)
    write_resume(data_dir)
    # A folder with nothing picked into it holds no examples either.
    (data_dir / "examples").mkdir()
    model.answers.append(ANSWER)

    opened = dashboard.get(PAGE).text
    ask_for_a_first_version(dashboard)
    fetch_next_answer(dashboard)

    assert "examples" in part(opened, "letter")
    assert "resume.md" not in part(opened, "letter")
    assert difference_on(dashboard.get(PAGE).text) is not None


def test_with_a_resume_and_examples_the_page_names_nothing_as_missing(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    write_resume(data_dir)
    write_example(data_dir, "beispielwerft.md", "Dear Mr. Mustermann,\n\nI weld what I draw.\n")

    opened = dashboard.get(PAGE).text

    assert "resume.md" not in part(opened, "letter")
    assert "examples" not in part(opened, "letter")


def test_without_a_posting_and_without_notes_a_first_revision_is_refused_and_the_page_says_why(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    committer: RememberedCommits,
) -> None:
    write_application(FOLDER, BARE)
    write_brief(data_dir)
    model.answers.append(ANSWER)

    refused = dashboard.post(f"{PAGE}/letter/ask")

    assert refused.status_code == 422
    assert "holds no Posting" in refused.text
    assert "Notes" in refused.text
    fetch_next_answer(dashboard)
    assert model.asked == []
    assert not conversation_path(data_dir).exists()
    assert committer.commits == []
    # Nothing was asked, so it can be once the Notes are written.
    assert "Ask for a first version" in dashboard.get(PAGE).text


def test_without_a_posting_and_with_notes_a_first_revision_can_be_asked_for(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, BARE + "notes: They build towing tanks, and I want to test them.\n")
    write_brief(data_dir)
    model.answers.append(ANSWER)

    ask_for_a_first_version(dashboard)
    fetch_next_answer(dashboard)

    (prompt,) = model.asked
    assert "They build towing tanks, and I want to test them." in prompt
    assert difference_on(dashboard.get(PAGE).text) is not None


def test_with_a_posting_and_without_notes_a_first_revision_can_be_asked_for(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, BARE)
    write_posting(data_dir)
    write_brief(data_dir)
    model.answers.append(ANSWER)

    ask_for_a_first_version(dashboard)
    fetch_next_answer(dashboard)

    assert difference_on(dashboard.get(PAGE).text) is not None


def said_about_the_language(prompt: str) -> str:
    """The paragraph of a prompt that says which language to write in."""
    (said,) = [each for each in prompt.split("\n\n") if "language" in each]
    return said


def test_with_a_posting_the_prompt_asks_for_its_language_unless_the_applicant_says_otherwise(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, BARE)
    write_posting(data_dir)
    write_brief(data_dir)
    model.answers.append(ANSWER)
    ask_for_a_first_version(dashboard)

    fetch_next_answer(dashboard)

    said = said_about_the_language(model.asked[0])
    assert "Posting" in said
    assert "Notes" in said
    assert "Conversation" in said
    assert "Brief" not in said


def test_without_a_posting_the_prompt_asks_for_the_language_the_brief_names(
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

    said = said_about_the_language(model.asked[0])
    assert "Brief" in said
    assert "Posting" not in said


def test_a_resume_that_cannot_be_read_is_named_and_stops_nothing(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    (data_dir / "resume.md").write_bytes(b"\xff\xfe not text")
    write_example(data_dir, "unreadable.md", "")
    (data_dir / "examples" / "unreadable.md").write_bytes(b"\xff\xfe not text")
    write_example(data_dir, "beispielwerft.md", "Dear Mr. Mustermann,\n\nI weld what I draw.\n")
    model.answers.append(ANSWER)

    opened = dashboard.get(PAGE).text
    ask_for_a_first_version(dashboard)
    fetch_next_answer(dashboard)

    assert "resume.md could not be read" in part(opened, "letter")
    assert "unreadable.md could not be read" in part(opened, "letter")
    assert "I weld what I draw." in model.asked[0]
    assert difference_on(dashboard.get(PAGE).text) is not None


def test_a_posting_that_cannot_be_read_refuses_a_first_revision_and_says_what_to_fix(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
) -> None:
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    (data_dir / "applications" / FOLDER / "posting.yaml").write_text(
        "company: [unclosed\n", encoding="utf-8"
    )

    refused = dashboard.post(f"{PAGE}/letter/ask")

    assert refused.status_code == 422
    assert "fix the Posting" in refused.text
    assert not conversation_path(data_dir).exists()
