# Deployment

The service runs on a server under Dokploy, reachable over the tailnet only (decisions 9, 10, 31b
and 32). CI runs the checks on every push and deploys a green `main`. Locally it runs either
directly (`DATA_DIR=data .venv/bin/python -m jobs`) or from the image (`compose.yaml`).

What follows calls the server **`dokploy`**, its tailnet address `100.x.y.z`, its public address
`203.0.113.7`, the tailnet `example.ts.net` and the service's hostname `jobs.example.com`. Put your
own in their place.

Everything the repository can hold is in it: the `Dockerfile`, `.github/workflows/ci.yaml`,
`deploy/dokploy-deploy.sh`, `deploy/deploy.sh` and `deploy/verify-not-public.sh`. What follows is
the part that lives outside it, set up once by hand.

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

On `dokploy`, a new project **`jobs`**, holding a single service. There is no database service
to add: files are the database (ADR 0002), and the only state is the git clone in the volume.

1. New **Application**. Provider: GitHub, repository `jobs`, branch `main`, build type
   **Dockerfile**, path `Dockerfile`, context `.`. If the repository is private, the Dokploy GitHub
   App needs access granted to it.
2. General → **Auto Deploy: off**, and no `On Push` trigger. This may differ from other
   applications on the host, and deliberately: if Dokploy listened to the push, the checks and the
   deployment would race and a red run would ship anyway (decision 31b). GitHub Actions triggers
   the deployment instead, after the checks.
3. Environment: `DATA_REPO_URL`, `DATA_REPO_TOKEN` and `CLAUDE_CODE_OAUTH_TOKEN`. Nothing else —
   and nothing employer-facing ever (decisions 24 and 33).
4. Advanced → Volumes: a **volume mount**, name `jobs-data`, mount path `/data`. A named volume,
   not a bind mount: it takes the unprivileged user's ownership from the image on first use.
5. Domains: `jobs.example.com`, container port **8000**, HTTPS on. See the next section for the
   certificate, which is the one part that cannot be left at Dokploy's default.
6. Profile → API keys: generate one for `deploy/deploy.sh` and for the CI deploy job.

The compose id, for `DOKPLOY_COMPOSE_ID`, is the last path segment of the service's URL in Dokploy.
It is a *compose* service -- the application beside its Tailscale sidecar -- so the deploy script
calls `compose.deploy`, not `application.deploy`.

**Before the first deployment:** Dokploy builds the branch as GitHub has it, so `main` has to be
pushed. Nothing is deployable until it is.

## Reachable over the tailnet only

Traefik on `dokploy` listens on the public interface too, so a hostname nobody knows is not a
boundary. Three things make the boundary, and any other private application on the host can share
the same arrangement.

1. **DNS.** `jobs.example.com` gets an A record of `100.x.y.z`, `dokploy`'s *tailnet* address.
   That is a CGNAT address, so the record may sit in public DNS: off the tailnet it leads nowhere.
   It must be **DNS-only, not proxied** — a proxy cannot reach a CGNAT address at all, and if it
   could, Traefik would see the proxy's address instead of the caller's and the allow-list below
   would be judging the wrong thing.
2. **Certificate by DNS-01.** Dokploy's default resolver answers over HTTP, and Let's Encrypt
   cannot reach a tailnet address to ask. The DNS challenge proves the name by a TXT record
   instead, which works regardless of where the name points. Dokploy's **DNS Providers** section
   configures this; if a resolver is already there from another application, reuse it rather than
   adding a second. One way to add it: a second resolver, `letsencrypt-dns`, in `traefik.yml`
   beside the HTTP one, with `CF_DNS_API_TOKEN` in Traefik's environment, a Cloudflare token
   limited to the one zone, with DNS:Edit and Zone:Read.
3. **An allow-list on the router**, for anyone who presents the hostname to the public address
   directly.

For an Application, Dokploy generates the router into
`/etc/dokploy/traefik/dynamic/<service>.yml`, in this shape:

```yaml
http:
  routers:
    <service>-router-1:
      rule: Host(`<hostname>`)
      middlewares: [redirect-to-https]
      entryPoints: [web]
    <service>-router-websecure-1:
      rule: Host(`<hostname>`)
      middlewares: []          # <- the allow-list goes here
      entryPoints: [websecure]
      tls:
        certResolver: letsencrypt   # <- the DNS-01 resolver goes here
```

