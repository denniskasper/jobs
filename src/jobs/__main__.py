"""Run the service. The only place the environment is read; everything below is handed its
boundaries."""

import os
import sys
from datetime import timedelta
from pathlib import Path

import uvicorn

from jobs.clock import system_clock
from jobs.commits import GitCommitter
from jobs.fetching import Chromium, HttpNetwork
from jobs.models import ClaudeCli
from jobs.polling import Schedule
from jobs.printing import ChromiumPrinter
from jobs.registrations import on_a_daemon_thread
from jobs.store import Origin, StoreNotUsable, bring_up_to_date
from jobs.web import create_app

# Deriving a Source config happens once per Source and is then frozen, so it gets a capable model
# rather than a cheap one (decision 31).
DEFAULT_MODEL = "claude-opus-5"

# Postings stay up for weeks, so once a day finds them well inside their Deadline and asks each
# host for a few pages a day.
DEFAULT_POLL_EVERY_HOURS = 24.0


def main() -> None:
    data_dir = Path(os.environ.get("DATA_DIR", "data")).resolve()
    origin = _origin()
    try:
        warning = bring_up_to_date(data_dir, origin)
    except StoreNotUsable as not_usable:
        sys.exit(f"jobs: {not_usable}")
    if warning:
        print(f"jobs: {warning}", file=sys.stderr)
    committer = GitCommitter(data_dir, origin)
    # Whatever an earlier run committed and could not push goes out now, rather than waiting
    # for the next mutation.
    committer.push()
    if not_pushed := committer.not_pushed():
        print(f"jobs: the store was not pushed: {not_pushed}", file=sys.stderr)
    model_name = os.environ.get("MODEL") or DEFAULT_MODEL
    app = create_app(
        data_dir=data_dir,
        clock=system_clock,
        network=HttpNetwork(browser=Chromium()),
        committer=committer,
        model=ClaudeCli(model_name),
        # Prose can run on the best model while registration runs on another (decision 31); left
        # unset, the one model writes Letters too.
        prose_model=ClaudeCli(os.environ.get("PROSE_MODEL") or model_name),
        printer=ChromiumPrinter(),
        schedule=_schedule(),
        runner=on_a_daemon_thread,
    )
    uvicorn.run(
        app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000"))
    )


def _schedule() -> Schedule | None:
    """How often Sources are polled. `0` leaves polling to the button on the dashboard."""
    value = os.environ.get("POLL_EVERY_HOURS", "")
    try:
        hours = float(value) if value else DEFAULT_POLL_EVERY_HOURS
    except ValueError:
        sys.exit(f"jobs: POLL_EVERY_HOURS is {value!r}, not a number of hours")
    return Schedule(every=timedelta(hours=hours)) if hours > 0 else None


def _origin() -> Origin | None:
    """The data repository the store is kept up to date with and pushed to. Unset on a machine
    whose `data/` is a repository of its own, created on the first start and pushed nowhere; on
    the server both come from the deployment's secrets."""
    url = os.environ.get("DATA_REPO_URL")
    if not url:
        return None
    return Origin(url=url, token=os.environ.get("DATA_REPO_TOKEN", ""))


if __name__ == "__main__":
    main()
