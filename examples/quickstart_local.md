# Quickstart: two agents on one workstation

This example coordinates a planner and a reviewer that run in different
runtimes, entirely on the local filesystem, with no network and no
third-party packages.

## Run it

```bash
pip install -e .
bash examples/two_agents_demo.sh
```

The script creates a fresh bridge under a temporary directory, registers
two agents, sends a `review.request`, drives it through the task lifecycle,
prints the derived monitor dashboard, and validates the append-only log.

## What to inspect afterwards

- `events/*.jsonl` in the bridge root holds the canonical, append-only log:
  one line per registration, message, and state transition.
- `monitor.md` is rebuilt from that log and shows active agents, pending
  directed messages, blocked work, and recent events.
- Re-running `agent-bridge validate` re-parses the whole log and reports any
  malformed record.

## Next step: cross-runtime coordination

To connect agents that run in different processes or on different machines,
start the HTTP server instead:

```bash
agent-bridge-server --host 127.0.0.1 --port 8765
```

and hand each agent the bootstrap prompt in `examples/bootstrap_prompt.md`.
