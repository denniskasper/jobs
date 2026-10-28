"""The page that creates an Application, and a Posting brought in by hand with it.

Every title, link, company and Posting here is invented.
"""

import html
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
import yaml
from starlette.testclient import TestClient

from conftest import Pages, RememberedCommits, ScriptedModel, fetch_next_answer
from test_proposals import write_posting

FOLDER = "nordwind-robotics/2026-09-18-simulation-engineer"
LINK = "https://www.stellenwerk.example/jobs/4711"
TEXT = "You build simulations of towing tanks.\nYou measure before you believe."


def opened(dashboard: TestClient, **query: str) -> str:
    """The page that creates an Application, opened at an address carrying `query`."""
    page = dashboard.get(f"/applications/new?{urlencode(query)}")
    assert page.status_code == 200
    text: str = page.text
    return text


def field(page: str, name: str) -> str:
    """What the form on a page holds in one field, as the browser would show it."""
    found = re.search(rf'<input[^>]*name="{name}"[^>]*value="([^"]*)"', page)
    if found is None:
        found = re.search(rf'<textarea[^>]*name="{name}"[^>]*>(.*?)</textarea>', page, re.DOTALL)
    assert found is not None, f"the form has no field {name}"
    return html.unescape(found[1])


def create(dashboard: TestClient, **entered: str) -> Any:
    return dashboard.post("/applications", data=entered, follow_redirects=False)


def posting_in(data_dir: Path, folder: str = FOLDER) -> dict[str, Any]:
    text = (data_dir / "applications" / folder / "posting.yaml").read_text(encoding="utf-8")
    record = yaml.safe_load(text)
    assert isinstance(record, dict)
    return record


def test_the_applications_page_leads_to_the_page_that_creates_one(dashboard: TestClient) -> None:
    listing = dashboard.get("/").text
    (link,) = re.findall(r'<a[^>]*href="([^"]+)"[^>]*>New Application</a>', listing)

    page = dashboard.get(html.unescape(link)).text

    assert "modal" not in listing
    assert "<script>\n  (function () {\n    var overlay" not in listing
    assert re.search(r'<form method="post" action="[^"]*/applications"', page)
    for name in ("company", "role", "link", "deadline", "text"):
        field(page, name)


