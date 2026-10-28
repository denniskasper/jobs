"""A Posting brought in by hand without its text, given it later, once.

Every title, link, company and Posting here is invented.
"""

import html
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
from starlette.testclient import TestClient

from conftest import Pages, RememberedCommits, ScriptedModel, fetch_next_answer
from test_bringing_in import FOLDER, LINK, TEXT, create, posting_in

PAGE = f"/applications/{FOLDER}"


def brought_in_without_text(dashboard: TestClient) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=LINK)


def text_field(page: str) -> str | None:
    """What the field for the Posting's text holds, or None when the page has no such field."""
    found = re.search(
        r'<form[^>]*action="([^"]*)"[^>]*>(?:(?!</form>).)*?'
        r'<textarea[^>]*name="text"[^>]*>(.*?)</textarea>',
        page,
        re.DOTALL,
    )
    if found is None:
        return None
    assert html.unescape(found[1]).endswith(f"{PAGE}/posting/text")
    return html.unescape(found[2])


def give(dashboard: TestClient, text: str) -> Any:
    return dashboard.post(f"{PAGE}/posting/text", data={"text": text}, follow_redirects=False)


@pytest.mark.parametrize("status", ["todo", "waiting", "interview", "closed"])
def test_an_application_whose_posting_has_no_text_shows_the_field_at_every_status(
    dashboard: TestClient, status: str
) -> None:
    brought_in_without_text(dashboard)
    dashboard.post(f"{PAGE}/status", data={"status": status})

    assert text_field(dashboard.get(PAGE).text) == ""


def test_a_posting_with_a_text_shows_no_field(dashboard: TestClient) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=LINK, text=TEXT)

    assert text_field(dashboard.get(PAGE).text) is None


def test_an_application_holding_no_posting_shows_no_field(dashboard: TestClient) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer")

    assert text_field(dashboard.get(PAGE).text) is None


