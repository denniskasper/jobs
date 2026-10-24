"""Deriving a Source's config again, shown as a difference that a human accepts or rejects, through
the HTTP surface.

Every page here is invented, as is every host.
"""

import html
import json
import re
from pathlib import Path

from starlette.testclient import TestClient

from conftest import Pages, RememberedCommits, ScriptedModel, register
from test_sources import (
    FOLLOW_TO_LISTING,
    FOLLOW_TO_POSTINGS,
    INSTITUTE,
    LISTING,
    posting_page,
    read_source_file,
    serve_institute,
)

MOVED = "https://portal.example/careers/{}"
FOLLOW_TO_MOVED = json.dumps({"follow": r"/careers/\d+"})


def register_institute(dashboard: TestClient, network: Pages, model: ScriptedModel) -> Path:
    serve_institute(network)
    model.answers = [FOLLOW_TO_LISTING, FOLLOW_TO_POSTINGS]
    register(dashboard, INSTITUTE, label="Hafenstadt")
    return Path("sources") / "hafenstadt.yaml"


def redesign_the_portal(network: Pages) -> None:
    """The listing now links to its posting pages under another path."""
    network.serve(
        LISTING,
        "<html><body><a href='/careers/101'>Doctoral researcher</a>"
        "<a href='/careers/102'>Laboratory engineer</a></body></html>",
    )
    network.serve(MOVED.format(101), posting_page("101", "Doctoral researcher", "F-26-101-M8"))
    network.serve(MOVED.format(102), posting_page("102", "Laboratory engineer", "F-26-102-K3"))


def fields_of(page: str) -> dict[str, str]:
    """What the hidden fields of the page's forms would send, with the line breaks a browser
    sends them with."""
    found = re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', page)
    return {name: html.unescape(value).replace("\n", "\r\n") for name, value in found}


def test_deriving_again_shows_the_new_config_as_a_difference_and_writes_nothing(
    dashboard: TestClient,
    data_dir: Path,
    network: Pages,
    model: ScriptedModel,
    committer: RememberedCommits,
) -> None:
    file = data_dir / register_institute(dashboard, network, model)
    written = file.read_text(encoding="utf-8")
    commits = len(committer.commits)
    redesign_the_portal(network)
    model.answers = [FOLLOW_TO_LISTING, FOLLOW_TO_MOVED]

    derived = dashboard.post("/sources/hafenstadt/rederive")

    assert derived.status_code == 200
    lines = [html.unescape(line).strip() for line in derived.text.splitlines()]
    assert any(re.match(r"<del[^>]*>-", line) and r"/jobs/posting/\d+" in line for line in lines)
    assert any(re.match(r"<ins[^>]*>\+", line) and r"/careers/\d+" in line for line in lines)
    assert file.read_text(encoding="utf-8") == written
    assert len(committer.commits) == commits


def test_an_accepted_config_takes_effect_and_is_committed(
    dashboard: TestClient,
    data_dir: Path,
    network: Pages,
    model: ScriptedModel,
    committer: RememberedCommits,
) -> None:
    file = data_dir / register_institute(dashboard, network, model)
    redesign_the_portal(network)
    model.answers = [FOLLOW_TO_LISTING, FOLLOW_TO_MOVED]
    derived = dashboard.post("/sources/hafenstadt/rederive")

    accepted = dashboard.post("/sources/hafenstadt/rederive/accept", data=fields_of(derived.text))

    assert accepted.status_code == 200
    config = read_source_file(data_dir, "hafenstadt")["config"]
    assert [hop["follow"] for hop in config["hops"]] == [
        r"portal\.example/jobs/list",
        r"/careers/\d+",
    ]
    assert committer.commits[-1].paths == [file]
    dashboard.post("/sources/hafenstadt/poll")
    assert "2 Postings" in " ".join(dashboard.get("/sources/hafenstadt").text.split())


def test_a_config_that_is_not_accepted_leaves_the_source_untouched(
    dashboard: TestClient,
    data_dir: Path,
    network: Pages,
    model: ScriptedModel,
    committer: RememberedCommits,
) -> None:
    file = data_dir / register_institute(dashboard, network, model)
    written = file.read_text(encoding="utf-8")
    commits = len(committer.commits)
    redesign_the_portal(network)
    model.answers = [FOLLOW_TO_LISTING, FOLLOW_TO_MOVED]
    derived = dashboard.post("/sources/hafenstadt/rederive")

    [rejecting] = re.findall(r'href="([^"]+)">Reject<', derived.text)
    rejected = dashboard.get(rejecting)

    assert rejected.status_code == 200
    assert "<ins" not in rejected.text
    assert file.read_text(encoding="utf-8") == written
    assert len(committer.commits) == commits


