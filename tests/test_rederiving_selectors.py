"""Deriving the config of a selectors Source again, which keeps the selectors that still work,
through the HTTP surface.

Every page here is invented, as is every host.
"""

import html
import json
import re
from datetime import timedelta
from pathlib import Path

from starlette.testclient import TestClient

from conftest import Pages, RememberedCommits, ScriptedModel, SettableClock, register
from test_rederivation import fields_of
from test_registering_by_selectors import (
    CAREERS,
    FOLLOW_TO_OPENINGS,
    OPENING,
    POSITIONS,
    SELECTORS,
    listing_of,
    opening_page,
    proposed,
    read_source_file,
    serve_two_openings,
)

SAME_WAY = "reached and read the way they already are"


def register_example(dashboard: TestClient, network: Pages, model: ScriptedModel) -> Path:
    serve_two_openings(network)
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS, proposed(SELECTORS)]
    register(dashboard, CAREERS, label="Example")
    return Path("sources") / "example.yaml"


def changed(page: str) -> list[str]:
    """The lines of the difference a page shows that were removed or added."""
    lines = [html.unescape(line).strip() for line in page.splitlines()]
    return [line for line in lines if re.match(r"<(del|ins)[^>]*>[-+]", line)]


def test_selectors_that_still_read_the_pages_are_kept_and_the_model_not_asked_for_new_ones(
    dashboard: TestClient, network: Pages, model: ScriptedModel, clock: SettableClock
) -> None:
    register_example(dashboard, network, model)
    asked = len(model.asked)
    clock.now += timedelta(days=30)
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS]

    derived = dashboard.post("/sources/example/rederive")

    assert derived.status_code == 200
    assert SAME_WAY in " ".join(derived.text.split())
    assert all("derived:" in line for line in changed(derived.text))
    assert len(model.asked) == asked + 2


def test_a_selector_corrected_by_hand_that_still_works_is_kept_as_written(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    file = data_dir / register_example(dashboard, network, model)
    written = file.read_text(encoding="utf-8")
    assert "role: h1.opening" in written
    file.write_text(written.replace("role: h1.opening", "role: main > h1"), encoding="utf-8")
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS]

    derived = dashboard.post("/sources/example/rederive")
    dashboard.post("/sources/example/rederive/accept", data=fields_of(derived.text))

    assert derived.status_code == 200
    assert not any("h1" in line for line in changed(derived.text))
    config = read_source_file(data_dir, "example")["config"]
    assert config["selectors"] == {**SELECTORS, "role": "main > h1"}
    assert config["company"] == "Example GmbH"


def redesign_the_pages(network: Pages) -> None:
    """The role is no longer where the selectors look for it."""
    for number, role in ((1, "Doctoral researcher"), (2, "Laboratory engineer")):
        page = opening_page(role, f"EX-{number}").replace('class="opening"', 'class="title"')
        network.serve(OPENING.format(number), page)


def test_selectors_that_no_longer_work_are_replaced_and_the_difference_shows_old_and_new(
    dashboard: TestClient,
    data_dir: Path,
    network: Pages,
    model: ScriptedModel,
    committer: RememberedCommits,
) -> None:
    file = data_dir / register_example(dashboard, network, model)
    written = file.read_text(encoding="utf-8")
    redesign_the_pages(network)
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS, proposed({**SELECTORS, "role": "h1.title"})]

    derived = dashboard.post("/sources/example/rederive")

    lines = changed(derived.text)
    assert any(line.startswith("<del") and "role: h1.opening" in line for line in lines)
    assert any(line.startswith("<ins") and "role: h1.title" in line for line in lines)
    assert not any("section.text" in line for line in lines)
    assert SAME_WAY not in " ".join(derived.text.split())
    assert file.read_text(encoding="utf-8") == written

    dashboard.post("/sources/example/rederive/accept", data=fields_of(derived.text))

    config = read_source_file(data_dir, "example")["config"]
    assert config["selectors"] == {**SELECTORS, "role": "h1.title"}
    assert committer.commits[-1].message == "Derive Source example again"
    dashboard.post("/sources/example/poll")
    assert "2 Postings" in " ".join(dashboard.get("/sources/example").text.split())


def test_new_selectors_that_read_the_company_take_the_literal_company_out_of_the_config(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    register_example(dashboard, network, model)
    redesign_the_pages(network)
    selectors = {"role": "h1.title", "description": "section.text", "company": "h1.site"}
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS, proposed(selectors, company=None)]

    derived = dashboard.post("/sources/example/rederive")
    dashboard.post("/sources/example/rederive/accept", data=fields_of(derived.text))

    config = read_source_file(data_dir, "example")["config"]
    assert config["selectors"] == selectors
    assert "company" not in config


