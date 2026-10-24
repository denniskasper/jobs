"""A careers page that lists its few positions on the one page, with no posting pages behind
them, through the HTTP surface.

Every page here is invented, as is every host.
"""

import html
import json
from pathlib import Path

from starlette.testclient import TestClient

from conftest import Pages, ScriptedModel, register
from test_registering_by_selectors import POSITIONS, kept, proposed, read_source_file
from test_source_health import shown

CAREERS = "https://tidewater.example/work-with-us"

OPENINGS = (
    ("Electronics technician", "You assemble and test the gauge electronics."),
    ("Field service engineer", "You install gauges along the coast."),
    ("Office assistant", "You keep the order book."),
)


def opening(role: str | None, tasks: str) -> str:
    heading = f"<h3>{role}</h3>" if role else ""
    body = f'<div class="body"><p>{tasks}</p></div>'
    return f'<li class="opening"><p class="kind">Vacancy</p>{heading}{body}</li>'


def careers_page(*openings: tuple[str | None, str]) -> str:
    items = "".join(opening(role, tasks) for role, tasks in openings)
    return f"""<html><body>
<header><h1 class="site">Tidewater Instruments</h1></header>
<main><h2>Open positions</h2><ul class="openings">{items}</ul></main>
</body></html>"""


SELECTORS = {"item": "li.opening", "role": "h3", "description": ".body"}
COMPANY = "Tidewater Instruments"


def registered_careers_page(dashboard: TestClient, network: Pages, model: ScriptedModel) -> None:
    network.serve(CAREERS, careers_page(*OPENINGS))
    model.answers = [POSITIONS, proposed(SELECTORS, company=COMPANY)]
    register(dashboard, CAREERS, label="Tidewater")


def test_a_careers_page_with_no_posting_pages_registers_and_the_config_holds_item(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    registered_careers_page(dashboard, network, model)

    config = read_source_file(data_dir, "tidewater")["config"]
    assert config["strategy"] == "selectors"
    assert config["hops"] == []
    assert config["selectors"] == SELECTORS
    assert config["company"] == COMPANY


def test_the_model_is_told_that_item_is_for_a_page_carrying_several_positions(
    dashboard: TestClient, network: Pages, model: ScriptedModel
) -> None:
    registered_careers_page(dashboard, network, model)

    selectors_question = model.asked[1]
    assert '"item"' in selectors_question.partition("content taken from the web")[0]


def test_its_postings_are_three_files_and_a_second_poll_writes_over_the_same_three(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    registered_careers_page(dashboard, network, model)

    dashboard.post("/sources/tidewater/poll")
    files = sorted((data_dir / "postings" / "tidewater").glob("*.yaml"))
    dashboard.post("/sources/tidewater/poll")

    assert sorted((data_dir / "postings" / "tidewater").glob("*.yaml")) == files
    postings = kept(data_dir, "tidewater")
    assert sorted(posting["role"] for posting in postings) == sorted(role for role, _ in OPENINGS)
    assert {posting["url"] for posting in postings} == {CAREERS}
    assert {posting["identity"]["by"] for posting in postings} == {"content"}
    assert len(model.asked) == 2


def test_a_listing_reached_through_a_hop_never_gives_its_url_to_its_only_posting(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    home = "https://tidewater.example/"
    network.serve(home, "<html><body><a href='/work-with-us'>Work with us</a></body></html>")
    network.serve(CAREERS, careers_page(OPENINGS[0]))
    follow = json.dumps({"follow": "/work-with-us$"})
    model.answers = [follow, POSITIONS, proposed(SELECTORS, company=COMPANY)]
    register(dashboard, home, label="Tidewater")

    dashboard.post("/sources/tidewater/poll")

    [posting] = kept(data_dir, "tidewater")
    assert posting["identity"]["by"] == "content"


def test_items_that_all_carry_the_same_role_fail_the_proposed_selectors(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    network.serve(CAREERS, careers_page(*OPENINGS))
    kind_as_role = {**SELECTORS, "role": ".kind"}
    model.answers = [POSITIONS] + [proposed(kind_as_role, company=COMPANY)] * 2

    refused = html.unescape(register(dashboard, CAREERS, label="Tidewater"))

    assert "read the same role, 'Vacancy', off all 3 Postings" in refused
    assert CAREERS in refused
    assert "Nothing was registered" in refused
    assert not (data_dir / "sources").exists()
    assert len(model.asked) == 3


def test_a_page_where_item_matches_nothing_is_quiet_and_in_time_unhealthy(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    registered_careers_page(dashboard, network, model)
    network.serve(CAREERS, careers_page())

    dashboard.post("/sources/tidewater/poll")
    once = shown(dashboard.get("/sources/tidewater").text)
    dashboard.post("/sources/tidewater/poll")
    dashboard.post("/sources/tidewater/poll")
    thrice = shown(dashboard.get("/sources/tidewater").text)

    assert "Quiet" in once
    assert "Unhealthy" not in once
    assert "could not be read" not in once
    assert "Unhealthy" in thrice


def test_an_item_lacking_a_role_fails_the_page_and_flags_the_source_at_once(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    registered_careers_page(dashboard, network, model)
    network.serve(CAREERS, careers_page(OPENINGS[0], (None, "You do something."), OPENINGS[2]))

    dashboard.post("/sources/tidewater/poll")

    page = html.unescape(shown(dashboard.get("/sources/tidewater").text))
    assert "Unhealthy" in page
    assert "item 2 of 'li.opening' has no role: the selector 'h3' found none" in page
    assert kept(data_dir, "tidewater") == []


def test_deriving_again_keeps_an_item_that_still_reads_the_page_and_asks_for_no_selectors(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    registered_careers_page(dashboard, network, model)
    model.answers = [POSITIONS]

    derived = dashboard.post("/sources/tidewater/rederive")

    assert derived.status_code == 200
    assert "came to the file as it is" in shown(derived.text)
    assert len(model.asked) == 3
    assert read_source_file(data_dir, "tidewater")["config"]["selectors"] == SELECTORS


def test_an_empty_listing_reached_through_a_hop_is_no_error(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    home = "https://tidewater.example/"
    network.serve(home, "<html><body><a href='/work-with-us'>Work with us</a></body></html>")
    network.serve(CAREERS, careers_page(*OPENINGS))
    follow = json.dumps({"follow": "/work-with-us$"})
    model.answers = [follow, POSITIONS, proposed(SELECTORS, company=COMPANY)]
    register(dashboard, home, label="Tidewater")
    network.serve(CAREERS, careers_page())

    dashboard.post("/sources/tidewater/poll")

    page = shown(dashboard.get("/sources/tidewater").text)
    assert "Quiet" in page
    assert "carries no Posting" not in page


def test_an_empty_listing_is_a_result_and_is_not_rendered_in_a_browser_to_make_sure(
    dashboard: TestClient, network: Pages, model: ScriptedModel
) -> None:
    registered_careers_page(dashboard, network, model)
    network.serve(CAREERS, careers_page())
    network.show(CAREERS, careers_page())

    dashboard.post("/sources/tidewater/poll")

    assert network.rendered == []
    assert "Quiet" in shown(dashboard.get("/sources/tidewater").text)