def test_opened_with_a_title_a_link_and_a_text_it_shows_them_and_creates_nothing(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    page = opened(
        dashboard,
        title="Simulation Engineer at Nordwind Robotics | Stellenwerk",
        link=LINK,
        text=TEXT,
    )

    assert field(page, "company") == "Nordwind Robotics"
    assert field(page, "role") == "Simulation Engineer"
    assert field(page, "link") == LINK
    assert field(page, "text") == TEXT
    assert field(page, "deadline") == ""
    assert not (data_dir / "applications").exists()
    assert committer.commits == []


@pytest.mark.parametrize(
    ("title", "link", "company", "role"),
    [
        (
            "Simulation Engineer at Nordwind Robotics | Stellenwerk",
            LINK,
            "Nordwind Robotics",
            "Simulation Engineer",
        ),
        (
            "Simulation Engineer at Nordwind Robotics",
            "",
            "Nordwind Robotics",
            "Simulation Engineer",
        ),
        (
            "Nordwind Robotics is hiring a Simulation Engineer - Stellenwerk",
            LINK,
            "Nordwind Robotics",
            "Simulation Engineer",
        ),
        (
            "Helios Antriebe is hiring: Test Engineer (m/w/d) – Stellenwerk.example",
            LINK,
            "Helios Antriebe",
            "Test Engineer (m/w/d)",
        ),
        (
            "Prüfingenieur (m/w/d) bei Helios Antriebe · Jobbörse Nord",
            "https://jobboerse-nord.example/stelle/12",
            "Helios Antriebe",
            "Prüfingenieur (m/w/d)",
        ),
        (
            "Join us and shape the future of towing tanks | Stellenwerk",
            LINK,
            "",
            "Join us and shape the future of towing tanks",
        ),
        (
            "Simulation Engineer – Nordwind Robotics",
            LINK,
            "",
            "Simulation Engineer – Nordwind Robotics",
        ),
    ],
)
def test_a_title_proposes_the_company_and_role_a_reader_would(
    dashboard: TestClient, title: str, link: str, company: str, role: str
) -> None:
    page = opened(dashboard, title=title, link=link)

    assert field(page, "company") == company
    assert field(page, "role") == role


def test_a_company_or_role_in_the_address_is_shown_instead_of_a_guess(
    dashboard: TestClient,
) -> None:
    page = opened(
        dashboard, title="Simulation Engineer at Nordwind Robotics", company="Nordwind AG"
    )

    assert field(page, "company") == "Nordwind AG"
    assert field(page, "role") == ""


@pytest.mark.parametrize("text", [f"{LINK}\n{TEXT}", f"{TEXT}\n\n{LINK}", f"  {LINK}  "])
def test_a_link_at_the_start_or_end_of_the_text_becomes_the_link(
    dashboard: TestClient, text: str
) -> None:
    page = opened(dashboard, text=text)

    assert field(page, "link") == LINK
    assert field(page, "text") == ("" if text.strip() == LINK else TEXT)


def test_a_link_in_the_text_stays_there_when_a_link_was_given(dashboard: TestClient) -> None:
    page = opened(dashboard, link="https://www.stellenwerk.example/jobs/12", text=f"{TEXT}\n{LINK}")

    assert field(page, "link") == "https://www.stellenwerk.example/jobs/12"
    assert field(page, "text") == f"{TEXT}\n{LINK}"


def test_a_link_in_the_middle_of_the_text_stays_there(dashboard: TestClient) -> None:
    text = f"Apply at {LINK} until Friday."

    page = opened(dashboard, text=text)

    assert field(page, "link") == ""
    assert field(page, "text") == text


def test_create_writes_the_application_and_its_posting_in_one_commit_and_leads_to_its_page(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    created = create(
        dashboard,
        company="Nordwind Robotics",
        role="Simulation Engineer",
        link=LINK,
        deadline="2026-10-15",
        text=TEXT.replace("\n", "\r\n"),
    )

    assert created.status_code == 303
    assert created.headers["location"].endswith(f"/applications/{FOLDER}")
    record = yaml.safe_load(
        (data_dir / "applications" / FOLDER / "application.yaml").read_text(encoding="utf-8")
    )
    assert record["status"] == "todo"
    assert str(record["date"]) == "2026-09-18"
    posting = posting_in(data_dir)
    assert posting["company"] == "Nordwind Robotics"
    assert posting["role"] == "Simulation Engineer"
    assert posting["url"] == LINK
    assert posting["description"] == TEXT
    assert str(posting["deadline"]) == "2026-10-15"
    assert str(posting["brought_in_by_hand"]) == "2026-09-18"
    (commit,) = committer.commits
    directory = data_dir / "applications" / FOLDER
    assert {directory / "application.yaml", directory / "posting.yaml"} <= set(commit.paths)


def test_a_text_alone_brings_in_a_posting_without_a_link(
    dashboard: TestClient, data_dir: Path
) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", text=TEXT)

    posting = posting_in(data_dir)
    assert "url" not in posting
    assert "deadline" not in posting
    assert posting["description"] == TEXT


def test_a_link_alone_brings_in_a_posting_without_a_text(
    dashboard: TestClient, data_dir: Path
) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=LINK)

    posting = posting_in(data_dir)
    assert posting["url"] == LINK
    assert "description" not in posting


def test_a_link_handed_in_inside_the_text_is_kept_as_the_link(
    dashboard: TestClient, data_dir: Path
) -> None:
    create(
        dashboard, company="Nordwind Robotics", role="Simulation Engineer", text=f"{TEXT}\n{LINK}"
    )

    posting = posting_in(data_dir)
    assert posting["url"] == LINK
    assert posting["description"] == TEXT


def test_company_and_role_alone_create_an_application_with_no_posting(
    dashboard: TestClient, data_dir: Path
) -> None:
    created = create(
        dashboard, company="Nordwind Robotics", role="Simulation Engineer", link="", text="  "
    )

    assert created.status_code == 303
    assert created.headers["location"].endswith(f"/applications/{FOLDER}")
    assert not (data_dir / "applications" / FOLDER / "posting.yaml").exists()
    assert "This Application holds no Posting." in dashboard.get(created.headers["location"]).text


@pytest.mark.parametrize(
    ("entered", "complaint"),
    [
        ({"company": ""}, "company"),
        ({"deadline": "next Friday"}, "deadline"),
        ({"link": "javascript:alert(1)"}, "link"),
    ],
)
def test_a_refused_create_brings_the_form_back_with_everything_entered(
    dashboard: TestClient,
    data_dir: Path,
    committer: RememberedCommits,
    entered: dict[str, str],
    complaint: str,
) -> None:
    form = {
        "company": "Nordwind Robotics",
        "role": "Simulation Engineer",
        "link": LINK,
        "deadline": "2026-10-15",
        "text": TEXT,
        **entered,
    }

    refused = create(dashboard, **form)

    assert refused.status_code == 422
    assert complaint in refused.text.split("Not done.")[1].split("</p>")[0]
    for name, value in form.items():
        assert field(refused.text, name) == value
    assert not list((data_dir / "applications").glob("*/*"))
    assert committer.commits == []


def test_the_applications_page_shows_the_posting_and_says_it_was_brought_in_by_hand(
    dashboard: TestClient,
) -> None:
    created = create(
        dashboard,
        company="Nordwind Robotics",
        role="Simulation Engineer",
        link=LINK,
        deadline="2026-10-15",
        text="<script>alert('run')</script>\n<b>Second</b> line",
    )

    page = dashboard.get(created.headers["location"]).text

    posting = page.split('id="posting-title"')[1].split("</section>")[0]
    assert f'href="{LINK}"' in posting
    assert "2026-10-15" in posting
    assert "Brought in by hand on 2026-09-18" in posting
    assert "&lt;script&gt;alert(&#39;run&#39;)&lt;/script&gt;\n&lt;b&gt;Second&lt;/b&gt; line" in (
        posting
    )
    assert "<script>alert" not in page
    assert "<b>Second" not in page


def test_a_posting_brought_in_by_hand_is_on_no_sources_page_and_in_no_proposals(
    dashboard: TestClient,
) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=LINK, text=TEXT)

    for address in ("/sources", "/proposals", "/proposals/left-out"):
        page = dashboard.get(address).text
        assert "Simulation Engineer" not in page
        assert LINK not in page


