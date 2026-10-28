"""LinkedIn's links and titles: the only rule for one site, and nothing of it is ever fetched.

No LinkedIn page is saved as a fixture, since one is reached signed in. LinkedIn appears here only
as invented titles, share texts and links, for an invented job ID.
"""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from conftest import Pages, RememberedCommits
from test_bringing_in import already_brought_in, create, field, opened, posting_in

FOLDER = "nordwind-robotics/2026-09-18-simulation-engineer"
JOB = "https://www.linkedin.com/jobs/view/3141592653"
TEXT = "You build simulations of towing tanks.\nYou measure before you believe."

SHAPES = [
    JOB,
    f"{JOB}/",
    f"{JOB}/?trackingId=abc%3D%3D&refId=xyz&trk=public_jobs_topcard-title",
    "https://www.linkedin.com/jobs/view/simulation-engineer-at-nordwind-robotics-3141592653/",
    "https://www.linkedin.com/jobs/search/?currentJobId=3141592653&keywords=simulation",
    "https://www.linkedin.com/jobs/collections/recommended/?currentJobId=3141592653",
    "https://de.linkedin.com/jobs/view/3141592653?position=1&pageNum=0",
    "https://linkedin.com/jobs/view/3141592653",
    "https://www.linkedin.com/comm/jobs/view/3141592653?trackingId=abc&refId=def&lipi=urn",
    "https://WWW.LinkedIn.com/jobs/view/3141592653#apply",
]


@pytest.mark.parametrize("shape", SHAPES)
def test_each_linkedin_address_of_a_job_is_stored_as_the_canonical_one(
    dashboard: TestClient, data_dir: Path, shape: str
) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=shape)

    assert posting_in(data_dir)["url"] == JOB


@pytest.mark.parametrize(
    ("first", "again"), [(first, again) for first in SHAPES[::3] for again in SHAPES]
)
def test_each_linkedin_address_of_a_job_finds_the_application_brought_in_under_another(
    dashboard: TestClient,
    data_dir: Path,
    committer: RememberedCommits,
    first: str,
    again: str,
) -> None:
    create(
        dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=first, text=TEXT
    )

    created = create(dashboard, company="Nordwind AG", role="Lead", link=again)

    assert created.headers["location"].split("?")[0].endswith(f"/applications/{FOLDER}")
    assert "This Posting was already brought in" in already_brought_in(dashboard, created)
    assert len(committer.commits) == 1


def test_another_linkedin_job_is_not_on_record(dashboard: TestClient, data_dir: Path) -> None:
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=JOB)

    create(
        dashboard,
        company="Nordwind Robotics",
        role="Test Engineer",
        link="https://www.linkedin.com/jobs/search/?currentJobId=2718281828",
    )

    assert len(list((data_dir / "applications").glob("*/*"))) == 2


def test_a_linkedin_address_that_names_no_job_is_kept_as_it_came(
    dashboard: TestClient, data_dir: Path
) -> None:
    link = "https://www.linkedin.com/company/nordwind-robotics/jobs/"

    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=link)

    assert posting_in(data_dir)["url"] == link


def test_a_short_link_is_kept_as_it_came_and_only_matches_itself(
    dashboard: TestClient, data_dir: Path
) -> None:
    short = "https://lnkd.in/eQ7xYz2B"
    create(dashboard, company="Nordwind Robotics", role="Simulation Engineer", link=short)

    assert posting_in(data_dir)["url"] == short
    assert "This Posting was already brought in" in already_brought_in(
        dashboard, create(dashboard, company="Nordwind AG", role="Lead", link=short)
    )
    create(dashboard, company="Nordwind Robotics", role="Test Engineer", link=JOB)
    create(dashboard, company="Helios Antriebe", role="Lead", link="https://lnkd.in/k9Wv3Lp1")
    assert len(list((data_dir / "applications").glob("*/*"))) == 3


@pytest.mark.parametrize(
    "title",
    [
        "Nordwind Robotics hiring Simulation Engineer in Hamburg, Germany | LinkedIn",
        "(3) Nordwind Robotics hiring Simulation Engineer in Hamburg, Hamburg, Germany | LinkedIn",
        "Nordwind Robotics hiring Simulation Engineer in Hamburg | LinkedIn",
    ],
)
def test_a_linkedin_page_title_fills_company_and_role_without_the_place(
    dashboard: TestClient, title: str
) -> None:
    page = opened(dashboard, title=title, link=JOB)

    assert field(page, "company") == "Nordwind Robotics"
    assert field(page, "role") == "Simulation Engineer"


def test_a_role_with_in_in_it_keeps_it_and_loses_only_the_place(dashboard: TestClient) -> None:
    page = opened(
        dashboard,
        title="Helios Antriebe hiring Engineer in Training in Kiel, Germany | LinkedIn",
        link=JOB,
    )

    assert field(page, "company") == "Helios Antriebe"
    assert field(page, "role") == "Engineer in Training"


def test_the_linkedin_apps_share_text_fills_the_link_and_the_company_and_leaves_the_role_empty(
    dashboard: TestClient, data_dir: Path
) -> None:
    page = opened(
        dashboard,
        text="Check out this job at Nordwind Robotics: "
        "https://www.linkedin.com/jobs/view/3141592653?trk=share",
    )

    assert field(page, "company") == "Nordwind Robotics"
    assert field(page, "role") == ""
    assert field(page, "link") == "https://www.linkedin.com/jobs/view/3141592653?trk=share"
    assert field(page, "text") == ""
    assert not (data_dir / "applications").exists()


def test_a_job_shared_from_the_app_is_created_without_a_text(
    dashboard: TestClient, data_dir: Path
) -> None:
    page = opened(dashboard, text=f"Check out this job at Nordwind Robotics: {JOB}?trk=share")

    create(
        dashboard,
        company=field(page, "company"),
        role="Simulation Engineer",
        link=field(page, "link"),
        text=field(page, "text"),
    )

    record = posting_in(data_dir)
    assert record["url"] == JOB
    assert "description" not in record


def test_nothing_is_fetched_for_a_linkedin_job(dashboard: TestClient, network: Pages) -> None:
    opened(dashboard, text=f"Check out this job at Nordwind Robotics: {JOB}")
    created = create(dashboard, company="Nordwind Robotics", role="Engineer", link=SHAPES[4])
    dashboard.get(created.headers["location"])
    opened(dashboard, title="Nordwind Robotics hiring Engineer in Kiel | LinkedIn", link=SHAPES[2])

    assert network.fetched == []
    assert network.rendered == []