So in the application's **Advanced → Traefik configuration**, point `certResolver` at the DNS-01
resolver and attach the middleware to both routers:

```yaml
http:
  middlewares:
    tailnet-only:
      ipAllowList:
        sourceRange:
          - 100.64.0.0/10
          - fd7a:115c:a1e0::/48
```

`ipAllowList` is the Traefik v3 name. Under v2 it was `ipWhiteList`.

**Put those edits in Dokploy's Advanced → Traefik configuration, never in the file on disk.**
Dokploy regenerates `/etc/dokploy/traefik/dynamic/<service>.yml` on every deployment, so a hand
edit there survives until the next deploy and then silently disappears — taking the allow-list with
it and leaving an unauthenticated dashboard on a public port.

That configuration lives in Dokploy, not in this repository, which is the one part of the
deployment git does not carry. `deploy/verify-not-public.sh` exists because of that: it is how the
result gets checked rather than assumed, after every deployment.

### Why the allow-list is load-bearing

Traefik on `dokploy` listens on the public interface, and routes by `Host` header alone.
Asked at its public address, `203.0.113.7`:

```
unknown Host                 -> 404
a Host it has a router for   -> 200
```

So DNS pointing at a tailnet address hides the dashboard from anyone who does not know the name; it
does not hide it from anyone who does. Without the allow-list, `curl --resolve
jobs.example.com:443:203.0.113.7` reaches the app from anywhere on the internet. There is
no login behind it to stop them (decision 32).

Stronger, if the shared configuration is worth touching further: give Traefik an entry point bound
to the tailnet address only (`address: 100.x.y.z:443`) and put this router on that entry point
instead of `websecure`. Then the public interface has no route to the app at all, rather than a
route that is filtered.

Then verify, from a machine that is **not** on the tailnet:

```sh
JOBS_HOSTNAME=jobs.example.com SERVER_PUBLIC_IP=203.0.113.7 deploy/verify-not-public.sh
```

CI's `not-public` job runs the same script from a GitHub runner after every deployment. After a
change to the routing, the certificate or the tailnet policy, either run the script by hand from
off the tailnet, or start the CI workflow by hand, which redeploys and then verifies.

## When every public site on the server stops answering

Worth two minutes rather than an hour, if the server's firewall is managed with `ufw-docker`.
Saving Traefik's environment in Dokploy recreates the `dokploy-traefik` container. Firewall rules
written as `ufw-docker allow dokploy-traefik 80` are pinned to the container's internal address,
the recreated container gets a new one, and public traffic to 80 and 443 is then dropped:
connections time out, while the same sites still answer over the tailnet address, because traffic
arriving on `tailscale0` is accepted wholesale. A reboot can move the address just as well.

`ufw-docker` does not repair it by itself. It writes rules for the networks `docker inspect` lists,
but a container attached only to `dokploy-network` has its published ports forwarded through
`docker_gwbridge`, which is not among them.

The fix is at the source, on the server and outside this repository: name no container in the
firewall rules. Write, once:

```
ufw route allow proto tcp from any to any port 80
ufw route allow proto tcp from any to any port 443
```

Only one container can publish a given host port, so "allow forwarded traffic to port 80" is
"allow whatever serves port 80" — Traefik by construction rather than by configuration. The rule
carries no address, so no recreation or reboot can invalidate it.

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
| `JOBS_HOSTNAME` | `jobs.example.com` |
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

For a private repository Actions minutes are metered. GitHub's Free plan includes **2,000 minutes
a month**; Linux runners bill at 1x and each job is rounded up to the next minute.

| Push | Jobs | Roughly |
|---|---|---|
| a branch | `checks` | 3-5 min |
| `main`, prose or tickets only | `checks`, `image-inputs` | 4-6 min |
| `main`, touching `src/` or the Dockerfile | all four | 8-14 min |

So a busy month of daily pushes sits near a tenth of the allowance. The image build dominates the
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
  cannot see is a route that is open while Traefik itself is down.

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
