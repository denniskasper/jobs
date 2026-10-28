# Context

## Tickets ready now

!`bash /home/agent/runner/ready.sh`

Each line above is one ticket that is ready: open, labelled `ready-for-agent`, not a spec, not already done on this branch, and with every blocker closed or done on this branch. The list is already filtered, and it is the only source of truth for what work exists. Do not run your own query to find more. If it is empty, there is nothing to do.

## Recent commits (last 10)

!`git log --oneline -10`

# Task

You are an autonomous coding agent working through tickets one at a time, in a sandbox, with nobody watching. Read `AGENTS.md` first and follow it: it is this repository's rules, and they apply to you. Read `CONTEXT.md` for the vocabulary and use it.

## Which ticket

Take the ready ticket with the lowest number. Tickets are written in the order they are meant to be built.

## Workflow

1. **Explore**: read the ticket carefully, then its parent spec (`gh issue view <N>`, named under "Parent"), which holds the decisions the ticket rests on. Read the relevant source files and tests before writing any code. Respect the ADRs in `docs/adr/` and the decision log in `docs/decisions.md`.
2. **Plan**: decide what to change and why. Keep the change as small as the ticket allows.
3. **Execute**: Red, Green, Refactor. Write a failing test first, at the seam this repository already uses (`create_app` driven through its pages with the fixtures in `tests/conftest.py`, as `AGENTS.md` describes), then the implementation that passes it.
4. **Verify**: run `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, `.venv/bin/mypy` and `.venv/bin/pytest`, and fix every failure before committing. The virtualenv and Chromium are already set up, so the browser tests run here too. Never skip or weaken a test to get green.
5. **Commit**: make a single commit in the Conventional Commits form `AGENTS.md` requires, subject under ~50 characters, with `Closes #<N>` in the body. Follow `AGENTS.md` on versioning: a ticket is not a spec, so a `feat:` commit bumps nothing. Only the commit that finishes a spec's last ticket also closes the spec and bumps the minor, and you only do that if this ticket is the spec's last open one. In the body say what was done, any decision you made that the ticket did not make for you, and which checks ran.
6. **Report**: comment on the ticket with `gh issue comment <N>`: what you did, what you decided, and anything left over. **Do not close the ticket and do not push.** The commit stays on `sandcastle/work`; a person reviews it, and pushing `main` closes the ticket.

## Rules

- **One ticket per iteration.**
- **Never push, merge, close an issue or edit an issue's body.** A person does that after reading your work.
- **Never read or write `data/`**, and never copy anything personal into the repository, a commit message or a comment. Every name, page and Posting in a test is invented (`AGENTS.md`, "Personal data"). `tests/test_no_personal_data.py` must pass.
- **The server never fetches a page a user brought in**, on any site; tests use the fixtures' network, which refuses every request.
- **Never change `.sandcastle/`**: it is how you are run, and a person changes it.
- No commented-out code, no TODO comments.
- If you are blocked (missing context, checks you cannot get green, something only a person can decide), leave one comment on the ticket saying so, unless one saying the same is already there, and move on to the next ready ticket.

# Done

When every ready ticket is done or blocked, or the list at the top is empty, output the completion signal:

<promise>COMPLETE</promise>
