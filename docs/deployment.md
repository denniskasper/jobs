# Deployment

The service runs on a server under Dokploy, reachable over the tailnet only (decisions 9, 10, 31b
and 32). CI runs the checks on every push and deploys a green `main`. Locally it runs either
directly (`DATA_DIR=data .venv/bin/python -m jobs`) or from the image (`compose.yaml`).

What follows calls the server **`dokploy`**, its tailnet address `100.x.y.z`, its public address
`203.0.113.7`, the tailnet `example.ts.net` and the service's name on it `jobs.example.ts.net`. Put
your own in their place.

Everything the repository can hold is in it: the `Dockerfile`, `.github/workflows/ci.yaml`, and
under `deploy/`:

- `compose.tailnet.yaml`, the compose service Dokploy deploys: the application beside a Tailscale
  sidecar, sharing its network.
- `serve.json`, how the sidecar serves the application on the tailnet.
- `dokploy-deploy.sh`, which asks Dokploy to deploy and waits for it, for CI and by hand alike.
- `deploy.sh`, a deployment by hand: the checks, the guards, then `dokploy-deploy.sh`.
- `verify-not-public.sh`, which asks for the dashboard the way a stranger would and fails if it
  answers.

What follows is the part that lives outside it, set up once by hand.

## What the container expects

| Variable | Where it comes from | Meaning |
|---|---|---|
| `DATA_DIR` | the image, `/data` | The store. A volume must be mounted here. |
| `DATA_REPO_URL` | Dokploy environment | HTTPS URL of the `jobs-data` repository. |
| `DATA_REPO_TOKEN` | Dokploy environment | A fine-grained GitHub token, **`jobs-data` only, Contents read and write**. |
| `HOST`, `PORT` | the image, `0.0.0.0:8000` | Where uvicorn listens inside the container. |
| `MODEL` | Dokploy environment, optional, default `claude-opus-5` | The model that derives a Source's config at registration (decision 39). |
| `PROSE_MODEL` | Dokploy environment, optional, default whatever `MODEL` is | The model that writes Letters, so that prose can run on the best model while registration runs on another (decision 31). Asked through the same command with the same credential, and given no tools either. |
| `CLAUDE_CODE_OAUTH_TOKEN` | Dokploy environment | How the `claude` command in the image is signed in. Minted by hand with `claude setup-token`, good for a year. |
| `POLL_EVERY_HOURS` | optional, default `24` | How long after its last poll a Source is polled again, with nobody asking. `0` turns the schedule off and leaves polling to the dashboard. |
| `DEV` | unset in the container | `1` on a dev server: every page carries a banner saying it is not the deployed service. |

At start the service clones `DATA_REPO_URL` into an empty `DATA_DIR`, or fast-forwards the clone
already there (`src/jobs/store.py`). The token is handed to git through its environment; it is
never part of the URL and never written into the volume. If the fast-forward fails — no network,
or the clone has diverged — the service still starts on the store it has and says why on stderr.
If there is no store and none can be cloned, it refuses to start.

Every commit the service makes is then pushed to `DATA_REPO_URL` (`src/jobs/commits.py`), and so
is whatever an earlier run left behind, once at start. A push that fails does not undo anything:
the write and the commit stand, the dashboard says **Not pushed** with git's reason, and the next
push that succeeds carries everything along. A push is never forced. If the data repository has
commits the server does not have, someone other than the server wrote to it (decision 10 says
nobody should); the dashboard says so and nothing is overwritten until a human has brought the two
together inside the volume.

The token is the only credential that can write anywhere, so it is scoped as narrowly as GitHub
allows: a fine-grained token with **Repository access → Only select repositories → `jobs-data`**
and **Permissions → Contents: Read and write**, nothing else. It cannot reach the engine
repository or any other.

Registering a Source asks a model, through the `claude` command (`src/jobs/models.py`), and the
image carries it: a single glibc-linked binary installed under the unprivileged user, pinned to the
version the flags were checked against. No node runtime comes with it. A URL that is itself a
readable posting page registers without one. The only other thing that asks a model is a Letter,
through the same command: `PROSE_MODEL` names the model for it, and left unset it is `MODEL`.

The command is signed in by `CLAUDE_CODE_OAUTH_TOKEN` alone — no config directory, no keychain,
nothing on the volume. Mint it on a machine with a browser:

```sh
claude setup-token
```

It needs a Claude subscription, prints a token good for a year, and stores it nowhere, so paste it
straight into Dokploy's environment and then delete whatever you pasted it from. It can make model
requests and nothing else. **Write down when it expires**: the year runs out silently, and the
first sign is a registration refused with *"the credential for claude is missing or has expired"*.
Re-minting is the same one command.

