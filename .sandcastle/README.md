# Sandcastle

Runs [Sandcastle](https://www.aihero.dev/sandcastle) agents that work through this repository's
`ready-for-agent` tickets. This folder holds the repository's only Node, kept here and nowhere else;
nothing outside it reads it, and the app's image leaves it out.

## Once

From the repository root:

1. `pnpm --dir .sandcastle install`
2. `pnpm --dir .sandcastle build-image`. Run it again after any change to the `Dockerfile`.
3. `cp .sandcastle/.env.example .sandcastle/.env` and fill it in: a Claude token from
   `claude setup-token`, and a fine-grained GitHub token for this repository only, with Issues read
   and write.

## Each run

```
pnpm --dir .sandcastle sandcastle
```

The agent takes the `ready-for-agent` tickets whose blockers are closed, or already done on the
branch, one per iteration, up to six. It commits each one to `sandcastle/work` with `Closes #N`,
comments on the ticket, and never pushes or closes anything.

Then review and ship:

```
git log --stat main..sandcastle/work
git merge --ff-only sandcastle/work
git push      # closes the tickets and deploys
```

The next run notices the branch is on `main` and starts a fresh one. Logs are in
`.sandcastle/logs/`: each agent's own, and the runner's in `run.log`, appended to on every run.
Follow a run started in the background with `tail -F .sandcastle/logs/run.log`. Never redirect
its output to a shared path such as `/tmp/sandcastle-run.log`: other repositories' runners pick
the same name and overwrite it.

## Files

- `main.ts`: the run. Branch, model, iterations, mounts, and the sandbox's setup (virtualenv and
  Chromium).
- `branch-check.ts`: refuses to start on a branch that holds nothing beyond `main` but cannot be
  removed.
- `prompt.md`: the agent's instructions.
- `scripts/ready.sh`: which tickets are ready, run inside the sandbox at every iteration.
- `Dockerfile`: Python 3.14, git, gh, jq, Chromium's libraries and Claude Code.

Playwright's browsers for the sandbox are kept in `~/.cache/ms-playwright-sandcastle`, never in the
host's `~/.cache/ms-playwright`. Installing into a shared cache deletes the browsers of every host
project the sandbox cannot see.
