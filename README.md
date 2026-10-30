# Jobs

A personal job-search system. It watches places where positions appear, proposes them, helps
assemble tailored application documents, and records exactly what was sent to whom.

Two human gates are mandatory: one to pursue a Posting, one to send a Packet. The system never
submits on its own.

## What you need

- [Docker](https://docs.docker.com/get-docker/) with Compose, or Python 3.14 and git on macOS or
  Linux.
- A Claude subscription, for registering most Sources and for having a model write Letters.
  Everything else works without one; see [Without a model](#without-a-model).

Running it natively on Windows is not supported.

## Running it from the image

This is the recommended way. In a clone of this repository:

```sh
mkdir -p data && JOBS_UID=$(id -u) JOBS_GID=$(id -g) docker compose up --build
```

The first build takes a few minutes. The dashboard is then at `http://127.0.0.1:8000`.

Your data lands in `data/` in the clone: plain files, which the first start makes a git
repository of its own, and every change you make in the app is a commit there. Nothing is pushed
anywhere, and this repository ignores the directory. Back it up, or add a remote of your own and
push it yourself. The service runs as you, so the files are yours.

## Running it from source

```sh
python3.14 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/playwright install --with-deps --only-shell chromium
DATA_DIR=data .venv/bin/python -m jobs
```

The dashboard is at `http://127.0.0.1:8000`; `HOST` and `PORT` override that. The data lands in
`data/` as above. The browser installed on the third line renders the Sources whose listing a
script fills in and makes the PDF of a Letter; everything else runs without it.

## Signing in to Claude

The app asks a model through the `claude` command, which
`curl -fsSL https://claude.ai/install.sh | bash` installs.

- **From source** it uses the `claude` command on your `PATH`, signed in the way you would sign it
  in for yourself: run `claude` once and log in with your subscription.
- **From the image** the command inside the container is signed in by a token. Mint one on your
  own machine with `claude setup-token`, which prints a token good for a year, and start the image
  with it set:

  ```sh
  export CLAUDE_CODE_OAUTH_TOKEN=<the token>
  mkdir -p data && JOBS_UID=$(id -u) JOBS_GID=$(id -g) docker compose up --build
  ```

Two settings name the models, from the environment in either case:

| Variable | Default | What it does |
|---|---|---|
| `MODEL` | `claude-opus-5` | The model that works out how to read a Source. |
| `PROSE_MODEL` | whatever `MODEL` is | The model that writes Letters. |

## Without a model

With no credential the app still starts, and these work:

- Applications, from the dashboard, and their Documents.
- Sources whose URL is itself a readable page of postings, registered without a filter rule; any
  other Source needs a model to be registered.
- Letters written by hand, and their PDF.

## Which models

Only Claude is supported today. Other models may follow.

## What a Letter is written from

A model writes a Letter from what you place in the data directory by hand:

| Path | What it is |
|---|---|
| `data/brief.md` | The Brief: your voice, what you want, and what you never claim. Nothing is asked without it. |
| `data/resume.md` | Your resume as Markdown. Optional; without it the model knows only what the Brief tells it. |
| `data/examples/*.md` | The bodies of Letters you wrote before and picked as examples, one per file. Optional. |

Without the Brief, asking for a Letter is refused; the page of a Letter says when the resume or
the examples are missing.

A Packet is built from an Application's page, in one of four shapes, and its files are named after
your surname, which `data/letter/applicant.yaml` names as `surname: <your surname>`; without it no
Packet is built. Merged files put the resume right after the Letter: it is the Document whose name
says Lebenslauf, CV or resume.

## No login

The app has no login. Both ways above listen on your own machine only, at `127.0.0.1`; do not
change that to put it on a network. To run it on a server, reachable only over a private network,
see [`docs/deployment.md`](docs/deployment.md).

## Licence

Written by Dennis Kasper. MIT; see [`LICENSE`](LICENSE).

## Working on the code

Install with `.venv/bin/pip install -e '.[dev]'`, then:

```sh
.venv/bin/pytest
.venv/bin/mypy
.venv/bin/ruff check . && .venv/bin/ruff format .
```

- `CONTEXT.md` — the domain vocabulary. Read this first.
- `docs/adr/` — the decisions that shaped the design, and why.
- `AGENTS.md` — conventions for working in this repo.
