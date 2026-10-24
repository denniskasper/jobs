"""Creating an Application by hand and changing its Status, through the HTTP surface."""

import re
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from starlette.testclient import TestClient

from conftest import SettableClock, WriteApplication


def read_record(data_dir: Path, folder: str) -> dict[str, Any]:
    text = (data_dir / "applications" / folder / "application.yaml").read_text(encoding="utf-8")
    record = yaml.safe_load(text)
    assert isinstance(record, dict)
    return record


def test_creating_an_application_scaffolds_its_folder_and_lists_it(
    dashboard: TestClient, data_dir: Path
) -> None:
    created = dashboard.post(
        "/applications", data={"company": "Nordwind Robotics", "role": "Simulation Engineer"}
    )

    assert created.status_code == 200
    assert "Nordwind Robotics" in created.text
    assert "Simulation Engineer" in created.text
    assert "To do" in created.text
    record = read_record(data_dir, "nordwind-robotics/2026-09-18-simulation-engineer")
    assert record["company"] == "Nordwind Robotics"
    assert record["role"] == "Simulation Engineer"
    assert str(record["date"]) == "2026-09-18"
    assert record["status"] == "todo"


def test_german_characters_are_transliterated_in_the_folder_name(
    dashboard: TestClient, data_dir: Path
) -> None:
    dashboard.post(
        "/applications",
        data={"company": "Müller & Söhne Gerätebau", "role": "Prüfingenieur Größenmessung (m/w/d)"},
    )

    folder = "mueller-soehne-geraetebau/2026-09-18-pruefingenieur-groessenmessung-m-w-d"
    record = read_record(data_dir, folder)
    assert record["company"] == "Müller & Söhne Gerätebau"
    assert record["role"] == "Prüfingenieur Größenmessung (m/w/d)"


def test_other_accented_characters_lose_their_accent_in_the_folder_name(
    dashboard: TestClient, data_dir: Path
) -> None:
    dashboard.post("/applications", data={"company": "Société Générale", "role": "Ingénieur"})

    assert (data_dir / "applications" / "societe-generale" / "2026-09-18-ingenieur").is_dir()


def test_creating_an_application_whose_folder_exists_is_refused(
    dashboard: TestClient, data_dir: Path
) -> None:
    folder = "nordwind-robotics/2026-09-18-simulation-engineer"
    note = data_dir / "applications" / folder / "notes.md"
    note.parent.mkdir(parents=True)
    note.write_text("Called them on Monday.\n", encoding="utf-8")

    refused = dashboard.post(
        "/applications", data={"company": "Nordwind Robotics", "role": "Simulation Engineer"}
    )

    assert refused.status_code == 409
    assert f"{folder} already exists" in refused.text
    assert sorted(path.name for path in note.parent.iterdir()) == ["notes.md"]
    assert note.read_text(encoding="utf-8") == "Called them on Monday.\n"


@pytest.mark.parametrize(
    ("company", "role", "complaint"),
    [
        ("", "Simulation Engineer", "company"),
        ("Nordwind Robotics", "   ", "role"),
        ("???", "Simulation Engineer", "company"),
    ],
)
def test_an_application_that_cannot_be_named_is_refused(
    dashboard: TestClient, data_dir: Path, company: str, role: str, complaint: str
) -> None:
    refused = dashboard.post("/applications", data={"company": company, "role": role})

    assert refused.status_code == 422
    assert complaint in refused.text.split("Not done.")[1].split("</p>")[0]
    assert not list((data_dir / "applications").glob("*/*"))


def test_every_status_change_is_recorded_with_its_date(
    dashboard: TestClient, data_dir: Path, clock: SettableClock
) -> None:
    folder = "nordwind-robotics/2026-09-18-simulation-engineer"
    dashboard.post(
        "/applications", data={"company": "Nordwind Robotics", "role": "Simulation Engineer"}
    )

    clock.now += timedelta(days=3)
    waiting = dashboard.post(f"/applications/{folder}/status", data={"status": "waiting"})
    clock.now += timedelta(days=11)
    interview = dashboard.post(f"/applications/{folder}/status", data={"status": "interview"})

    assert waiting.status_code == 200
    assert "Waiting" in waiting.text
    assert "Interview" in interview.text
    assert "Waiting" not in interview.text
    record = read_record(data_dir, folder)
    assert record["status"] == "interview"
    assert record["date"] == date(2026, 9, 18)
    assert record["history"] == [
        {"status": "todo", "date": date(2026, 9, 18)},
        {"status": "waiting", "date": date(2026, 9, 21)},
        {"status": "interview", "date": date(2026, 10, 2)},
    ]


