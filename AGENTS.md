# AGENTS.md

## Personal data

This repo is the engine. Every piece of personal state — Applications, Documents, CVs, letters,
correspondence — lives in `jobs-data`, cloned into the ignored `data/` directory. That boundary is
the entire privacy model.

Never copy content out of `data/` into this repository. Not into fixtures, tests, issues, docs,
ADRs, commit messages, or PR descriptions. If a test needs applicant data,
invent it.

`tests/fixtures/pages/` holds public postings only. Never save a page that required a login, and
never add an application confirmation or anything reached from `data/`.

Saved pages often carry third-party personal data — staff names, recruiter emails, direct-dial
numbers. A test needs the markup, never the person: before committing a page, replace every name,
address, profile link and phone number in it with an invented one, and put the addresses at
`example.org`. Leave the markup otherwise byte-for-byte, re-encoding any obfuscated mailto token
with the vector it came with. The institution stays real; only the people are invented.
`tests/fixtures/pages/README.md` records this per page.

`tests/test_no_personal_data.py` enforces this. It is not a formality; if it fails, something
personal is about to be committed.

This repository stays private.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/): `<type>(<optional scope>): <description>`.

Keep them short — a subject line under ~50 characters, lowercase, imperative mood, no trailing period. Omit the body unless the _why_ is genuinely non-obvious.

Common types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`.

```
feat(auth): add session refresh
fix: handle empty payload
docs: note commit conventions
```

## Versioning

The version is `__version__` in `src/jobs/__init__.py`, and nowhere else; the footer shows it.

- The commit that closes a spec issue (`Closes #N`) bumps the minor and resets the patch to 0.
- A `fix:` commit outside a spec in progress bumps the patch, in that same commit.
- Nothing else bumps it. It stays on `0.x`: the major changes only when the user says so.

## Checks

Python 3.14 in `.venv`, built with `python3.14 -m venv .venv` so it cannot come out on whatever
`python` happens to be first on the PATH; install with `pip install -e '.[dev]'`.

- Typecheck: `.venv/bin/mypy`
- Lint and format: `.venv/bin/ruff check .` and `.venv/bin/ruff format .`
- Tests: `.venv/bin/pytest`, or one file with `.venv/bin/pytest tests/test_dashboard.py`
- Run: `DATA_DIR=data .venv/bin/python -m jobs`

Tests drive `create_app(data_dir=, clock=, network=, committer=, model=)` through the fixtures in
`tests/conftest.py`. `prose_model=` is optional: a second model that writes Letters, without which
`model` does, as in the fixtures. `sleep=` is optional: how the
wait between two requests to one host is passed, which the fixtures make instant. `printer=` is
optional: without one no PDF of a Letter can be built and the page says so, and the fixtures hand it
one that remembers the HTML page it was given and answers with fixed bytes. `schedule=` is
optional too: without one, as in the fixtures, a poll only runs when a test asks for it. So is
`runner=`: without one a Registration waits until a test runs the next one, with
`run_next_registration` from `tests/conftest.py`, and an answer about a Letter until a test fetches
the next one, with `fetch_next_answer` beside it. No test
asks a real model. The few tests that render in the real browser skip where Chromium cannot start;
`.venv/bin/playwright install --with-deps --only-shell chromium` brings it, and the image has it.
The environment is read only in `src/jobs/__main__.py`.

Deployment, and what the container expects from its environment: `docs/deployment.md`.

## Agent skills

### Issue tracker

Issues and specs live as GitHub issues, read and written with the `gh` command.
See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, used verbatim as label names.
See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root.
See `docs/agents/domain.md`.
