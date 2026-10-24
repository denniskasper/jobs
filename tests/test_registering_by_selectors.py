"""Registering a URL whose posting pages publish no structured record, through the HTTP surface.

Every page here is invented, as is every host.
"""

import html
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from starlette.testclient import TestClient

from conftest import Pages, RememberedCommits, ScriptedModel, register, try_again

CAREERS = "https://employer.example/careers/"
OPENING = "https://employer.example/careers/opening/{}"


def opening_page(role: str, reference: str, place: str = "Hafenstadt") -> str:
    return f"""<html><body>
<header><h1 class="site">Careers at Example</h1></header>
<main>
  <h1 class="opening">{role}</h1>
  <p class="meta">
    <span class="place">{place}</span>
    <span class="reference">{reference}</span>
    <time class="published" datetime="2026-09-01">1 September</time>
    <span class="closes">Apply by 31.10.2026</span>
  </p>
  <section class="text"><h2>Your tasks</h2><p>You run the wave flume.</p></section>
  <section class="text"><h2>Your profile</h2><p>A degree in engineering.</p></section>
  <script>track("{reference}")</script>
</main>
</body></html>"""


def listing_of(*openings: int) -> str:
    links = "".join(f"<a href='opening/{number}'>Opening {number}</a>" for number in openings)
    return f"<html><body><h1>Work with us</h1>{links}</body></html>"


def serve_two_openings(network: Pages) -> None:
    network.serve(CAREERS, listing_of(1, 2))
    network.serve(OPENING.format(1), opening_page("Doctoral researcher", "EX-1"))
    network.serve(OPENING.format(2), opening_page("Laboratory engineer", "EX-2", place="Kiel"))


FOLLOW_TO_OPENINGS = json.dumps({"follow": r"/careers/opening/\d+"})
POSITIONS = json.dumps({"positions": True})
SELECTORS = {
    "role": "h1.opening",
    "description": "section.text",
    "location": ".meta .place",
    "identifier": ".reference",
    "posted": "time.published@datetime",
    "deadline": ".closes",
}


def proposed(selectors: dict[str, Any], company: str | None = "Example GmbH") -> str:
    return json.dumps({"selectors": selectors, **({"company": company} if company else {})})


def read_source_file(data_dir: Path, name: str) -> dict[str, Any]:
    record = yaml.safe_load((data_dir / "sources" / f"{name}.yaml").read_text(encoding="utf-8"))
    assert isinstance(record, dict)
    return record


def kept(data_dir: Path, source: str) -> list[dict[str, Any]]:
    files = sorted((data_dir / "postings" / source).glob("*.yaml"))
    return [yaml.safe_load(file.read_text(encoding="utf-8")) for file in files]


def test_posting_pages_without_a_structured_record_register_with_selectors(
    dashboard: TestClient,
    data_dir: Path,
    network: Pages,
    model: ScriptedModel,
    committer: RememberedCommits,
) -> None:
    serve_two_openings(network)
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS, proposed(SELECTORS)]

    registered = register(dashboard, CAREERS, label="Example")

    config = read_source_file(data_dir, "example")["config"]
    assert config["strategy"] == "selectors"
    assert config["selectors"] == SELECTORS
    assert config["company"] == "Example GmbH"
    assert config["hops"] == [{"follow": r"/careers/opening/\d+", "render": "static"}]
    assert config["render"] == "static"
    assert "unfinished" not in config
    assert "Cannot be polled" not in registered
    assert "h1.opening" in registered
    assert [commit.message for commit in committer.commits] == ["Register Source example"]


def test_the_model_is_shown_the_first_page_reduced_and_fenced_off_as_web_content(
    dashboard: TestClient, network: Pages, model: ScriptedModel
) -> None:
    serve_two_openings(network)
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS, proposed(SELECTORS)]

    register(dashboard, CAREERS, label="Example")

    hop_question, selectors_question = model.asked[1], model.asked[2]
    assert OPENING.format(1) in hop_question
    assert '"positions"' in hop_question
    fence = "content taken from the web"
    assert fence in selectors_question
    below = selectors_question.partition(fence)[2]
    assert '<h1 class="opening">Doctoral researcher</h1>' in below
    assert "track(" not in selectors_question
    assert "Laboratory engineer" not in selectors_question


