# Agent Bridge

A runtime-agnostic, auditable message bus for coordinating heterogeneous Large Language Model (LLM) agents.

Agent Bridge lets arbitrary agent instances that run in different runtimes, in separate processes, or on separate machines (for example Claude Code, OpenAI Codex, or a custom agent) coordinate through named channels and directed inboxes. Every message and state transition is written to an append-only JSON Lines event log that is the single source of truth, so a whole multi-agent run is durable, auditable, and replayable. A human-readable dashboard is derived from that log.

The core has **no third-party dependencies**: it is a Python standard-library implementation exposed both as a command-line tool and as an HTTP service.

## Why

Most multi-agent frameworks orchestrate agents inside one process and one runtime, and keep no durable record of the conversation. When a human supervises a fleet of agents that live in different runtimes or on different machines, the missing piece is a shared, observable place for those agents to talk and to hand work off. Agent Bridge is that piece:

- **Runtime-agnostic.** Peers are addressed by a stable `agent_id`, never by runtime, so a Claude Code planner and a Codex reviewer talk the same way.
- **Auditable.** The append-only event log is canonical; inboxes, the registry, and the dashboard are derived from it. If a view and the log disagree, the log wins.
- **Typed and stateful.** Ten message types and an explicit task lifecycle (`queued -> acknowledged -> in_progress -> completed | blocked | failed | expired`).
- **Human-in-the-loop.** Agents can hand work to a human and back through the same protocol.
- **Small and safe.** No database, no external services, and a protocol where a message can never, by itself, authorize execution.

## Features

- Channels for many-to-many discovery and topic work, plus point-to-point directed inboxes.
- Ten message types: `notice`, `question`, `answer`, `task.request`, `task.result`, `review.request`, `review.result`, `handoff`, `status`, `error`.
- Task lifecycle with acknowledge, start, complete, block, and fail transitions.
- Enforced state machine: illegal transitions, and transitions by any agent other than the recipient, are rejected.
- Optional expiry sweep (`expire`) that marks overdue messages past their `expires_at`.
- Heartbeats and a rebuilt Markdown monitor dashboard.
- `validate` command that parses the whole log and fails on malformed records.
- Command-line interface and a dependency-free HTTP API over the same core.

## Install

From source (editable):

```bash
git clone https://github.com/mwalkowski/agent-bridge.git
cd agent-bridge
pip install -e .
```

This installs two commands: `agent-bridge` (CLI) and `agent-bridge-server` (HTTP API). Python 3.9 or newer is required; there are no other runtime dependencies.

## Quickstart (CLI)

```bash
agent-bridge init
agent-bridge register --agent-id claude.planner --runtime claude-code --role planner
agent-bridge register --agent-id codex.reviewer --runtime codex --role reviewer

MID=$(agent-bridge send --from claude.planner --to codex.reviewer \
        --type review.request --subject "Review state machine" \
        --body "Please review the ack/start/complete lifecycle.")

agent-bridge ack      --agent-id codex.reviewer "$MID"
agent-bridge start    --agent-id codex.reviewer "$MID"
agent-bridge complete --agent-id codex.reviewer --artifact note:looks-correct "$MID"

agent-bridge monitor    # rebuilds monitor.md from the event log
agent-bridge validate
```

A scripted version of this walkthrough is in [`examples/two_agents_demo.sh`](examples/two_agents_demo.sh).

## HTTP server

Start the API on a trusted network:

```bash
agent-bridge-server --host 127.0.0.1 --port 8765 --root .agent-bridge
```

Give each agent the bootstrap endpoint `http://127.0.0.1:8765/instructions`. Agents then join the `general` channel, discover where work is happening, and move into a topic channel:

```bash
curl -sS -X POST http://127.0.0.1:8765/general/join \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"agent.alpha","runtime":"codex","role":"researcher","task":"standing by"}'
```

See [`docs/http-instructions.md`](docs/http-instructions.md) for the full endpoint list and [`docs/protocol.md`](docs/protocol.md) for the protocol.

## Docker

Build and run the HTTP server in a container:

```bash
docker build -t agent-bridge:1.0.0 .
docker run --rm -p 127.0.0.1:8765:8765 -v agent-bridge-data:/data agent-bridge:1.0.0
```

Or with Docker Compose:

```bash
docker compose up --build
```

The image runs as a non-root user and keeps the append-only log on the `/data` volume. The port is published on `127.0.0.1` by default because the API is unauthenticated; expose it more widely only on a trusted, isolated network (see Safety model).

## Protocol overview

- **Identity.** Agents register with an `agent_id` (matching `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}`), a declared `runtime`, and a `role`.
- **Discovery.** Agents announce on the `general` channel, then create or join topic channels.
- **Messaging.** Directed messages go to an agent inbox; channel messages use `to=channel:{name}`.
- **Lifecycle.** Each work item advances through the state machine above; every transition is a separate event.
- **Storage.** `events/YYYY-MM-DD.jsonl` is canonical; `inbox/`, `outbox/`, `agents/`, and `monitor.md` are derived views.

## Safety model

- Routing targets `agent_id`, never `runtime`.
- Messages and events are append-only; state transitions are separate events.
- A message cannot, by itself, authorize shell execution.
- Secrets must not be placed in message bodies; treat all channel content as team-visible.
- The HTTP API has no authentication by design. Run it on localhost or an isolated, trusted network only.

## Project layout

```
agent-bridge/
├── src/agent_bridge/
│   ├── core.py         # mailbox, event log, state machine, CLI
│   ├── server.py       # dependency-free HTTP API over the core
│   ├── cli.py          # console entry point
│   └── protocol.json   # machine-readable protocol summary
├── docs/               # protocol and HTTP instructions
├── examples/           # quickstart, demo script, bootstrap prompt, figure-replay
├── tests/              # pytest suite
├── Dockerfile
└── docker-compose.yml
```

## Reproducing the paper figures

The raw exercise logs are confidential, but the reported aggregate statistics
are reproducible. A generator writes a synthetic, anonymized log whose
per-channel and per-type message counts match the paper, and an aggregation
script recomputes them from that append-only log:

```bash
python3 examples/replay/generate_ocf26_log.py --root ./ocf26-replay
python3 examples/replay/aggregate_stats.py    --root ./ocf26-replay
```

See [`examples/replay/README.md`](examples/replay/README.md). The exact counts
are asserted by the test suite.

## Testing

```bash
pip install -e ".[test]"
pytest
```

## License

Apache License 2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
