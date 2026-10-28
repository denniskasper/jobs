#!/usr/bin/env bash
# Prints the tickets of this repository that are ready to work on now, as JSON, one per line.
#
# Ready means: open, labelled ready-for-agent, not a spec (spec titles start with "Spec:"), not
# already done on this branch, and every blocker either closed or done on this branch. "Done on
# this branch" means a recent commit says "Closes #N". Such a ticket stays open until a person
# pushes `main`, and without this rule everything after the first ticket would wait for that.
set -euo pipefail

repo=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
done_here=" $(git log --format=%B -100 | grep -oiE 'closes #[0-9]+' | grep -oE '[0-9]+' | sort -u | tr '\n' ' ') "

gh api "repos/$repo/issues?state=open&labels=ready-for-agent&per_page=100" \
  --jq '.[] | select(.title | startswith("Spec:") | not) | .number' | sort -n |
  while read -r n; do
    case "$done_here" in *" $n "*) continue ;; esac
    waiting=$(gh api "repos/$repo/issues/$n/dependencies/blocked_by" \
      --jq '.[] | select(.state == "open") | .number' |
      while read -r b; do
        case "$done_here" in *" $b "*) ;; *) echo "$b" ;; esac
      done)
    if [ -z "$waiting" ]; then
      gh issue view "$n" -R "$repo" --json number,title,body,comments \
        --jq '{number, title, body, comments: [.comments[].body]}' | tr -d '\n'
      echo
    fi
  done
