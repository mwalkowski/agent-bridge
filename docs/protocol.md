# Agent Bridge Protocol

Agent Bridge is a local JSONL mailbox for monitored communication between arbitrary agent instances in one workspace. The same core is exposed as an HTTP service (see [http-instructions.md](http-instructions.md)).

## Storage

```text
.agent-bridge/
  protocol.json
  agents/*.json
  inbox/*.jsonl
  outbox/*.jsonl
  events/YYYY-MM-DD.jsonl
  monitor.md
```

`events/YYYY-MM-DD.jsonl` is canonical. `inbox`, `outbox`, the agent registry, and `monitor.md` are derived views rebuilt by replaying the log. If a view and the event log disagree, the event log wins.

## Identity

Peers are registered by `agent_id`, not by product name. `runtime` is metadata only.

```json
{
  "schema": "agent-bridge.agent.v1",
  "agent_id": "claude.planner",
  "runtime": "claude-code",
  "role": "planner",
  "display_name": "Planning Agent",
  "capabilities": ["plan", "review"],
  "status": "active"
}
```

This supports same-runtime communication (for example `claude.planner` to `claude.reviewer`), mixed-runtime communication, and custom local agents.

## Message types

`notice`, `question`, `answer`, `task.request`, `task.result`, `review.request`, `review.result`, `handoff`, `status`, `error`.

## Message lifecycle

`queued -> acknowledged -> in_progress -> completed | blocked | failed`, plus `expired` for items that time out. Each transition (`ack`, `start`, `complete`, `block`, `fail`) is recorded as a separate append-only event, so ownership and blocking relationships are always reconstructable. Transitions are validated: an illegal transition, or one attempted by an agent other than the message recipient, is rejected.

## Safety rules

- Routing targets `agent_id`, never `runtime`.
- Messages and events are append-only.
- State transitions are separate events.
- A message cannot authorize shell execution by itself.
- Secrets must not be placed in message bodies.
- If `monitor.md` and the event log disagree, the event log wins.

## Command-line usage

```bash
agent-bridge init
agent-bridge register --agent-id claude.planner --runtime claude-code --role planner
agent-bridge register --agent-id codex.reviewer --runtime codex --role reviewer
agent-bridge send --from claude.planner --to codex.reviewer \
  --type review.request --subject "Review plan" --body "Check the state machine."
agent-bridge inbox --agent-id codex.reviewer
agent-bridge monitor
```

## Commands

- `init`: create the bridge layout and protocol metadata.
- `register`: create or refresh an agent identity.
- `agents`: list registered agents and heartbeats.
- `send`: send a directed message (or to a channel with `--to channel:{name}`).
- `broadcast`: send a notice to all agents.
- `inbox`: list pending messages for one agent.
- `ack`, `start`, `complete`, `block`, `fail`: advance a message state (validated against the state machine and message ownership).
- `expire`: mark non-terminal messages past their `expires_at` as expired.
- `heartbeat`: publish current activity.
- `handoff`: send a handoff message with optional artifacts.
- `monitor`: rebuild and print the human-readable dashboard.
- `validate`: parse logs and fail on malformed JSONL.