def test_a_changed_hop_with_working_selectors_shows_the_hop_in_the_difference_and_no_selector(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    register_example(dashboard, network, model)
    asked = len(model.asked)
    moved = "https://employer.example/careers/position/{}"
    links = "".join(f"<a href='position/{number}'>Position {number}</a>" for number in (1, 2))
    network.serve(CAREERS, f"<html><body><h1>Work with us</h1>{links}</body></html>")
    network.serve(moved.format(1), opening_page("Doctoral researcher", "EX-1"))
    network.serve(moved.format(2), opening_page("Laboratory engineer", "EX-2"))
    model.answers = [json.dumps({"follow": r"/careers/position/\d+"}), POSITIONS]

    derived = dashboard.post("/sources/example/rederive")

    lines = changed(derived.text)
    assert any(line.startswith("<del") and r"/careers/opening/\d+" in line for line in lines)
    assert any(line.startswith("<ins") and r"/careers/position/\d+" in line for line in lines)
    assert all("follow:" in line or "derived:" in line for line in lines)
    assert SAME_WAY not in " ".join(derived.text.split())
    assert len(model.asked) == asked + 2


def test_a_site_that_now_publishes_a_structured_record_moves_the_source_to_it(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    register_example(dashboard, network, model)
    record = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Doctoral researcher",
        "description": "<p>You run the wave flume.</p>",
        "hiringOrganization": {"name": "Example GmbH"},
    }
    network.serve(CAREERS, listing_of(1))
    network.serve(
        OPENING.format(1),
        opening_page("Doctoral researcher", "EX-1").replace(
            "<main>", f"<script type='application/ld+json'>{json.dumps(record)}</script><main>"
        ),
    )
    model.answers = [FOLLOW_TO_OPENINGS]

    derived = dashboard.post("/sources/example/rederive")
    dashboard.post("/sources/example/rederive/accept", data=fields_of(derived.text))

    config = read_source_file(data_dir, "example")["config"]
    assert config["strategy"] == "structured-record"
    assert "selectors" not in config
    assert "company" not in config
    assert config["hops"] == [{"follow": r"/careers/opening/\d+", "render": "static"}]


def test_new_selectors_that_cannot_be_made_to_work_refuse_and_leave_the_file_as_it_is(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    file = data_dir / register_example(dashboard, network, model)
    written = file.read_text(encoding="utf-8")
    asked = len(model.asked)
    redesign_the_pages(network)
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS] + [proposed(SELECTORS)] * 2

    refused = dashboard.post("/sources/example/rederive")

    assert refused.status_code == 422
    words = html.unescape(refused.text)
    assert "the page has no role: the selector 'h1.opening' found none" in words
    assert "The Source was left as it is" in words
    assert "registered" not in words
    assert file.read_text(encoding="utf-8") == written
    assert len(model.asked) == asked + 4


def test_what_a_human_put_in_a_selectors_config_survives_new_selectors(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_two_openings(network)
    model.answers = [
        FOLLOW_TO_OPENINGS,
        POSITIONS,
        proposed(SELECTORS),
        json.dumps({"field": "location", "pattern": "Hafenstadt"}),
    ]
    register(dashboard, CAREERS, label="Example", rule="only positions in Hafenstadt")
    file = data_dir / "sources" / "example.yaml"
    file.write_text(
        file.read_text(encoding="utf-8").replace("interval: 1.0", "interval: 5.0"), "utf-8"
    )
    redesign_the_pages(network)
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS, proposed({**SELECTORS, "role": "h1.title"})]

    derived = dashboard.post("/sources/example/rederive")
    dashboard.post("/sources/example/rederive/accept", data=fields_of(derived.text))

    record = read_source_file(data_dir, "example")
    assert record["label"] == "Example"
    assert record["config"]["interval"] == 5.0
    assert record["config"]["filters"] == [
        {"field": "location", "pattern": "Hafenstadt", "rule": "only positions in Hafenstadt"}
    ]


def test_where_the_pages_came_to_need_a_browser_and_there_is_none_the_selectors_stay(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    register_example(dashboard, network, model)
    shell = "<html><body><div id='app'></div></body></html>"
    network.serve(OPENING.format(1), shell)
    network.serve(OPENING.format(2), shell)
    model.answers = [FOLLOW_TO_OPENINGS, json.dumps({"browser": True})]

    derived = dashboard.post("/sources/example/rederive")
    dashboard.post("/sources/example/rederive/accept", data=fields_of(derived.text))

    config = read_source_file(data_dir, "example")["config"]
    assert "needs a browser" in config["unfinished"]
    assert config["strategy"] == "selectors"
    assert config["selectors"] == SELECTORS
    assert config["company"] == "Example GmbH"