@pytest.mark.parametrize(
    ("status", "label"),
    [("todo", "To do"), ("waiting", "Waiting"), ("interview", "Interview"), ("closed", "Closed")],
)
def test_a_hand_written_application_can_be_moved_to_any_status(
    dashboard: TestClient,
    data_dir: Path,
    write_application: WriteApplication,
    status: str,
    label: str,
) -> None:
    folder = "tuhh/2026-09-01-research-engineer"
    before = "closed" if status == "todo" else "todo"
    write_application(
        folder,
        f"company: TUHH\nrole: Research Engineer\ndate: 2026-09-01\nstatus: {before}\n"
        "salary: 62000\n",
    )

    page = dashboard.post(f"/applications/{folder}/status", data={"status": status})

    assert page.status_code == 200
    assert label in page.text
    record = read_record(data_dir, folder)
    assert record["status"] == status
    assert record["history"][-1] == {"status": status, "date": date(2026, 9, 18)}
    assert record["salary"] == 62000
    assert record["company"] == "TUHH"


def test_an_unknown_status_is_refused_and_the_record_left_alone(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    folder = "tuhh/2026-09-01-research-engineer"
    written = "company: TUHH\nrole: Research Engineer\ndate: 2026-09-01\nstatus: waiting\n"
    path = write_application(folder, written)

    refused = dashboard.post(f"/applications/{folder}/status", data={"status": "sent"})

    assert refused.status_code == 422
    assert "todo, waiting, interview, closed" in refused.text
    assert path.read_text(encoding="utf-8") == written


def test_changing_the_status_of_a_record_that_cannot_be_read_is_refused(
    dashboard: TestClient, write_application: WriteApplication
) -> None:
    folder = "acme/2026-09-02-engineer"
    path = write_application(folder, "company: [unclosed\n")

    refused = dashboard.post(f"/applications/{folder}/status", data={"status": "closed"})

    assert refused.status_code == 422
    assert "not valid YAML" in refused.text.split("Not done.")[1].split("</p>")[0]
    assert path.read_text(encoding="utf-8") == "company: [unclosed\n"


@pytest.mark.parametrize(
    "folder",
    [
        "tuhh/2026-09-01-gone",
        "tuhh",
        "tuhh/2026-09-01-research-engineer/deeper",
        "../outside/x",
        "..%2Foutside/x",
        "tuhh/..%2F..%2Foutside%2Fx",
    ],
)
def test_changing_the_status_of_something_that_is_not_an_application_is_not_found(
    dashboard: TestClient, tmp_path: Path, write_application: WriteApplication, folder: str
) -> None:
    write_application(
        "tuhh/2026-09-01-research-engineer",
        "company: TUHH\nrole: Research Engineer\ndate: 2026-09-01\nstatus: waiting\n",
    )
    outside = tmp_path / "data" / "outside" / "x" / "application.yaml"
    outside.parent.mkdir(parents=True)
    outside.write_text("status: waiting\n", encoding="utf-8")

    missing = dashboard.post(f"/applications/{folder}/status", data={"status": "closed"})

    assert missing.status_code == 404
    assert outside.read_text(encoding="utf-8") == "status: waiting\n"


def test_deleting_the_folder_by_hand_removes_the_application(
    dashboard: TestClient, data_dir: Path
) -> None:
    dashboard.post(
        "/applications", data={"company": "Nordwind Robotics", "role": "Simulation Engineer"}
    )
    assert "Nordwind Robotics" in dashboard.get("/").text

    shutil.rmtree(data_dir / "applications" / "nordwind-robotics")

    page = dashboard.get("/")
    assert page.status_code == 200
    assert "Nordwind Robotics" not in page.text
    assert "No Applications yet" in page.text


def test_the_dashboard_offers_the_forms_that_create_and_change(
    dashboard: TestClient, data_dir: Path
) -> None:
    create = re.search(r'<form[^>]*action="([^"]+)"', dashboard.get("/").text)
    assert create is not None
    listed = dashboard.post(create[1], data={"company": "Nordwind Robotics", "role": "Engineer"})

    change = re.search(r'<form[^>]*action="([^"]+/status)"', listed.text)
    assert change is not None
    dashboard.post(change[1], data={"status": "closed"})

    record = read_record(data_dir, "nordwind-robotics/2026-09-18-engineer")
    assert record["status"] == "closed"


def test_a_new_record_is_plain_yaml_a_human_can_edit(dashboard: TestClient, data_dir: Path) -> None:
    dashboard.post("/applications", data={"company": "Nordwind Robotics", "role": "Engineer"})

    folder = data_dir / "applications" / "nordwind-robotics" / "2026-09-18-engineer"
    written = (folder / "application.yaml").read_text(encoding="utf-8")
    assert "&" not in written
    assert "*" not in written
    assert [path.name for path in folder.iterdir()] == ["application.yaml"]


def test_a_name_too_long_for_a_folder_is_refused_and_leaves_nothing_behind(
    dashboard: TestClient, data_dir: Path
) -> None:
    refused = dashboard.post(
        "/applications", data={"company": "Nordwind Robotics", "role": "Engineer " * 40}
    )

    assert refused.status_code == 422
    assert "could not be created" in refused.text
    assert not list((data_dir / "applications").glob("*/*"))


def test_umlauts_typed_as_a_letter_plus_combining_dots_are_transliterated_too(
    dashboard: TestClient, data_dir: Path
) -> None:
    dashboard.post("/applications", data={"company": "Mu\u0308ller", "role": "Pru\u0308fer"})

    assert (data_dir / "applications" / "mueller" / "2026-09-18-pruefer").is_dir()


def test_the_first_change_to_a_hand_written_record_keeps_the_status_it_had(
    dashboard: TestClient, data_dir: Path, write_application: WriteApplication
) -> None:
    folder = "tuhh/2026-09-01-research-engineer"
    write_application(
        folder, "company: TUHH\nrole: Research Engineer\ndate: 2026-09-01\nstatus: waiting\n"
    )

    dashboard.post(f"/applications/{folder}/status", data={"status": "interview"})

    assert read_record(data_dir, folder)["history"] == [
        {"status": "waiting"},
        {"status": "interview", "date": date(2026, 9, 18)},
    ]


def test_setting_the_status_an_application_already_has_adds_no_history(
    dashboard: TestClient, data_dir: Path
) -> None:
    folder = "nordwind-robotics/2026-09-18-engineer"
    dashboard.post("/applications", data={"company": "Nordwind Robotics", "role": "Engineer"})

    page = dashboard.post(f"/applications/{folder}/status", data={"status": "todo"})

    assert page.status_code == 200
    assert read_record(data_dir, folder)["history"] == [
        {"status": "todo", "date": date(2026, 9, 18)}
    ]


@pytest.mark.parametrize(
    ("written", "complaint"),
    [
        ("company: TUHH\ndate: 2026-09-01\nstatus: waiting\n", "role"),
        (
            "company: TUHH\nrole: Engineer\ndate: 2026-09-01\nstatus: waiting\n"
            "history: sent on 2026-09-02\n",
            "history",
        ),
    ],
)
def test_a_record_that_needs_fixing_by_hand_is_not_rewritten(
    dashboard: TestClient, write_application: WriteApplication, written: str, complaint: str
) -> None:
    folder = "tuhh/2026-09-01-engineer"
    path = write_application(folder, written)

    refused = dashboard.post(f"/applications/{folder}/status", data={"status": "closed"})

    assert refused.status_code == 422
    assert complaint in refused.text.split("Not done.")[1].split("</p>")[0]
    assert path.read_text(encoding="utf-8") == written
