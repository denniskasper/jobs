#!/usr/bin/env bash
# Asks Dokploy to deploy, and waits for the build to finish. The one implementation of that, shared
# by the CI deploy job and by deploy/deploy.sh, so the two cannot drift apart.
#
# Needs DOKPLOY_URL, DOKPLOY_COMPOSE_ID and DOKPLOY_API_KEY in the environment, or in an
# untracked .env at the repo root; see .env.example. The key is never an argument, so it stays out
# of the process list. Reaching DOKPLOY_URL needs the tailnet: Dokploy has no public address.
#
# Compose, not Application. The service is a Dokploy *compose* stack -- the app plus its Tailscale
# sidecar -- so the endpoints are compose.one and compose.deploy, keyed by composeId. The
# application.* endpoints this used to call answer 404 for a compose service, which reads as a
# broken URL rather than the wrong kind of thing being asked for.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

: "${DOKPLOY_URL:?the Dokploy host, e.g. http://dokploy.example.ts.net:3000}"
: "${DOKPLOY_COMPOSE_ID:?the compose id, the last path segment of the service URL in Dokploy}"
: "${DOKPLOY_API_KEY:?a Dokploy API key, from Profile then API keys}"

# Fails the step visibly on a runner, and reads as a plain message anywhere else.
fail() {
  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    echo "::error::$1"
  else
    echo "$1" >&2
  fi
  exit 1
}

newest_deployment() {
  curl --fail --silent --show-error --max-time 30 --get \
    "$DOKPLOY_URL/api/compose.one" \
    --data-urlencode "composeId=$DOKPLOY_COMPOSE_ID" \
    --header "x-api-key: $DOKPLOY_API_KEY" \
    | jq --raw-output '.deployments // [] | sort_by(.createdAt) | last
                       | "\(.deploymentId // "none") \(.status // "none")"'
}

# Dokploy only queues the deployment, and until the queue reaches it the service still carries
# the status of the one before. So the newest is noted first and the wait is for a newer one.
# `read` fails on empty input, which is what a curl that failed leaves behind; under `set -e` that
# would end the script, so every read says what it falls back to.
read -r before _ < <(newest_deployment) || before=unknown

curl --fail-with-body --silent --show-error --max-time 60 \
  --request POST "$DOKPLOY_URL/api/compose.deploy" \
  --header "x-api-key: $DOKPLOY_API_KEY" \
  --header "Content-Type: application/json" \
  --data "{\"composeId\": \"$DOKPLOY_COMPOSE_ID\"}"

for attempt in $(seq 1 60); do
  sleep 10
  # Dokploy is busy building exactly now, so one request that times out is not a failed deployment.
  read -r id status < <(newest_deployment) || { echo "attempt $attempt: Dokploy did not answer"; continue; }
  printf 'attempt %s: deployment %s is %s\n' "$attempt" "$id" "$status"
  [ "$id" = "$before" ] && continue
  case "$status" in
    done) echo "deployed"; exit 0 ;;
    error) fail "Dokploy reports the deployment failed" ;;
  esac
done

fail "the deployment did not finish within ten minutes"
