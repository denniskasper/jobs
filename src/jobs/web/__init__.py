"""The web surface: server-rendered templates over the files on disk."""

import asyncio
import contextlib
import hashlib
import re
import sys
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from markupsafe import Markup
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from jobs import __version__, guess
from jobs.applications import (
    AlreadyBroughtIn,
    Status,
    change_status,
    create_application,
    list_applications,
    posting_of,
    read_application,
    write_down,
)
from jobs.clock import Clock
from jobs.commits import Committer
from jobs.documents import add_shared, list_shared
from jobs.fetching import Network
from jobs.letters import Letters
from jobs.models import Model
from jobs.polling import Condition, Poller, Schedule, health_of, last_poll
from jobs.postings import list_postings
from jobs.printing import NotPrinted, Printer, letter_page
from jobs.proposals import Listing, Proposals
from jobs.records import AlreadyExists, Malformed, NotCommitted, NotFound, Refused
from jobs.registrations import Registration, Registrations, Runner, State
from jobs.sources import (
    Problem,
    Rederivation,
    list_sources,
    read_source,
    rederive,
    save_source,
    set_enabled,
)
from jobs.sources import accept as accept_rederivation
from jobs.web import bookmark

HERE = Path(__file__).parent

# The stylesheet names the waiting badge `applied`.
STATUS_BADGES = {
    Status.TODO: "badge-todo",
    Status.WAITING: "badge-applied",
    Status.INTERVIEW: "badge-interview",
    Status.CLOSED: "badge-closed",
}


REFUSAL_STATUS_CODES: dict[type[Exception], int] = {
    NotFound: 404,
    AlreadyExists: 409,
    NotCommitted: 500,
}


HEALTH_BADGES = {
    Condition.HEALTHY: "badge-interview",
    Condition.QUIET: "badge-closed",
    Condition.UNHEALTHY: "badge-todo",
}


REGISTRATION_BADGES = {
    State.WAITING: "badge-closed",
    State.UNDER_WAY: "badge-closed",
    State.REFUSED: "badge-todo",
    State.REGISTERED: "badge-interview",
}


@dataclass(frozen=True)
class Marker:
    """What the Sources tab carries while there is something to know about a Registration: the
    badge whose dot it borrows, and what it stands for, in words for a screen reader."""

    badge: str
    words: str


# The neutral dot also for one that ended in a Source: the tab says that there is something to
# know, and only what is left to read is an alert.
TO_BE_KNOWN = Marker(
    "badge-closed", "A Registration is waiting or under way, or has ended in a Source"
)
TO_BE_READ = Marker("badge-todo", "A Registration has ended with something to read")


def _sources_marker(shown: Sequence[Registration]) -> Marker | None:
    """No count: it says that there is something to know, and what is to be read wins over what
    is only to be known. With nothing to know there is none."""
    if any(each.to_be_cleared for each in shown):
        return TO_BE_READ
    return TO_BE_KNOWN if shown else None


# How what a model writes is set on a page: as the PDF of a Letter sets it, with no markup of its
# own let through and no image fetched.
_MARKDOWN = MarkdownIt("commonmark", {"html": False}).disable("image")

# What the Register form asks for.
REGISTER_FIELDS = ("url", "label", "rule")

# What the page that creates an Application asks for.
CREATE_FIELDS = ("company", "role", "link", "deadline", "text")

# Set by the bookmark button when the selection it brings was cut.
CUT = "cut"

# What the user writes down on an Application's page.
WRITTEN_FIELDS = ("notes", "address")

# What is said where a PDF of a Letter would be, by a service that was started without a printer.
NO_PRINTER = "No PDF can be built: this service was started without a printer."

# What an Application's page adds to its address to show a waiting Revision as the PDF it would
# make, where it shows the difference without it.
VIEW = "view"
PREVIEW = "preview"

# What an Application's page is opened with when a link brought in again led to it.
AGAIN = "again"

# The icons a phone asks for, drawn from the favicon's tick, full bleed so a launcher may mask
# them to its own shape.
ICON_SIZES = (192, 512)