def test_a_source_registered_with_selectors_can_be_polled_straight_away(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_two_openings(network)
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS, proposed(SELECTORS)]
    register(dashboard, CAREERS, label="Example")

    polled = dashboard.post("/sources/example/poll")

    assert polled.status_code == 200
    researcher, engineer = kept(data_dir, "example")
    assert (researcher["role"], engineer["role"]) == ("Doctoral researcher", "Laboratory engineer")
    assert researcher["company"] == "Example GmbH"
    assert researcher["deadline"].isoformat() == "2026-10-31"
    assert len(model.asked) == 3


def test_the_company_may_be_read_by_a_selector_and_no_words_given(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_two_openings(network)
    selectors = {"role": "h1.opening", "description": "section.text", "company": "h1.site"}
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS, proposed(selectors, company=None)]

    register(dashboard, CAREERS, label="Example")

    config = read_source_file(data_dir, "example")["config"]
    assert config["selectors"] == selectors
    assert "company" not in config


def test_a_field_answered_with_nothing_is_left_out(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_two_openings(network)
    selectors = {"role": "h1.opening", "description": "section.text", "identifier": None}
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS, proposed(selectors)]

    register(dashboard, CAREERS, label="Example")

    assert read_source_file(data_dir, "example")["config"]["selectors"] == {
        "role": "h1.opening",
        "description": "section.text",
    }


CHAIN = "https://group.example/{}"


def serve_a_chain_of_three(network: Pages, last: str) -> None:
    """Three signposts in a row, and whatever a test puts three hops on."""
    network.serve(CHAIN.format("one"), "<html><body><a href='/two'>Careers</a></body></html>")
    network.serve(CHAIN.format("two"), "<html><body><a href='/three'>Germany</a></body></html>")
    network.serve(CHAIN.format("three"), "<html><body><a href='/four'>Research</a></body></html>")
    network.serve(CHAIN.format("four"), last)


FOLLOWS = [json.dumps({"follow": f"/{step}$"}) for step in ("two", "three", "four")]