def test_saving_writes_the_text_into_the_posting_in_one_commit_and_the_field_is_gone(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    brought_in_without_text(dashboard)
    before = posting_in(data_dir)

    saved = give(dashboard, TEXT.replace("\n", "\r\n") + "\r\n")

    assert saved.status_code == 303
    assert saved.headers["location"].endswith(PAGE)
    assert posting_in(data_dir) == {**before, "description": TEXT}
    assert len(committer.commits) == 2
    assert committer.commits[-1].paths == [data_dir / "applications" / FOLDER / "posting.yaml"]
    page = dashboard.get(PAGE).text
    assert text_field(page) is None
    assert TEXT in html.unescape(page)


def test_the_text_is_given_once(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    brought_in_without_text(dashboard)
    give(dashboard, TEXT)

    refused = give(dashboard, "Something else entirely.")

    assert refused.status_code == 422
    assert posting_in(data_dir)["description"] == TEXT
    assert len(committer.commits) == 2


def test_an_empty_text_is_refused_and_saves_nothing(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    brought_in_without_text(dashboard)

    refused = give(dashboard, "  \r\n ")

    assert refused.status_code == 422
    assert "description" not in posting_in(data_dir)
    assert len(committer.commits) == 1
    assert text_field(refused.text) is not None


def test_markup_in_the_saved_text_shows_as_text(dashboard: TestClient) -> None:
    brought_in_without_text(dashboard)

    give(dashboard, "<script>alert('run')</script>\n<b>Second</b> line")

    page = dashboard.get(PAGE).text
    assert (
        "&lt;script&gt;alert(&#39;run&#39;)&lt;/script&gt;\n&lt;b&gt;Second&lt;/b&gt; line" in page
    )
    assert "<script>alert" not in page
    assert "<b>Second" not in page


@pytest.mark.parametrize(
    "again",
    [
        {"link": f"{LINK}#apply", "text": TEXT},
        {"text": f"{TEXT}\n{LINK}?utm_source=share"},
    ],
)
def test_the_same_link_created_again_with_a_text_leads_to_the_field_filled_in_and_saves_nothing(
    dashboard: TestClient,
    data_dir: Path,
    committer: RememberedCommits,
    again: dict[str, str],
) -> None:
    brought_in_without_text(dashboard)

    led = dashboard.post("/applications", data={"company": "Nordwind AG", "role": "Lead", **again})

    assert led.status_code == 200
    assert "This Posting was already brought in" in led.text
    assert text_field(led.text) == TEXT
    assert "description" not in posting_in(data_dir)
    assert len(committer.commits) == 1
    assert [path.name for path in (data_dir / "applications").glob("*/*")] == [FOLDER.split("/")[1]]


def test_the_same_link_opened_again_with_a_text_leads_to_the_field_filled_in(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    brought_in_without_text(dashboard)
    query = urlencode(
        {"title": "Simulation Engineer at Nordwind Robotics", "link": LINK, "text": TEXT}
    )

    led = dashboard.get(f"/applications/new?{query}")

    assert led.status_code == 200
    assert "This Posting was already brought in" in led.text
    assert text_field(led.text) == TEXT
    assert "description" not in posting_in(data_dir)
    assert len(committer.commits) == 1


def test_the_same_link_opened_again_without_a_text_leads_to_the_empty_field(
    dashboard: TestClient,
) -> None:
    brought_in_without_text(dashboard)

    led = dashboard.get(f"/applications/new?{urlencode({'link': LINK})}")

    assert "This Posting was already brought in" in led.text
    assert text_field(led.text) == ""


@pytest.mark.parametrize("by", ["opening", "creating"])
def test_the_same_link_again_when_the_posting_has_a_text_changes_nothing(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits, by: str
) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=LINK, text=TEXT)
    other = "A text that came along later."
    entered = {"company": "Nordwind AG", "role": "Lead", "link": LINK, "text": other}

    if by == "opening":
        led = dashboard.get(f"/applications/new?{urlencode(entered)}")
    else:
        led = dashboard.post("/applications", data=entered)

    assert led.url.path == PAGE
    assert "This Posting was already brought in" in led.text
    assert other not in led.text
    assert text_field(led.text) is None
    assert posting_in(data_dir)["description"] == TEXT
    assert len(committer.commits) == 1


def test_a_letter_asked_for_while_the_posting_has_no_text_says_so_and_links_to_the_field(
    dashboard: TestClient, data_dir: Path, model: ScriptedModel
) -> None:
    (data_dir / "brief.md").write_text("I write plainly.", encoding="utf-8")
    brought_in_without_text(dashboard)
    model.answers.append("A first version.\n\n<letter>\nDear Sir or Madam,\n</letter>\n")

    asked = dashboard.post(f"{PAGE}/letter/ask")
    fetch_next_answer(dashboard)

    assert asked.status_code == 200
    assert len(model.asked) == 1
    page = dashboard.get(PAGE).text
    letter = page.split('id="letter-title"')[1].split("</section>")[0]
    assert "The Posting has no text" in letter
    (target,) = re.findall(r'<a href="#([^"]+)">', letter)
    assert re.search(rf'<textarea[^>]*id="{target}"[^>]*name="text"', page)


def test_the_letter_says_nothing_of_it_once_the_posting_has_its_text(
    dashboard: TestClient,
) -> None:
    brought_in_without_text(dashboard)
    give(dashboard, TEXT)

    letter = dashboard.get(PAGE).text.split('id="letter-title"')[1].split("</section>")[0]
    assert "The Posting has no text" not in letter


def test_nothing_is_fetched_while_the_text_is_given(dashboard: TestClient, network: Pages) -> None:
    brought_in_without_text(dashboard)
    dashboard.get(f"/applications/new?{urlencode({'link': LINK, 'text': TEXT})}")
    create(dashboard, company="Nordwind AG", role="Lead", link=LINK, text=TEXT)
    give(dashboard, TEXT)

    assert network.fetched == []
    assert network.rendered == []
