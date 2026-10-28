"""Citing: text selected in the Letter, the Posting or a model's answer is quoted into the
message about the Letter, in the real browser.

Skips where Chromium cannot start, as the rendering tests do. Every Application, Posting and
person here is invented.
"""

from pathlib import Path

import pytest
from playwright.sync_api import Page
from starlette.applications import Starlette
from starlette.testclient import TestClient

from conftest import ScriptedModel, WriteApplication, fetch_next_answer
from test_fetching import no_chromium
from test_letter_answers import new_app  # noqa: F401  # a fixture
from test_letter_revision import (
    ANSWER,
    FOLDER,
    PAGE,
    RECORD,
    ask_for_a_first_version,
    write_brief,
)
from test_waiting import in_the_browser

POSTING = """\
company: TUHH
role: Research Engineer
description: You will build test rigs for towing tanks and keep their data in order.
"""


def select(page: Page, within: str, text: str) -> None:
    """Select `text` where it stands inside `within`, as a reader does with the mouse."""
    page.evaluate(
        """([within, text]) => {
            const walker = document.createTreeWalker(
                document.querySelector(within), NodeFilter.SHOW_TEXT);
            for (let node = walker.nextNode(); node; node = walker.nextNode()) {
                const at = node.data.indexOf(text);
                if (at < 0) continue;
                const range = document.createRange();
                range.setStart(node, at);
                range.setEnd(node, at + text.length);
                const selection = getSelection();
                selection.removeAllRanges();
                selection.addRange(range);
                document.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                return;
            }
            throw new Error('not found: ' + text);
        }""",
        [within, text],
    )


@pytest.fixture
def waiting_first_version(
    data_dir: Path,
    write_application: WriteApplication,
    model: ScriptedModel,
    new_app: Starlette,  # noqa: F811
) -> Starlette:
    """An Application with a Posting, whose first version waits with the model's answer."""
    write_application(FOLDER, RECORD)
    (data_dir / "applications" / FOLDER / "posting.yaml").write_text(POSTING, encoding="utf-8")
    write_brief(data_dir)
    dashboard = TestClient(new_app)
    model.answers.append(ANSWER)
    ask_for_a_first_version(dashboard)
    fetch_next_answer(dashboard)
    return new_app


def test_text_selected_in_the_letter_the_posting_and_an_answer_is_quoted_into_the_message(
    waiting_first_version: Starlette,
) -> None:
    if reason := no_chromium():
        pytest.skip(reason)
    with in_the_browser(waiting_first_version) as (page, origin):
        page.goto(f"{origin}{PAGE}")
        cite = page.get_by_role("button", name="Cite")
        assert not cite.is_visible()

        select(page, "#letter", "learned to measure before I believe")
        cite.click()

        assert page.input_value("#message") == "> learned to measure before I believe\n\n"
        # What is typed goes below the quote.
        assert page.evaluate("document.activeElement.id") == "message"
        assert not cite.is_visible()

        page.keyboard.type("Say it without believe.")
        select(page, "#posting", "keep their data in order")
        cite.click()
        select(page, "#conversation .written-by-model", "I left out the salary.")
        cite.click()

        assert page.input_value("#message") == (
            "> learned to measure before I believe\n\n"
            "Say it without believe.\n\n"
            "> keep their data in order\n\n"
            "> I left out the salary.\n\n"
        )


def test_text_selected_elsewhere_or_across_parts_offers_nothing_to_cite(
    waiting_first_version: Starlette,
) -> None:
    if reason := no_chromium():
        pytest.skip(reason)
    with in_the_browser(waiting_first_version) as (page, origin):
        page.goto(f"{origin}{PAGE}")
        cite = page.get_by_role("button", name="Cite")

        select(page, ".filed", "TUHH")
        assert not cite.is_visible()

        # From the Posting into the Letter: no one passage of one text.
        page.evaluate(
            """() => {
                const range = document.createRange();
                range.setStart(document.querySelector('#posting .posting-text').firstChild, 9);
                range.setEnd(document.querySelectorAll('#letter .difference ins')[1].firstChild, 3);
                getSelection().removeAllRanges();
                getSelection().addRange(range);
                document.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
            }"""
        )
        assert not cite.is_visible()
