#!/usr/bin/env bash
# Asks for the dashboard the way a stranger would and fails if it answers. Run it from a machine
# that is NOT on the tailnet: the last job of the CI workflow, or a phone on mobile data with
# Tailscale switched off. From inside the tailnet it proves nothing.
#
#   JOBS_HOSTNAME=jobs.example.ts.net SERVER_PUBLIC_IP=203.0.113.7 deploy/verify-not-public.sh
#
# What changed, and why this script inverted. The dashboard used to be served under a public
# hostname whose A record held a tailnet address, fronted by Traefik and kept private by an
# ipAllowList middleware. Now it is served by a Tailscale sidecar under a MagicDNS name, with no
# Traefik router and no published port, so there is no route to filter.
#
# The old precondition was that the hostname resolves to a tailnet address, because a mistyped
# hostname shuts every door and would have proved nothing. A MagicDNS name does not resolve at all
# off the tailnet, so that precondition now fails on exactly the machine the test must run from.
#
# It becomes its own assertion instead. From outside the tailnet the name must NOT resolve:
#   - it resolves to a tailnet address  -> this machine is on the tailnet; the test proves nothing
#   - it resolves to a public address   -> something publishes it, Funnel being the likely cause
#   - it does not resolve               -> as intended; carry on and try the doors anyway
#
# The doors tried: the server public address on 443 and 80 with the hostname presented, which is
# how a router listening on every interface is reached by someone who knows the name; and the
# application port, in case it was ever published. Set SERVER_PUBLIC_IPV6 if the server has one,
# and JOBS_RETIRED_HOSTNAME to also check a public name the dashboard used to answer on -- with a
# wildcard DNS record in play that name still reaches the server, so it is worth asserting that
# nothing there answers for it.
set -euo pipefail

: "${JOBS_HOSTNAME:?the hostname the dashboard is served under}"
: "${SERVER_PUBLIC_IP:?the public address of the server}"

resolved=$(getent ahosts "$JOBS_HOSTNAME" | awk '{ print $1 }' | sort -u || true)
for address in $resolved; do
  case "$address" in
    100.6[4-9].* | 100.[7-9][0-9].* | 100.1[01][0-9].* | 100.12[0-7].* | fd7a:115c:a1e0:*)
      echo "$JOBS_HOSTNAME resolves to the tailnet address $address, so this machine is on the" >&2
      echo "tailnet and the test would prove nothing. Run it from somewhere else." >&2
      exit 1
      ;;
    *)
      echo "$JOBS_HOSTNAME resolves publicly to $address. A MagicDNS name should not resolve off" >&2
      echo "the tailnet; something is publishing it. Check whether Funnel is enabled." >&2
      exit 1
      ;;
  esac
done

echo "shut (no DNS)  $JOBS_HOSTNAME does not resolve from here"

exposed=0

answers() {
  local label=$1
  shift
  local status
  status=$(curl --silent --insecure --max-time 10 --output /dev/null --write-out '%{http_code}' "$@" || true)
  # /health is asked for because the application never answers it with a 404, so a 404 can only
  # be the router's.
  # A shut door is no connection at all (000), the allow-list refusing (403), or a router that
  # does not know the name (404). Anything else came through the route: the application's own
  # answer, healthy or not, or the 502 of a route that is open onto an application still starting.
  case "$status" in
    000 | 403 | 404) echo "shut ($status)     $label" ;;
    *)
      echo "EXPOSED ($status)  $label"
      exposed=1
      ;;
  esac
}

# No "as resolved" probe any more: the name does not resolve, which the check above established.
answers "https://$JOBS_HOSTNAME at $SERVER_PUBLIC_IP" \
  --resolve "$JOBS_HOSTNAME:443:$SERVER_PUBLIC_IP" "https://$JOBS_HOSTNAME/health"
answers "http://$JOBS_HOSTNAME at $SERVER_PUBLIC_IP" --location \
  --resolve "$JOBS_HOSTNAME:80:$SERVER_PUBLIC_IP" --resolve "$JOBS_HOSTNAME:443:$SERVER_PUBLIC_IP" \
  "http://$JOBS_HOSTNAME/health"
answers "http://$SERVER_PUBLIC_IP:8000" "http://$SERVER_PUBLIC_IP:8000/health"

if [ -n "${SERVER_PUBLIC_IPV6:-}" ]; then
  answers "https://$JOBS_HOSTNAME at $SERVER_PUBLIC_IPV6" \
    --resolve "$JOBS_HOSTNAME:443:[$SERVER_PUBLIC_IPV6]" "https://$JOBS_HOSTNAME/health"
  answers "http://[$SERVER_PUBLIC_IPV6]:8000" "http://[$SERVER_PUBLIC_IPV6]:8000/health"
fi

# A name the dashboard used to answer on. The wildcard record means it still reaches this server,
# so the question is whether anything there answers for it -- a stale router would.
if [ -n "${JOBS_RETIRED_HOSTNAME:-}" ]; then
  answers "https://$JOBS_RETIRED_HOSTNAME (retired name)" "https://$JOBS_RETIRED_HOSTNAME/health"
fi

if [ "$exposed" -ne 0 ]; then
  echo "The dashboard is reachable from the public internet." >&2
  exit 1
fi
