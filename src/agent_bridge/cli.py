"""Console entry point for the Agent Bridge command-line interface."""

from __future__ import annotations

from agent_bridge.core import main


def run() -> None:
    """Run the CLI and exit with its status code."""
    raise SystemExit(main())


if __name__ == "__main__":
    run()
