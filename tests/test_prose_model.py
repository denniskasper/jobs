"""The model that writes Letters can be another than the one registration asks: an app is handed
a second one for prose, and without it asks the first for both.

Every Application, Brief, page and answer here is invented.
"""

import json
from pathlib import Path

from starlette.testclient import TestClient

from conftest import (
    Pages,
    RememberedCommits,
    ScriptedModel,
    SettableClock,
    WriteApplication,
    fetch_next_answer,
    run_next_registration,
)
from jobs.web import create_app
from test_letter_revision import (
    ANSWER,
    FOLDER,
    PAGE,
    RECORD,
    ask_for_a_first_version,
    difference_on,
    write_brief,
)
from test_registrations import BAKERY, hand_in

# What the model answers about the bakery's page, which ends the Registration.
NO_POSTINGS = json.dumps({"nothing": "the page is about bread, not about positions"})


def ask_for_a_letter_and_register_a_source(
    dashboard: TestClient, data_dir: Path, network: Pages, write_application: WriteApplication
) -> None:
    """Have the app ask a model once for each: a first version of a Letter, and what to make of
    a page handed in as a Source."""
    write_application(FOLDER, RECORD)
    write_brief(data_dir)
    ask_for_a_first_version(dashboard)
    fetch_next_answer(dashboard)
    network.serve(BAKERY, "<html><body><a href='/bread'>Our bread</a></body></html>")
    hand_in(dashboard, BAKERY)
    run_next_registration(dashboard)


def test_an_app_handed_a_second_model_asks_it_for_a_revision_and_the_first_for_registration(
    data_dir: Path,
    clock: SettableClock,
    network: Pages,
    committer: RememberedCommits,
    model: ScriptedModel,
    write_application: WriteApplication,
) -> None:
    prose_model = ScriptedModel(name="scripted-prose-model", answers=[ANSWER])
    model.answers.append(NO_POSTINGS)
    dashboard = TestClient(
        create_app(
            data_dir=data_dir,
            clock=clock,
            network=network,
            committer=committer,
            model=model,
            prose_model=prose_model,
            sleep=lambda seconds: None,
        )
    )

    ask_for_a_letter_and_register_a_source(dashboard, data_dir, network, write_application)

    (about_the_letter,) = prose_model.asked
    assert "I want to work on test rigs." in about_the_letter
    (about_the_page,) = model.asked
    assert "Our bread" in about_the_page
    assert difference_on(dashboard.get(PAGE).text)
    assert "the page is about bread" in dashboard.get("/sources").text


def test_an_app_handed_one_model_asks_it_for_both(
    dashboard: TestClient,
    data_dir: Path,
    network: Pages,
    model: ScriptedModel,
    write_application: WriteApplication,
) -> None:
    model.answers += [ANSWER, NO_POSTINGS]

    ask_for_a_letter_and_register_a_source(dashboard, data_dir, network, write_application)

    about_the_letter, about_the_page = model.asked
    assert "I want to work on test rigs." in about_the_letter
    assert "Our bread" in about_the_page
