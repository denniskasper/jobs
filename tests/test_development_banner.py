import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from conftest import Pages, RememberedCommits, ScriptedModel, SettableClock
from jobs.web import create_app

PAGES = ("/", "/proposals", "/proposals/left-out", "/documents", "/sources")

BANNER = re.compile(r'<div class="dev-banner"[^>]*>(.*?)</div>', re.S)


@pytest.fixture
def in_development(
    data_dir: Path,
    clock: SettableClock,
    network: Pages,
    committer: RememberedCommits,
    model: ScriptedModel,
) -> TestClient:
    """The service as a dev server runs it."""
    return TestClient(
        create_app(
            data_dir=data_dir,
            clock=clock,
            network=network,
            committer=committer,
            model=model,
            sleep=lambda seconds: None,
            development=True,
        )
    )


@pytest.mark.parametrize("path", PAGES)
def test_a_dev_server_says_so_on_every_page(in_development: TestClient, path: str) -> None:
    banner = BANNER.search(in_development.get(path).text)

    assert banner, "the page has no banner"
    assert "Development" in banner.group(1)


@pytest.mark.parametrize("path", PAGES)
def test_the_deployed_service_shows_no_banner(dashboard: TestClient, path: str) -> None:
    assert not BANNER.search(dashboard.get(path).text)