A prompt carries text from pages anyone can write, so the model is given no tools, no settings and
no MCP servers (decision 39), and asked from a temporary directory where no project instructions
are read. Checked in the built image: asked to list files and read `/etc/hostname`, it answers that
it has no Bash, Read or Glob tool to do it with.

Listings that fill in their postings with a script are rendered in Chromium
(`Chromium` in `src/jobs/fetching.py`, decision 45). The image carries it, under
`/opt/playwright`, together with the system libraries it needs; it is started for one page and
closed again, so it costs memory only while a page is being rendered, a few hundred megabytes. The
`PLAYWRIGHT_VERSION` in the `Dockerfile` has to be raised by hand: a Playwright only drives the
Chromium it was released with, so the two are installed in one layer. Run directly on a machine,
the service needs `.venv/bin/playwright install --with-deps --only-shell chromium` once; without
it everything else works, a Source that needs the browser says so when polled, and one registered
there is kept as unfinished until it is derived again where there is a browser.

The same Chromium prints a Letter to a PDF (`ChromiumPrinter` in `src/jobs/printing.py`, decision
48), so the image needs nothing more for it and no setting names it. It is started when an
Application's page asks for the PDF and closed again; the page it prints is built by the service,
runs no script, and the PDF is kept nowhere. Where Chromium cannot start, the place of the PDF says
why and everything else about a Letter works. The Letter is set in the engine's template unless the
data repository holds `letter/template.html` and `letter/style.css`, either or both; a template is
handed `stylesheet`, `address` as its lines, `date` and `body`.

No other credential belongs in this deployment. In particular nothing employer-facing: no portal
login, no mail account (decisions 24 and 33).

## Dokploy, once

On `dokploy`, a new project **`jobs`**, environment `production`, holding a single service. There
is no database service to add: files are the database (ADR 0002), and the only state is the git
clone in the volume.

1. New **Compose** service, `web`. Provider: GitHub, repository `jobs`, branch `main`, compose type
   **docker-compose**, compose path `./deploy/compose.tailnet.yaml`. If the repository is private,
   the Dokploy GitHub App needs access granted to it. Isolated deployment, randomize, suffix and
   submodules stay off.
2. General → **Autodeploy: off**, and no `On Push` trigger. This may differ from other services on
   the host, and deliberately: if Dokploy listened to the push, the checks and the deployment would
   race and a red run would ship anyway (decision 31b). GitHub Actions triggers the deployment
   instead, after the checks.
3. Environment, with **Create env file** on, so Dokploy writes it to `deploy/.env` beside the
   compose file, where Compose reads it:
   - `TS_AUTHKEY`, the sidecar's key to join the tailnet; see below.
   - `DATA_REPO_URL`, `DATA_REPO_TOKEN` and `CLAUDE_CODE_OAUTH_TOKEN`, as in the table above.
   - Optionally `MODEL`, `PROSE_MODEL` and `POLL_EVERY_HOURS`. Left unset, each falls back to its
     default.

   Nothing else, and nothing employer-facing ever (decisions 24 and 33). The compose file refuses
   to start without the four that are required. What Dokploy adds of its own (`APP_NAME`,
   `COMPOSE_PROJECT_NAME`, `DOCKER_CONFIG`) is left as it is.
4. **No domains and no mounts.** Both volumes come from the compose file: `jobs-data`, the
   application's `/data`, and `tailscale-state`, the sidecar's node identity. They are named
   volumes, not bind mounts: `jobs-data` takes the unprivileged user's ownership from the image on
   first use. A domain would give the service a Traefik router, which is exactly the public route
   this arrangement does without.
5. Profile → API keys: generate one for `deploy/deploy.sh` and for the CI deploy job.

The compose id, for `DOKPLOY_COMPOSE_ID`, is the last path segment of the service's URL in Dokploy.
It is a *compose* service, so the deploy script calls `compose.deploy`, not `application.deploy`.

**Before the first deployment:** Dokploy builds the branch as GitHub has it, so `main` has to be
pushed. Nothing is deployable until it is.

### The sidecar's auth key

In the Tailscale admin console, Settings → Keys → **Generate auth key**:

- **One-off**, not reusable. The key is only used on the sidecar's first start; from then on the
  `tailscale-state` volume holds the node's identity.
- **Not ephemeral.** An ephemeral node is removed whenever it goes offline, which a redeploy does.
- **Pre-approved**, where the tailnet requires device approval.

Put it in `TS_AUTHKEY` and deploy. The node joins as `jobs`, the compose file's `hostname`, so the
application is at `https://jobs.example.ts.net`.