def test_a_page_three_hops_on_that_describes_a_position_registers(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_a_chain_of_three(network, opening_page("Doctoral researcher", "EX-1"))
    model.answers = [*FOLLOWS, POSITIONS, proposed(SELECTORS)]

    register(dashboard, CHAIN.format("one"), label="Group")

    config = read_source_file(data_dir, "group")["config"]
    assert [hop["follow"] for hop in config["hops"]] == ["/two$", "/three$", "/four$"]
    assert config["strategy"] == "selectors"
    assert '"follow"' not in model.asked[3]
    assert '"positions"' in model.asked[3]


def test_a_page_three_hops_on_that_only_links_onward_is_refused(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_a_chain_of_three(network, "<html><body><a href='/five'>Openings</a></body></html>")
    model.answers = [*FOLLOWS, json.dumps({"follow": "/five$"})]

    refused = register(dashboard, CHAIN.format("one"), label="Group")

    assert "3 hops on, https://group.example/four still carries none" in refused
    assert "https://group.example/five" not in network.fetched
    assert not (data_dir / "sources").exists()


SHELL = "<html><body><div id='app'></div><script src='/bundle.js'></script></body></html>"


def test_posting_pages_that_need_a_browser_register_as_they_stood_when_the_wait_ran_out(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    network.serve(CAREERS, listing_of(1, 2))
    network.serve(OPENING.format(1), SHELL)
    network.serve(OPENING.format(2), SHELL)
    network.show(OPENING.format(1), opening_page("Doctoral researcher", "EX-1"))
    network.show(OPENING.format(2), opening_page("Laboratory engineer", "EX-2"))
    model.answers = [
        FOLLOW_TO_OPENINGS,
        json.dumps({"browser": True}),
        POSITIONS,
        proposed(SELECTORS),
    ]

    register(dashboard, CAREERS, label="Example")
    dashboard.post("/sources/example/poll")

    config = read_source_file(data_dir, "example")["config"]
    assert config["render"] == "browser"
    assert config["strategy"] == "selectors"
    assert [posting["role"] for posting in kept(data_dir, "example")] == [
        "Doctoral researcher",
        "Laboratory engineer",
    ]


def test_a_page_with_a_structured_record_never_leads_to_the_selectors_question(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    record = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Laboratory engineer",
        "description": "<p>You keep the flume running.</p>",
        "hiringOrganization": {"name": "Example GmbH"},
    }
    network.serve(CAREERS, listing_of(1, 2))
    network.serve(OPENING.format(1), opening_page("Doctoral researcher", "EX-1"))
    network.serve(
        OPENING.format(2),
        opening_page("Laboratory engineer", "EX-2").replace(
            "<main>", f"<script type='application/ld+json'>{json.dumps(record)}</script><main>"
        ),
    )
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS, proposed(SELECTORS)]

    register(dashboard, CAREERS, label="Example")

    config = read_source_file(data_dir, "example")["config"]
    assert config["strategy"] == "structured-record"
    assert "selectors" not in config
    assert len(model.asked) == 1


def test_selectors_that_fail_on_the_second_sampled_page_refuse_the_registration(
    dashboard: TestClient,
    data_dir: Path,
    network: Pages,
    model: ScriptedModel,
    committer: RememberedCommits,
) -> None:
    serve_two_openings(network)
    network.serve(
        OPENING.format(2),
        opening_page("Laboratory engineer", "EX-2").replace('class="opening"', 'class="title"'),
    )
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS] + [proposed(SELECTORS)] * 2

    refused = html.unescape(register(dashboard, CAREERS, label="Example"))

    assert OPENING.format(2) in refused
    assert "the page has no role: the selector 'h1.opening' found none" in refused
    assert "Nothing was registered" in refused
    assert not (data_dir / "sources").exists()
    assert committer.commits == []


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        ('"h1.opening"', "not a JSON object"),
        (json.dumps({"selectors": "h1.opening"}), "selectors is not a mapping"),
        (proposed({"description": "section.text"}), "selectors has no role"),
        (proposed({"role": "h1.opening"}), "selectors has no description"),
        (
            proposed({"role": "h1..opening", "description": "section.text"}),
            "'h1..opening' is not a CSS selector",
        ),
        (proposed({"role": "h1.opening", "description": "section.text"}, None), "no company"),
    ],
)
def test_an_answer_that_cannot_be_used_as_selectors_refuses_with_the_reason(
    dashboard: TestClient,
    data_dir: Path,
    network: Pages,
    model: ScriptedModel,
    answer: str,
    reason: str,
) -> None:
    serve_two_openings(network)
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS, answer, answer]

    refused = html.unescape(register(dashboard, CAREERS, label="Example"))

    assert reason in refused
    assert "Nothing was registered" in refused
    assert not (data_dir / "sources").exists()


