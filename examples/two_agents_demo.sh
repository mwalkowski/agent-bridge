#!/usr/bin/env bash
# Minimal, self-contained Agent Bridge demo: a planner and a reviewer
# coordinate a review through the full task lifecycle on the local filesystem.
#
# Requires: agent-bridge installed (pip install -e .). No network needed.
set -euo pipefail

ROOT="$(mktemp -d)/agent-bridge"
run() { agent-bridge --root "$ROOT" "$@"; }

echo "### init"
run init

echo "### register two agents in different runtimes"
run register --agent-id claude.planner --runtime claude-code --role planner --capabilities plan,review >/dev/null
run register --agent-id codex.reviewer --runtime codex --role reviewer --capabilities review >/dev/null
run heartbeat --agent-id claude.planner --task "drafting protocol design" >/dev/null

echo "### send a review request"
MID="$(run send --from claude.planner --to codex.reviewer \
  --type review.request --subject "Review message state machine" \
  --body "Please review the ack/start/complete lifecycle.")"
echo "message id: $MID"

echo "### reviewer inbox"
run inbox --agent-id codex.reviewer

echo "### advance the task lifecycle"
run ack      --agent-id codex.reviewer "$MID" >/dev/null
run start    --agent-id codex.reviewer "$MID" >/dev/null
run complete --agent-id codex.reviewer --artifact note:lifecycle-looks-correct "$MID" >/dev/null

echo "### monitor dashboard (rebuilt from the event log)"
run monitor

echo "### validate the append-only log"
run validate

echo
echo "bridge root: $ROOT"
