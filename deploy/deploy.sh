#!/usr/bin/env bash
# Deploys by hand, from a machine that is already on the tailnet. CI is the usual way in
# (.github/workflows/ci.yaml deploys a green main); this is the same deployment without waiting for
# a push, and it runs the checks and the guards that CI gets for free.
#
#   deploy/deploy.sh
set -euo pipefail

cd "$(dirname "$0")/.."

say() { printf '\n== %s\n' "$1"; }

say "Checks"
# The gate the deployment stands behind (decision 31b). Locally these cost seconds.
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest -q

say "What Dokploy would build"
# Dokploy builds the branch as GitHub has it, not as this working tree has it. So a deployment from
# a dirty or unpushed tree would ship something other than what the checks just passed. On a runner
# this cannot happen, which is why the guard lives here and not in the shared script.
branch=$(git rev-parse --abbrev-ref HEAD)
if [ "$branch" != "main" ]; then
  echo "on $branch, and Dokploy builds main" >&2
  exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
  echo "the working tree has changes that Dokploy would not build" >&2
  exit 1
fi
git fetch --quiet origin main
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  echo "HEAD and origin/main differ; push first, or Dokploy builds something else" >&2
  exit 1
fi
echo "main is clean and pushed at $(git rev-parse --short HEAD)"

say "Deploying"
deploy/dokploy-deploy.sh