def test_a_config_is_never_derived_again_without_being_asked_for(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    file = data_dir / register_institute(dashboard, network, model)
    written = file.read_text(encoding="utf-8")
    asked = len(model.asked)
    redesign_the_portal(network)

    for _ in range(4):
        dashboard.post("/poll")

    assert "Unhealthy" in dashboard.get("/sources").text
    assert len(model.asked) == asked
    assert file.read_text(encoding="utf-8") == written


def test_what_a_human_put_in_the_config_survives_deriving_it_again(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_institute(network)
    model.answers = [
        FOLLOW_TO_LISTING,
        FOLLOW_TO_POSTINGS,
        json.dumps({"field": "description", "pattern": r"M8\b"}),
    ]
    register(dashboard, INSTITUTE, label="Hafenstadt", rule="only reference numbers in M8")
    file = data_dir / "sources" / "hafenstadt.yaml"
    file.write_text(
        file.read_text(encoding="utf-8").replace("interval: 1.0", "interval: 5.0"), "utf-8"
    )
    redesign_the_portal(network)
    model.answers = [FOLLOW_TO_LISTING, FOLLOW_TO_MOVED]

    derived = dashboard.post("/sources/hafenstadt/rederive")
    dashboard.post("/sources/hafenstadt/rederive/accept", data=fields_of(derived.text))

    record = read_source_file(data_dir, "hafenstadt")
    assert record["label"] == "Hafenstadt"
    assert record["config"]["interval"] == 5.0
    assert record["config"]["filters"] == [
        {"field": "description", "pattern": r"M8\b", "rule": "only reference numbers in M8"}
    ]


def test_a_source_that_cannot_be_derived_again_is_left_as_it_is(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    file = data_dir / register_institute(dashboard, network, model)
    written = file.read_text(encoding="utf-8")
    network.serve(INSTITUTE, 404)

    refused = dashboard.post("/sources/hafenstadt/rederive")

    assert refused.status_code == 422
    assert "the Source was left as it is" in refused.text
    assert "registered" not in refused.text
    assert file.read_text(encoding="utf-8") == written


def test_a_config_derived_against_a_file_that_has_changed_since_is_not_accepted(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    file = data_dir / register_institute(dashboard, network, model)
    redesign_the_portal(network)
    model.answers = [FOLLOW_TO_LISTING, FOLLOW_TO_MOVED]
    derived = dashboard.post("/sources/hafenstadt/rederive")
    edited = file.read_text(encoding="utf-8").replace("interval: 1.0", "interval: 5.0")
    file.write_text(edited, encoding="utf-8")

    refused = dashboard.post("/sources/hafenstadt/rederive/accept", data=fields_of(derived.text))

    assert refused.status_code == 422
    assert "has changed since" in refused.text
    assert file.read_text(encoding="utf-8") == edited


def test_a_source_that_cannot_be_read_is_not_derived_again(
    dashboard: TestClient, data_dir: Path, model: ScriptedModel
) -> None:
    file = data_dir / "sources" / "broken.yaml"
    file.parent.mkdir(parents=True)
    file.write_text("url: https://portal.example/jobs\n", encoding="utf-8")

    refused = dashboard.post("/sources/broken/rederive")

    assert refused.status_code == 422
    assert "fix it by hand first" in refused.text
    assert model.asked == []


def test_a_source_whose_config_stopped_short_can_be_polled_once_derived_to_the_end(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    network.serve(LISTING, "<html><body><div id='app'></div></body></html>")
    model.answers = [json.dumps({"browser": True})]
    register(dashboard, LISTING, label="Portal")
    serve_institute(network)
    model.answers = [FOLLOW_TO_POSTINGS]

    derived = dashboard.post("/sources/portal/rederive")
    dashboard.post("/sources/portal/rederive/accept", data=fields_of(derived.text))

    assert "unfinished" not in read_source_file(data_dir, "portal")["config"]
    assert "Cannot be polled" not in dashboard.get("/sources/portal").text