# What the Sources page adds to its address when it asks for itself again.
BY_ITSELF = "by-itself"


def _status_code(refusal: Refused | NotCommitted | None) -> int:
    if refusal is None:
        return 200
    return REFUSAL_STATUS_CODES.get(type(refusal), 422)


def _custom_property(stylesheet: bytes, name: str) -> str:
    """The value the stylesheet first gives a custom property: the one it has where nothing is
    dark."""
    found = re.search(rf"--{name}:\s*([^;]+);", stylesheet.decode())
    if found is None:
        raise ValueError(f"the stylesheet sets no --{name}")
    return found[1].strip()


def create_app(
    *,
    data_dir: Path,
    clock: Clock,
    network: Network,
    committer: Committer,
    model: Model,
    prose_model: Model | None = None,
    printer: Printer | None = None,
    sleep: Callable[[float], None] = time.sleep,
    schedule: Schedule | None = None,
    runner: Runner | None = None,
) -> Starlette:
    """Build the service around its injected boundaries. Without a `prose_model` Letters are
    written by `model`, which registration asks. Without a `printer` no PDF of a Letter
    can be built, and the page says so. `sleep` is how the time between two
    requests to one host is waited out. Without a `schedule` a poll only runs when asked for.
    Without a `runner` a Registration waits until `app.state.registrations` is told to run the
    next one, and an answer about a Letter until `app.state.letters` is told to fetch it."""
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals["version"] = __version__
    # A model writes Markdown. It is set as such, and any markup in it is shown, never run.
    templates.env.filters["markdown"] = lambda text: Markup(_MARKDOWN.render(text))
    templates.env.filters["markdown_inline"] = lambda text: Markup(_MARKDOWN.renderInline(text))
    # A browser keeps a stylesheet it has seen, so the address it is linked at names what it
    # holds: a new one is fetched with the first page that wants it.
    stylesheet = (HERE / "static" / "dashboard.css").read_bytes()
    templates.env.globals["stylesheet"] = hashlib.sha256(stylesheet).hexdigest()[:12]
    poller = Poller(data_dir, committer, network, clock, sleep)
    proposing = Proposals(data_dir, committer, clock)
    registrations = Registrations(data_dir, committer, network, model, clock, sleep, runner)
    letters = Letters(data_dir, committer, prose_model or model, runner)

    def page(
        request: Request,
        template: str,
        context: dict[str, Any],
        refusal: Refused | NotCommitted | None = None,
    ) -> Response:
        return templates.TemplateResponse(
            request,
            template,
            {
                "refusal": refusal,
                "not_pushed": committer.not_pushed(),
                "today": clock().date(),
                # As fresh as the page load: only the Sources page brings itself up to date.
                # Looked at here, after the Sources page has said what was seen: the page that
                # shows how one ended is the one that says so, and its tab has nothing to add.
                "sources_marker": _sources_marker(registrations.look()),
                # Looked at after the Proposals page has shown them, which leaves none new there.
                "proposals_new": proposing.look().new,
                **context,
            },
            status_code=_status_code(refusal),
        )

    def render(request: Request, refusal: Refused | NotCommitted | None = None) -> Response:
        listing = list_applications(data_dir)
        return page(
            request,
            "dashboard.html",
            {
                "applications": listing.applications,
                "problems": listing.problems,
                "entered": {},
                "statuses": list(Status),
                "status_badges": STATUS_BADGES,
            },
            refusal,
        )

    def dashboard(request: Request) -> Response:
        return render(request)

    def favicon(request: Request) -> Response:
        # Browsers and tools ask for this path whatever icon a page links.
        return FileResponse(HERE / "static" / "favicon.svg", media_type="image/svg+xml")

    # The desk's colour where nothing is dark, which the page's own theme-color names too.
    ground = _custom_property(stylesheet, "ground")

    def manifest(request: Request) -> Response:
        # What makes the app installable and a target of the share sheet. A share opens the page
        # that creates an Application as a plain request, so no service worker is needed.
        icons = [
            {
                "src": str(request.url_for("static", path=f"icon-{size}.png")),
                "sizes": f"{size}x{size}",
                "type": "image/png",
                "purpose": purpose,
            }
            for purpose in ("any", "maskable")
            for size in ICON_SIZES
        ]
        return JSONResponse(
            {
                "name": "Job search",
                "short_name": "Jobs",
                "start_url": str(request.url_for("dashboard")),
                "display": "standalone",
                "background_color": ground,
                "theme_color": ground,
                "icons": icons,
                "share_target": {
                    "action": str(request.url_for("new_application")),
                    "method": "GET",
                    "params": {"title": "title", "text": "text", "url": "link"},
                },
            },
            media_type="application/manifest+json",
        )

    def health(request: Request) -> Response:
        # What the container's health check asks. A volume that failed to mount is the one
        # thing a running process cannot see from the inside any other way.
        if not data_dir.is_dir():
            return PlainTextResponse("no data directory", status_code=503)
        return PlainTextResponse("ok")

    def render_new(
        request: Request,
        entered: dict[str, str],
        refusal: Refused | None = None,
        cut: bool = False,
    ) -> Response:
        return page(
            request,
            "new_application.html",
            {
                "entered": entered,
                "cut": cut,
                "bookmark": bookmark.button(str(request.url_for("new_application"))),
            },
            refusal,
        )

    def new_application(request: Request) -> Response:
        # Opened filled in, by a share or a bookmark, it only shows the form: nothing is created
        # until Create is pressed, and nothing is fetched at all.
        query = request.query_params
        entered = {field: query.get(field, "") for field in CREATE_FIELDS}
        return render_new(
            request,
            guess.filled_in(entered, title=query.get("title", "")),
            # The bookmark button says so when it cut the selection to fit in an address.
            cut=CUT in query,
        )

    async def create(request: Request) -> Response:
        form = await request.form()
        entered = guess.filled_in({field: str(form.get(field, "")) for field in CREATE_FIELDS})
        try:
            # Off the event loop: the commit is pushed, and a push waits on the network.
            created = await run_in_threadpool(
                create_application, data_dir, committer, **entered, on=clock().date()
            )
        except AlreadyBroughtIn as already:
            # Nothing was created: what was entered with it is dropped.
            return RedirectResponse(
                request.url_for(
                    "application", folder=already.application.folder
                ).include_query_params(**{AGAIN: "1"}),
                status_code=303,
            )
        except Refused as refusal:
            return render_new(request, entered, refusal)
        except NotCommitted as not_committed:
            return render(request, not_committed)
        return RedirectResponse(
            request.url_for("application", folder=created.folder), status_code=303
        )

    async def set_status(request: Request) -> Response:
        form = await request.form()
        try:
            await run_in_threadpool(
                change_status,
                data_dir,
                committer,
                request.path_params["folder"],
                to=str(form.get("status", "")),
                on=clock().date(),
            )
        except (Refused, NotCommitted) as refusal:
            return render(request, refusal)
        return RedirectResponse(request.url_for("dashboard"), status_code=303)

    def render_application(
        request: Request,
        refusal: Refused | NotCommitted | None = None,
        typed: str | None = None,
        said: str = "",
    ) -> Response:
        folder = request.path_params["folder"]
        try:
            application = read_application(data_dir, folder)
        except Refused as not_opened:
            return render(request, refusal or not_opened)
        try:
            posting, posting_problem = posting_of(data_dir, folder), None
        except Malformed as malformed:
            posting, posting_problem = None, str(malformed)
        standing = letters.look(folder)
        return page(
            request,
            "application.html",
            {
                "application": application,
                "posting": posting,
                "posting_problem": posting_problem,
                "standing": standing,
                "prints": printer is not None,
                # The difference unless the preview was switched to, which only a Revision has.
                "previewed": request.query_params.get(VIEW) == PREVIEW,
                "view_param": VIEW,
                "brought_in_again": refusal is None and AGAIN in request.query_params,
                "preview_view": PREVIEW,
                "no_printer": NO_PRINTER,
                # A body typed by hand and refused is kept, so a refusal does not cost the edit.
                "typed": typed,
                # And so is a message that was refused.
                "said": said,
                # Only the page as it is asked for: with a refusal it is not at its own address,
                # and asking for that again would not be asking for this page.
                "keeps_up": refusal is None and standing.under_way,
                "status_badges": STATUS_BADGES,
                "entered": {},
            },
            refusal,
        )

    def application(request: Request) -> Response:
        return render_application(request)

    async def save_application(request: Request) -> Response:
        form = await request.form()
        folder = request.path_params["folder"]
        # Only what the form holds: a field it does not have is left as it is.
        written = {field: str(form[field]) for field in WRITTEN_FIELDS if field in form}
        try:
            await run_in_threadpool(write_down, data_dir, committer, folder, **written)
        except (Refused, NotCommitted) as refusal:
            # Refused only where there is no Application to show; that page says so instead.
            return render_application(request, refusal)
        return RedirectResponse(request.url_for("application", folder=folder), status_code=303)

    async def ask_for_letter(request: Request) -> Response:
        folder = request.path_params["folder"]
        try:
            # Only taken on here: the model is asked in the background.
            await run_in_threadpool(letters.ask_for_first, folder)
        except Refused as refusal:
            return render_application(request, refusal)
        return RedirectResponse(request.url_for("application", folder=folder), status_code=303)

    async def say_about_letter(request: Request) -> Response:
        form = await request.form()
        folder = request.path_params["folder"]
        message = str(form.get("message", ""))
        try:
            # Only taken on here: the model is asked in the background.
            await run_in_threadpool(letters.say, folder, message)
        except Refused as refusal:
            return render_application(request, refusal, said=message)
        return RedirectResponse(request.url_for("application", folder=folder), status_code=303)

    async def send_again(request: Request) -> Response:
        folder = request.path_params["folder"]
        try:
            await run_in_threadpool(letters.send_again, folder)
        except Refused as refusal:
            return render_application(request, refusal)
        return RedirectResponse(request.url_for("application", folder=folder), status_code=303)

    async def accept_revision(request: Request) -> Response:
        folder = request.path_params["folder"]
        try:
            await run_in_threadpool(letters.accept, folder)
        except (Refused, NotCommitted) as refusal:
            return render_application(request, refusal)
        return RedirectResponse(request.url_for("application", folder=folder), status_code=303)

    async def reject_revision(request: Request) -> Response:
        folder = request.path_params["folder"]
        try:
            await run_in_threadpool(letters.reject, folder)
        except (Refused, NotCommitted) as refusal:
            return render_application(request, refusal)
        return RedirectResponse(request.url_for("application", folder=folder), status_code=303)

    async def edit_letter(request: Request) -> Response:
        form = await request.form()
        folder = request.path_params["folder"]
        body = str(form.get("body", ""))
        try:
            await run_in_threadpool(letters.edit, folder, body)
        except (Refused, NotCommitted) as refusal:
            return render_application(request, refusal, typed=body)
        return RedirectResponse(request.url_for("application", folder=folder), status_code=303)

    def print_body(folder: str, of_revision: bool) -> bytes:
        """The PDF of the Letter in `folder` as it stands, or of the one the Revision that waits
        there would make: the same page but for the body."""
        found = read_application(data_dir, folder)
        standing = letters.look(folder)
        if standing.problem:
            raise Malformed(standing.problem)
        body = standing.waiting if of_revision else standing.letter
        if not body:
            raise NotFound(
                f"{folder} has no Revision waiting to preview."
                if of_revision
                else f"{folder} has no Letter to build a PDF of."
            )
        assert printer is not None
        return printer.print(letter_page(data_dir, found, body, clock().date()))

    async def build_pdf(request: Request, of_revision: bool) -> Response:
        if printer is None:
            return PlainTextResponse(NO_PRINTER, status_code=503)
        try:
            # Off the event loop: a browser is started for the page.
            pdf = await run_in_threadpool(print_body, request.path_params["folder"], of_revision)
        except Refused as refusal:
            return PlainTextResponse(str(refusal), status_code=_status_code(refusal))
        except Malformed as malformed:
            return PlainTextResponse(f"No PDF can be built: {malformed}.", status_code=422)
        except NotPrinted as not_printed:
            return PlainTextResponse(str(not_printed), status_code=503)
        name = "revision" if of_revision else "letter"
        return Response(
            pdf,
            media_type="application/pdf",
            headers={
                # Told apart by name once saved: a Revision is not the Letter yet.
                "Content-Disposition": f'inline; filename="{name}.pdf"',
                # Built anew whenever it is asked for: the body, the address and the day
                # change, and none of them shows in the address of the PDF.
                "Cache-Control": "no-store",
            },
        )

    async def letter_pdf(request: Request) -> Response:
        return await build_pdf(request, of_revision=False)

    async def revision_pdf(request: Request) -> Response:
        return await build_pdf(request, of_revision=True)

    def render_sources(
        request: Request,
        refusal: Refused | NotCommitted | None = None,
        entered: dict[str, str] | None = None,
    ) -> Response:
        listing = list_sources(data_dir)
        polls = {each.name: last_poll(data_dir, each.name) for each in listing.sources}
        shown = registrations.look()
        # Showing this page is what counts as the user having seen how a Registration ended,
        # unless the page asked for itself: that turn may have passed with nobody looking. The
        # last turn counts all the same: no other follows, so what it shows stays on the page
        # for whoever has it open.
        by_itself = BY_ITSELF in request.query_params
        still_going = any(not each.ended for each in shown)
        if not by_itself or not still_going:
            registrations.seen(shown)
        return page(
            request,
            "sources.html",
            {
                "sources": listing.sources,
                "problems": listing.problems,
                "polls": polls,
                "health": {
                    each.name: health_of(each, polls[each.name]) for each in listing.sources
                },
                "health_badges": HEALTH_BADGES,
                "registrations": shown,
                "registration_badges": REGISTRATION_BADGES,
                # Only the Sources page as it is asked for: with a refusal it is another page,
                # not reached by GET or not found, and may hold what was entered.
                "reloads_itself": refusal is None and still_going,
                "by_itself_param": BY_ITSELF,
                "asked_for_by_itself": by_itself,
                "entered": entered or {},
            },
            refusal,
        )

    def render_source(
        request: Request,
        refusal: Refused | NotCommitted | None = None,
        entered: str | None = None,
        rederivation: Rederivation | None = None,
    ) -> Response:
        name = request.path_params["name"]
        try:
            found, text = read_source(data_dir, name)
        except NotFound as not_found:
            return render_sources(request, refusal or not_found)
        poll = last_poll(data_dir, name)
        return page(
            request,
            "source.html",
            {
                "name": name,
                "source": None if isinstance(found, Problem) else found,
                "problem": found if isinstance(found, Problem) else None,
                "poll": poll,
                "health": None if isinstance(found, Problem) else health_of(found, poll),
                "health_badges": HEALTH_BADGES,
                "rederivation": rederivation,
                "kept": list_postings(data_dir, name),
                # What was entered and refused is kept, so a slip does not cost the whole edit.
                "text": text if entered is None else entered,
                "entered": {},
            },
            refusal,
        )

    def sources(request: Request) -> Response:
        # "Try again" leads here with what was entered, for the form to show. Nothing else
        # comes of it until Register is pressed.
        entered = {
            field: request.query_params[field]
            for field in REGISTER_FIELDS
            if field in request.query_params
        }
        return render_sources(request, entered=entered)

    def source(request: Request) -> Response:
        return render_source(request)

    async def register(request: Request) -> Response:
        form = await request.form()
        entered = {field: str(form.get(field, "")) for field in REGISTER_FIELDS}
        try:
            registrations.hand_in(**entered)
        except Refused as refusal:
            return render_sources(request, refusal, entered)
        return RedirectResponse(request.url_for("sources"), status_code=303)

    def clear_registration(request: Request) -> Response:
        registrations.clear(request.path_params["identifier"])
        return RedirectResponse(request.url_for("sources"), status_code=303)

    async def save(request: Request) -> Response:
        form = await request.form()
        text = str(form.get("text", ""))
        name = request.path_params["name"]
        try:
            await run_in_threadpool(save_source, data_dir, committer, name, text)
        except Refused as refusal:
            return render_source(request, refusal, text)
        except NotCommitted as not_committed:
            return render_source(request, not_committed)
        return RedirectResponse(request.url_for("source", name=name), status_code=303)

    async def set_source_enabled(request: Request) -> Response:
        form = await request.form()
        name = request.path_params["name"]
        try:
            await run_in_threadpool(
                set_enabled, data_dir, committer, name, enabled=form.get("enabled") == "true"
            )
        except (Refused, NotCommitted) as refusal:
            return render_source(request, refusal)
        return RedirectResponse(request.url_for("source", name=name), status_code=303)

    async def derive_again(request: Request) -> Response:
        name = request.path_params["name"]
        try:
            # Off the event loop: exploring a URL waits on the network and on the model.
            rederivation = await run_in_threadpool(
                rederive, data_dir, network, model, name, now=clock(), sleep=sleep
            )
        except Refused as refusal:
            return render_source(request, refusal)
        # Shown and not kept: nothing is written until the difference is accepted.
        return render_source(request, rederivation=rederivation)

    async def accept_derived(request: Request) -> Response:
        form = await request.form()
        name = request.path_params["name"]
        try:
            await run_in_threadpool(
                accept_rederivation,
                data_dir,
                committer,
                name,
                proposed=str(form.get("proposed", "")),
                base=str(form.get("base", "")),
            )
        except (Refused, NotCommitted) as refusal:
            return render_source(request, refusal)
        return RedirectResponse(request.url_for("source", name=name), status_code=303)

    async def poll(request: Request) -> Response:
        name = request.path_params["name"]
        try:
            # Off the event loop: a walk waits on the network, and between its requests.
            await run_in_threadpool(poller.poll, name)
        except (Refused, NotCommitted) as refusal:
            return render_source(request, refusal)
        return RedirectResponse(request.url_for("source", name=name), status_code=303)

    async def poll_all(request: Request) -> Response:
        try:
            await run_in_threadpool(poller.poll_all)
        except NotCommitted as not_committed:
            return render_sources(request, not_committed)
        return RedirectResponse(request.url_for("sources"), status_code=303)

    def render_proposals(
        request: Request,
        listing: Listing,
        refusal: Refused | NotCommitted | None = None,
    ) -> Response:
        return page(request, "proposals.html", {"listing": listing, "entered": {}}, refusal)

    async def proposals(request: Request) -> Response:
        try:
            # Off the event loop: what is shown for the first time is committed and pushed.
            listing = await run_in_threadpool(proposing.show)
        except NotCommitted as not_committed:
            return render_proposals(request, proposing.look(), not_committed)
        return render_proposals(request, listing)

    def left_out(request: Request) -> Response:
        return page(request, "left_out.html", {"listing": proposing.look(), "entered": {}})

    async def dismiss(request: Request) -> Response:
        try:
            await run_in_threadpool(
                proposing.dismiss, request.path_params["source"], request.path_params["name"]
            )
        except (Refused, NotCommitted) as refusal:
            return render_proposals(request, proposing.look(), refusal)
        return RedirectResponse(request.url_for("proposals"), status_code=303)

    async def pursue(request: Request) -> Response:
        form = await request.form()
        ticked = [str(each) for each in form.getlist("posting")]
        refusals: list[str] = []
        for each in ticked:
            source, _, name = each.partition("/")
            try:
                # Off the event loop: the commit is pushed, and a push waits on the network.
                await run_in_threadpool(proposing.pursue, source, name)
            except Refused as refusal:
                # The others that were ticked are still pursued.
                refusals.append(str(refusal))
            except NotCommitted as not_committed:
                # What comes after it is left proposed, since its commit would fail as well.
                said = " ".join([*refusals, str(not_committed)])
                return render_proposals(request, proposing.look(), NotCommitted(said))
        if not ticked:
            refusals.append("Nothing was ticked.")
        if refusals:
            return render_proposals(request, proposing.look(), Refused(" ".join(refusals)))
        return RedirectResponse(request.url_for("proposals"), status_code=303)

    def render_documents(
        request: Request,
        refusal: Refused | NotCommitted | None = None,
        entered: dict[str, str] | None = None,
    ) -> Response:
        try:
            shared, problem = list_shared(data_dir), None
        except Malformed as malformed:
            shared, problem = [], str(malformed)
        return page(
            request,
            "documents.html",
            {"documents": shared, "problem": problem, "entered": entered or {}},
            refusal,
        )

    def documents(request: Request) -> Response:
        return render_documents(request)

    async def add_document(request: Request) -> Response:
        form = await request.form()
        upload = form.get("file")
        entered = {"name": str(form.get("name", ""))}
        content, filename = b"", ""
        if isinstance(upload, UploadFile):
            content, filename = await upload.read(), upload.filename or ""
        try:
            await run_in_threadpool(
                add_shared,
                data_dir,
                committer,
                name=entered["name"].strip() or filename,
                content=content,
            )
        except Refused as refusal:
            return render_documents(request, refusal, entered)
        except NotCommitted as not_committed:
            return render_documents(request, not_committed)
        return RedirectResponse(request.url_for("documents"), status_code=303)

    async def keep_polling(schedule: Schedule) -> None:
        while True:
            try:
                await run_in_threadpool(poller.poll_due, schedule.every)
            except Exception as error:
                # Deliberately everything: the schedule has to outlive any one run.
                print(f"jobs: a scheduled poll failed: {error}", file=sys.stderr)
            await asyncio.sleep(schedule.check_every)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        polling = asyncio.create_task(keep_polling(schedule)) if schedule else None
        try:
            yield
        finally:
            if polling:
                polling.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await polling

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/", dashboard),
            Route("/health", health),
            Route("/favicon.ico", favicon),
            Route("/manifest.webmanifest", manifest),
            Route("/applications", create, methods=["POST"]),
            Route("/applications/new", new_application),
            Route("/applications/{folder:path}/status", set_status, methods=["POST"]),
            Route("/applications/{folder:path}/letter/ask", ask_for_letter, methods=["POST"]),
            Route("/applications/{folder:path}/letter/say", say_about_letter, methods=["POST"]),
            Route("/applications/{folder:path}/letter/again", send_again, methods=["POST"]),
            Route("/applications/{folder:path}/letter/accept", accept_revision, methods=["POST"]),
            Route("/applications/{folder:path}/letter/reject", reject_revision, methods=["POST"]),
            Route("/applications/{folder:path}/letter/edit", edit_letter, methods=["POST"]),
            Route("/applications/{folder:path}/letter/revision.pdf", revision_pdf),
            Route("/applications/{folder:path}/letter.pdf", letter_pdf),
            Route("/applications/{folder:path}", application),
            Route(
                "/applications/{folder:path}",
                save_application,
                methods=["POST"],
                name="save_application",
            ),
            Route("/proposals", proposals),
            Route("/proposals", pursue, methods=["POST"]),
            Route("/proposals/left-out", left_out),
            Route("/proposals/{source}/{name}/dismiss", dismiss, methods=["POST"]),
            Route("/documents", documents),
            Route("/documents", add_document, methods=["POST"]),
            Route("/sources", sources),
            Route("/sources", register, methods=["POST"]),
            Route("/registrations/{identifier:int}/clear", clear_registration, methods=["POST"]),
            Route("/sources/{name}", source),
            Route("/sources/{name}", save, methods=["POST"], name="save"),
            Route(
                "/sources/{name}/enabled", set_source_enabled, methods=["POST"], name="set_enabled"
            ),
            Route("/sources/{name}/poll", poll, methods=["POST"]),
            Route("/sources/{name}/rederive", derive_again, methods=["POST"]),
            Route("/sources/{name}/rederive/accept", accept_derived, methods=["POST"]),
            Route("/poll", poll_all, methods=["POST"]),
            Mount("/static", StaticFiles(directory=HERE / "static"), name="static"),
        ],
    )
    app.state.registrations = registrations
    app.state.letters = letters
    return app