def test_a_first_version_of_the_letter_is_written_from_the_postings_text(
    dashboard: TestClient, data_dir: Path, model: ScriptedModel
) -> None:
    (data_dir / "brief.md").write_text("I write plainly.", encoding="utf-8")
    created = create(
        dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=LINK, text=TEXT
    )
    model.answers.append("A first version.\n\n<letter>\nDear Sir or Madam,\n</letter>\n")
    page = dashboard.get(created.headers["location"]).text
    (action,) = re.findall(
        r'<form[^>]*action="([^"]+)"[^>]*>(?:(?!</form>).)*Ask for a first version',
        page,
        re.DOTALL,
    )
    dashboard.post(html.unescape(action))

    fetch_next_answer(dashboard)

    (prompt,) = model.asked
    assert TEXT in prompt


def test_nothing_is_fetched_while_a_posting_is_brought_in(
    dashboard: TestClient, network: Pages
) -> None:
    opened(dashboard, title="Simulation Engineer at Nordwind Robotics", link=LINK, text=TEXT)
    created = create(
        dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=LINK, text=TEXT
    )
    dashboard.get(created.headers["location"])
    create(dashboard, company="", role="Simulation Engineer", link=LINK, text=TEXT)

    assert network.fetched == []
    assert network.rendered == []