**Then disable key expiry** for the `jobs` machine in the admin console. An untagged node expires
after the tailnet's key expiry, 180 days by default, and drops off the tailnet, taking the
application with it — silently, since nothing on the server notices.

A tagged key, say `tag:jobs`, is the alternative: a tagged node has key expiry disabled by itself,
but the tag needs a `tagOwners` entry in the tailnet policy first, and the node then belongs to the
tag rather than to a user, outside every rule written for that user.

If the `tailscale-state` volume is ever lost, mint a new one-off key, set `TS_AUTHKEY`, redeploy,
and remove the old `jobs` machine from the admin console; otherwise the new node comes up as
`jobs-1`, under another name.

## Reachable over the tailnet only

The application has no login (decision 32), so the boundary is that nothing outside the tailnet can
reach it at all.

The compose service is two containers. The **sidecar**, `tailscale/tailscale`, is a node of its
own on the tailnet, named `jobs`, in userspace networking: no `/dev/net/tun`, no `NET_ADMIN`, no
privileged flags. The **application** shares the sidecar's network (`network_mode:
service:tailscale`), listens on port 8000 there, publishes no port to the host and joins no other
network.

The sidecar serves it by `deploy/serve.json`, handed over as `TS_SERVE_CONFIG`: HTTPS on 443 under
the node's own MagicDNS name, `${TS_CERT_DOMAIN}`, which the sidecar fills in as
`jobs.example.ts.net`, proxied to `http://127.0.0.1:8000`. The certificate is the tailnet's own,
issued to the node by Tailscale; this needs **MagicDNS** and **HTTPS certificates** enabled in the
admin console's DNS settings. Nothing in Dokploy or Traefik is involved.

So there is no public route to filter: no Traefik router names the service, no port is published,
and the name does not resolve off the tailnet. **Funnel stays off** for the node: it is the one
switch that would put a tailnet name on the public internet.

Why a sidecar rather than Traefik's Tailscale certificate resolver: that resolver only issues
certificates for `<machine>.<tailnet>.ts.net`, so every private application on the server would
share the one hostname. A sidecar is a node of its own, so `jobs.example.ts.net` is simply its
name.

What it replaced is worth knowing on a server whose other applications Dokploy routes through
Traefik. Traefik there listens on the public interface and routes by `Host` header alone, so a
private application behind it is a route that has to be filtered, by an allow-list kept in a
configuration Dokploy regenerates on every deployment. This service is not routed by Traefik at
all: asked at the server's public address under the service's name, Traefik answers 404, because no
router knows it. If the service was ever served under a public name before, and a wildcard DNS
record still leads that name to the server, `JOBS_RETIRED_HOSTNAME` has `verify-not-public.sh`
check that nothing answers for it there either; a stale router would.

Then verify, from a machine that is **not** on the tailnet:

```sh
JOBS_HOSTNAME=jobs.example.ts.net SERVER_PUBLIC_IP=203.0.113.7 deploy/verify-not-public.sh
```

It first insists that the name does **not** resolve: a tailnet address means the machine is on the
tailnet and the test would prove nothing; a public one means something publishes the name, Funnel
being the likely cause. Then it tries the doors anyway: the public address on 443 and 80 with the
name presented, and port 8000 in case it was ever published, over IPv6 as well when
`SERVER_PUBLIC_IPV6` is set.

| Variable | Meaning |
|---|---|
| `JOBS_HOSTNAME` | The service's MagicDNS name, `jobs.example.ts.net`. |
| `SERVER_PUBLIC_IP` | The server's public address, `203.0.113.7`. |
| `SERVER_PUBLIC_IPV6` | Optional, the server's public IPv6 address, if it has one. |
| `JOBS_RETIRED_HOSTNAME` | Optional, a public name the dashboard used to answer on. Asked for as public DNS resolves it, which a wildcard record may still do to the server; nothing may answer there. CI's job does not pass it, so it is checked by hand. |

CI's `not-public` job runs the same script from a GitHub runner after every deployment. After a
change to the compose file, `serve.json` or the tailnet's settings, either run the script by hand
from off the tailnet, or start the CI workflow by hand, which redeploys and then verifies.

## Deploying

CI is the way in. A push to `main` that touches the image's inputs runs the checks, asks Dokploy,
then verifies the result is not public. Nothing is triggered by the push reaching Dokploy: its Auto
Deploy stays off, so the checks are what a deployment waits on.

To deploy without waiting for a push, from any machine on the tailnet:

```sh
deploy/deploy.sh
```

