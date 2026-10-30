"""The Timeline on the dashboard: every Application sent, on its Sent date, under a sentence that
says how many roles are still to do and how the sent ones stand. Every Application here is
invented; the clock stands at 2026-09-18."""

import re

from starlette.testclient import TestClient

from conftest import WriteApplication


def record(company: str, status: str, *history: tuple[str, str]) -> str:
    lines = [f"company: {company}", "role: Engineer", "date: 2026-06-01", f"status: {status}"]
    if history:
        lines.append("history:")
        lines += [f"- status: {was}\n  date: {on}" for was, on in history]
    return "\n".join(lines) + "\n"


def write_the_desk(write_application: WriteApplication) -> None:
    write_application("werft-nord/2026-06-01-engineer", record(
        "Werft Nord", "interview",
        ("todo", "2026-06-01"), ("waiting", "2026-08-20"), ("interview", "2026-09-02"),
    ))  # fmt: skip
    write_application("tuhh/2026-06-01-engineer", record(
        "TUHH", "waiting", ("todo", "2026-06-01"), ("waiting", "2026-07-03"),
    ))  # fmt: skip
    write_application("hafenamt/2026-06-01-engineer", record(
        "Hafenamt", "waiting", ("todo", "2026-06-01"), ("waiting", "2026-08-10"),
    ))  # fmt: skip
    write_application("acme/2026-06-01-engineer", record(
        "ACME", "closed",
        ("todo", "2026-06-01"), ("waiting", "2026-07-15"), ("closed", "2026-08-01"),
    ))  # fmt: skip
    # Dropped before it went out: never sent.
    write_application("dropped/2026-06-01-engineer", record(
        "Dropped GmbH", "closed", ("todo", "2026-06-01"), ("closed", "2026-06-05"),
    ))  # fmt: skip
    # Written by hand as waiting, with no history: sent, on a day nobody recorded.
    write_application("by-hand/2026-06-01-engineer", record("By Hand AG", "waiting"))
    write_application("next/2026-06-01-engineer", record("Next KG", "todo"))
    write_application("later/2026-06-01-engineer", record("Later KG", "todo"))


def timeline_of(page: str) -> str:
    match = re.search(r'<figure class="timeline">.*?</figure>', page, re.S)
    assert match, "no Timeline on the page"
    return match.group(0)


def plain(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def test_the_headline_says_how_many_roles_still_need_an_application(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    write_the_desk(write_application)

    lede = re.search(r'<h1 class="lede">(.*?)</h1>', dashboard.get("/").text, re.S)

    assert lede and plain(lede.group(1)) == "Two roles still need an application."


def test_one_role_left_is_said_in_the_singular(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    write_application("next/2026-06-01-engineer", record("Next KG", "todo"))

    assert "One role still needs an application." in dashboard.get("/").text


def test_the_summary_says_how_the_sent_ones_stand(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    write_the_desk(write_application)

    summary = re.search(r'<p class="summary">(.*?)</p>', dashboard.get("/").text, re.S)

    assert summary and plain(summary.group(1)) == (
        "Four applications sent since July, and one more without a date. "
        "Three are still waiting for a reply, and Werft Nord reached interview. "
        "The other one is closed."
    )


def test_every_application_sent_on_a_known_day_is_a_dot_that_opens_it(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    write_the_desk(write_application)

    timeline = timeline_of(dashboard.get("/").text)

    dots = re.findall(r'<a class="dot dot-(\w+)[^"]*" href="[^"]*/applications/([^"]+)"', timeline)
    assert sorted(dots) == [
        ("closed", "acme/2026-06-01-engineer"),
        ("interview", "werft-nord/2026-06-01-engineer"),
        ("waiting", "hafenamt/2026-06-01-engineer"),
        ("waiting", "tuhh/2026-06-01-engineer"),
    ]
    # Only an interview is named beside its dot.
    assert "Werft Nord" in plain(timeline)
    assert "TUHH" not in re.sub(r'aria-label="[^"]*"|title="[^"]*"', "", timeline)


def test_the_axis_runs_from_the_month_of_the_first_one_sent_to_today(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    write_the_desk(write_application)

    timeline = timeline_of(dashboard.get("/").text)

    assert re.findall(r'<span class="month"[^>]*>(\w+)</span>', timeline) == ["Jul", "Aug", "Sep"]


def test_two_sent_on_the_same_day_stand_on_top_of_each_other(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    for company in ("Alpha", "Beta"):
        write_application(f"{company.lower()}/2026-06-01-engineer", record(
            company, "waiting", ("todo", "2026-06-01"), ("waiting", "2026-09-01"),
        ))  # fmt: skip

    timeline = timeline_of(dashboard.get("/").text)

    places = re.findall(r'style="--at: ([\d.]+)%; --row: (\d+)"', timeline)
    assert len(places) == 2
    assert places[0][0] == places[1][0]
    assert sorted(row for _, row in places) == ["0", "1"]


def test_with_nothing_sent_there_is_no_timeline(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    write_application("next/2026-06-01-engineer", record("Next KG", "todo"))

    page = dashboard.get("/").text

    assert "Nothing has been sent yet." in page
    assert '<figure class="timeline">' not in page


def test_with_every_role_applied_for_the_headline_says_so(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    write_application("tuhh/2026-06-01-engineer", record(
        "TUHH", "waiting", ("todo", "2026-06-01"), ("waiting", "2026-07-03"),
    ))  # fmt: skip

    page = dashboard.get("/").text

    assert "No role still needs an application." in page
    assert "One application sent since July. It is still waiting for a reply." in plain(page)