def already_brought_in(dashboard: TestClient, created: Any) -> str:
    """The page a Create that found its link on record leads to."""
    assert created.status_code == 303
    page: str = dashboard.get(created.headers["location"]).text
    return page


@pytest.mark.parametrize(
    "again",
    [
        LINK,
        f"{LINK}#apply",
        f"{LINK}?utm_source=newsletter&utm_medium=email",
        f"{LINK}?fbclid=abc123",
        "https://WWW.Stellenwerk.Example/jobs/4711",
    ],
)
def test_the_same_link_again_creates_nothing_and_leads_to_the_application_that_holds_it(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits, again: str
) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=LINK, text=TEXT)

    created = create(dashboard, company="Nordwind AG", role="Simulation lead", link=again)

    assert created.headers["location"].split("?")[0].endswith(f"/applications/{FOLDER}")
    assert "This Posting was already brought in" in already_brought_in(dashboard, created)
    assert [
        path.relative_to(data_dir / "applications").as_posix()
        for path in sorted((data_dir / "applications").glob("*/*"))
    ] == [FOLDER]
    assert len(committer.commits) == 1
    assert posting_in(data_dir)["description"] == TEXT


def test_a_link_handed_in_inside_the_text_is_found_on_record_too(
    dashboard: TestClient, data_dir: Path
) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=LINK, text=TEXT)

    created = create(dashboard, company="Nordwind AG", role="Lead", text=f"{TEXT}\n{LINK}#top")

    assert "This Posting was already brought in" in already_brought_in(dashboard, created)
    assert not (data_dir / "applications" / "nordwind-ag").exists()


def test_the_link_of_a_posting_pursued_at_the_pursue_gate_is_on_record(
    dashboard: TestClient, data_dir: Path, committer: RememberedCommits
) -> None:
    write_posting(data_dir, "doctoral-researcher")
    dashboard.get("/proposals")
    dashboard.post("/proposals", data={"posting": "hafenstadt/doctoral-researcher"})
    pursued = "hafenstadt-institute-of-technology/2026-09-18-doctoral-researcher"
    commits = len(committer.commits)

    created = create(
        dashboard,
        company="Hafenstadt Institute of Technology",
        role="Doctoral researcher",
        link="https://PORTAL.example/jobs/posting/doctoral-researcher?utm_campaign=autumn#top",
        text=TEXT,
    )

    assert created.headers["location"].split("?")[0].endswith(f"/applications/{pursued}")
    page = already_brought_in(dashboard, created)
    assert "This Posting was already brought in" in page
    assert TEXT not in page
    assert len(committer.commits) == commits
    assert [path.name for path in (data_dir / "applications").glob("*/*")] == [
        pursued.split("/")[1]
    ]


def test_a_different_link_is_not_on_record(dashboard: TestClient, data_dir: Path) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=LINK)

    created = create(
        dashboard, company="Nordwind Robotics", role="Test Engineer", link=f"{LINK}?page=2"
    )

    page = already_brought_in(dashboard, created)
    assert "This Posting was already brought in" not in page
    assert len(list((data_dir / "applications").glob("*/*"))) == 2


def test_applications_without_links_never_match(dashboard: TestClient, data_dir: Path) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", text=TEXT)
    create(dashboard, company="Helios Antriebe", role="Test Engineer")

    created = create(dashboard, company="Nordwind Robotics", role="Test Engineer", text=TEXT)

    assert "This Posting was already brought in" not in already_brought_in(dashboard, created)
    assert len(list((data_dir / "applications").glob("*/*"))) == 3


def test_the_applications_page_says_nothing_of_it_when_opened_on_its_own(
    dashboard: TestClient,
) -> None:
    created = create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=LINK)

    assert "already brought in" not in already_brought_in(dashboard, created)


def test_nothing_is_fetched_to_find_a_link_on_record(dashboard: TestClient, network: Pages) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=LINK)
    created = create(dashboard, company="Nordwind AG", role="Lead", link=f"{LINK}#top")
    dashboard.get(created.headers["location"])

    assert network.fetched == []
    assert network.rendered == []