That runs the same checks, then refuses unless `main` is clean and pushed — Dokploy builds the
branch as GitHub has it, not as the working tree has it — and calls the same
`deploy/dokploy-deploy.sh` that CI's deploy job calls, so the two cannot drift apart. Its
configuration comes from an untracked `.env`; see `.env.example`.

## GitHub Actions, once

Repository → Settings → Secrets and variables → Actions → **Secrets** (repository level; the
`production` environment the deploy job names sees them too, and exists only so deployments show
up as such):

| Name | Value |
|---|---|
| `DOKPLOY_API_KEY` | the key generated above |
| `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET` | a Tailscale OAuth client, scope `auth_keys`, tag `tag:ci` |
| `DOKPLOY_URL` | `http://dokploy.example.ts.net:3000` |
| `JOBS_HOSTNAME` | `jobs.example.ts.net` |
| `SERVER_PUBLIC_IP` | `203.0.113.7` |
| `SERVER_PUBLIC_IPV6` | only if the server has one |

`DOKPLOY_URL`, `JOBS_HOSTNAME` and the server's addresses are not credentials, but they are secrets
all the same: GitHub prints a variable in a job's log as it is, and masks a secret. As variables,
every run that deployed wrote the tailnet's name, Dokploy's address, the service's hostname and the
server's public address into a log that anyone who can read the repository can read. The deploy job
masks the Dokploy host and its tailnet on their own as well, since a secret is only masked where its
whole value appears.

Same page → **Variables** (repository level), exactly one:

| Name | Value |
|---|---|
| `DOKPLOY_COMPOSE_ID` | from the compose service's URL in Dokploy |

`DOKPLOY_COMPOSE_ID` stays a variable because the deploy job is skipped while it is unset, and a
job's condition cannot read a secret. It has to be a *repository* variable, too: a job's condition
is evaluated before its environment is attached, so a variable kept in the environment would read
as empty there and the deployment would never start. Printed in a log it gives nothing away: the
id is no use without the API key and a way onto the tailnet.

The tailnet policy scopes the runner to the Dokploy host and nothing else. The host is named by
address rather than by tag: tagging a machine that belongs to a user takes it out of that user's
ownership and out of every rule written for them.

```jsonc
"tagOwners": { "tag:ci": ["autogroup:admin"] },
"hosts": { "dokploy": "100.x.y.z" },
"acls": [
  // ...
  { "action": "accept", "src": ["tag:ci"], "dst": ["dokploy:3000"] }
]
```

That rule only narrows anything if no broader one applies: a policy that still carries the default
`{ "src": ["*"], "dst": ["*:*"] }` lets `tag:ci` reach everything regardless.

Nodes created through that OAuth client are ephemeral: the runner disappears from the tailnet when
the job ends.

### What it costs

Nothing, for this repository: it is public, and a public repository's runs on standard
GitHub-hosted runners are free. A private copy is metered: GitHub's Free plan includes **2,000
minutes a month**; Linux runners bill at 1x and each job is rounded up to the next minute.

| Push | Jobs | Roughly |
|---|---|---|
| a branch | `checks` | 3-5 min |
| `main`, prose or tickets only | `checks`, `image-inputs` | 4-6 min |
| `main`, touching `src/` or the Dockerfile | all four | 8-14 min |

So in a private copy a busy month of daily pushes sits near a tenth of the allowance. The image build dominates the
`checks` job and is cached across runs; the rest is dependency installation.

### What none of it guarantees

- Dokploy builds the head of `main`, not the commit whose checks went green. Two pushes in quick
  succession can ship the second on the strength of the first.
- Only the commits of a push decide whether it deploys. After a deployment that failed, or after
  red checks on a push that touched `src/` were fixed by a push that touched only `tests/`, nothing
  deploys by itself; start the workflow by hand, which always deploys.
- The wait reads `deployments` from Dokploy's `compose.one`, as the API's documentation describes
  it; on a Dokploy of another version, watch the first run.
- `not-public` judges by status: no connection, 403 and 404 are a shut door, anything else is
  exposure, including the 502 of an open route onto an application that is still starting. What it
  cannot see is a door that is shut only for now: a router added while Traefik itself is down, or
  Funnel switched on between two deployments.

## Restoring the store

The volume is not the backup; the clones are (decision 10).

```sh
docker volume rm <the jobs-data volume>    # or a new server with no volume at all
# redeploy from Dokploy
```

The service starts, finds an empty volume and clones `jobs-data` back into it. The store is then
exactly as of the last push. The server pushes every commit it makes, so that is everything,
unless the dashboard was saying **Not pushed** when the volume was lost.

If `docker volume rm` says the volume is in use after the application was stopped, exited task
containers still hold it: `docker ps -a --filter volume=<name>`, `docker rm` those, then remove
the volume.