def test_a_filter_rule_is_tried_on_a_posting_the_selectors_read(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_two_openings(network)
    model.answers = [
        FOLLOW_TO_OPENINGS,
        POSITIONS,
        proposed(SELECTORS),
        json.dumps({"field": "location", "pattern": "Hafenstadt"}),
    ]
    words = "only positions in Hafenstadt"

    register(dashboard, CAREERS, label="Example", rule=words)
    dashboard.post("/sources/example/poll")

    assert read_source_file(data_dir, "example")["config"]["filters"] == [
        {"field": "location", "pattern": "Hafenstadt", "rule": words}
    ]
    assert "EX-1" in model.asked[3]
    proposals = dashboard.get("/proposals").text
    assert "Doctoral researcher" in proposals
    assert "Laboratory engineer" not in proposals


def test_the_provenance_of_a_selectors_source_names_the_model(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    network.serve(OPENING.format(1), opening_page("Doctoral researcher", "EX-1"))
    model.answers = [POSITIONS, proposed(SELECTORS)]

    registered = register(dashboard, OPENING.format(1), label="Example")

    provenance = read_source_file(data_dir, "example")["config"]["provenance"]
    assert provenance["model"] == "scripted-model"
    assert "scripted-model" in registered


def test_a_model_that_cannot_be_asked_for_selectors_refuses_as_it_does_for_a_hop(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_two_openings(network)
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS]

    refused = register(dashboard, CAREERS, label="Example")

    assert "could not be asked" in refused
    assert "nothing was registered" in refused
    assert not (data_dir / "sources").exists()


def test_a_registration_refused_over_selectors_is_kept_until_cleared_and_tried_again_as_entered(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_two_openings(network)
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS] + [proposed({"role": "h1.opening"})] * 2

    refused = register(dashboard, CAREERS, label="Example", rule="only positions in Kiel")

    for path in ["/", "/sources", "/proposals", "/sources"]:
        assert dashboard.get(path).status_code == 200
    assert "selectors has no description" in dashboard.get("/sources").text
    form = try_again(dashboard, refused)
    assert f'value="{CAREERS}"' in form
    assert 'value="Example"' in form
    assert 'value="only positions in Kiel"' in form
    clear = re.search(r'<form method="post" action="([^"]+/registrations/[^"]+)"', refused)
    assert clear
    dashboard.post(clear[1])
    assert "selectors has no description" not in dashboard.get("/sources").text
    assert not (data_dir / "sources").exists()


# What a selector that found the site's header reads: the same words off every page.
HEADER_AS_ROLE = {**SELECTORS, "role": "h1.site"}


def test_selectors_that_read_the_same_role_off_both_sampled_pages_refuse_and_say_so(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_two_openings(network)
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS] + [proposed(HEADER_AS_ROLE)] * 2

    refused = html.unescape(register(dashboard, CAREERS, label="Example"))

    assert "read the same role, 'Careers at Example', off every page" in refused
    assert OPENING.format(1) in refused
    assert OPENING.format(2) in refused
    assert "Nothing was registered" in refused
    assert not (data_dir / "sources").exists()


def test_with_a_single_posting_read_overall_the_required_fields_alone_decide(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    network.serve(OPENING.format(1), opening_page("Doctoral researcher", "EX-1"))
    model.answers = [POSITIONS, proposed(HEADER_AS_ROLE)]

    register(dashboard, OPENING.format(1), label="Example")

    assert read_source_file(data_dir, "example")["config"]["selectors"] == HEADER_AS_ROLE
    assert len(model.asked) == 2


def test_after_a_failed_answer_the_model_is_asked_once_more_and_told_what_was_wrong(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_two_openings(network)
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS, proposed(HEADER_AS_ROLE), proposed(SELECTORS)]

    registered = register(dashboard, CAREERS, label="Example")

    assert read_source_file(data_dir, "example")["config"]["selectors"] == SELECTORS
    assert "h1.opening" in registered
    first, second = model.asked[2], model.asked[3]
    assert "the same role" not in first
    above, _, below = second.partition("content taken from the web")
    assert "answered this once" in above
    assert "Careers at Example" not in above
    assert "h1.site" in below
    assert "read the same role, 'Careers at Example', off every page" in below
    assert '<h1 class="opening">Doctoral researcher</h1>' in below


def test_a_second_failure_refuses_with_its_own_reason_and_the_model_is_never_asked_a_third_time(
    dashboard: TestClient,
    data_dir: Path,
    network: Pages,
    model: ScriptedModel,
    committer: RememberedCommits,
) -> None:
    serve_two_openings(network)
    network.serve(
        OPENING.format(2),
        opening_page("Laboratory engineer", "EX-2").replace('class="text"', 'class="body"'),
    )
    model.answers = [
        FOLLOW_TO_OPENINGS,
        POSITIONS,
        proposed(HEADER_AS_ROLE),
        proposed(SELECTORS),
        proposed({**SELECTORS, "description": "section"}),
    ]

    refused = html.unescape(register(dashboard, CAREERS, label="Example"))

    assert f"do not read {OPENING.format(2)}" in refused
    assert "the page has no description: the selector 'section.text' found none" in refused
    assert "the same role" not in refused
    assert "Nothing was registered" in refused
    assert not (data_dir / "sources").exists()
    assert committer.commits == []
    assert len(model.asked) == 4


def test_an_answer_that_cannot_be_used_is_a_failed_try_and_a_second_one_may_work(
    dashboard: TestClient, data_dir: Path, network: Pages, model: ScriptedModel
) -> None:
    serve_two_openings(network)
    unusable = proposed({**SELECTORS, "role": "h1..opening"})
    model.answers = [FOLLOW_TO_OPENINGS, POSITIONS, unusable, proposed(SELECTORS)]

    register(dashboard, CAREERS, label="Example")

    assert read_source_file(data_dir, "example")["config"]["selectors"] == SELECTORS
    assert "'h1..opening' is not a CSS selector" in model.asked[3]
