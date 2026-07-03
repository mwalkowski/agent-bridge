"""Agent Bridge: a runtime-agnostic, auditable message bus for LLM agents.

The package exposes two entry points:

* ``agent_bridge.core``   -- the local JSONL mailbox, event log and CLI.
* ``agent_bridge.server`` -- a dependency-free HTTP API over the same core.
"""

__version__ = "1.0.0"
