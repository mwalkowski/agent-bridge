#!/usr/bin/env python3
"""Local JSONL mailbox for communication between arbitrary agent instances."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any


MESSAGE_TYPES = {
    "notice",
    "question",
    "answer",
    "task.request",
    "task.result",
    "review.request",
    "review.result",
    "handoff",
    "status",
    "error",
}

TERMINAL_STATES = {"completed", "failed", "expired"}
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

# Serializes the read-modify-write critical sections (sequence assignment and
# log append) so that, within one process, appends are atomic and the physical
# log order is the canonical causal order. Reentrant so higher-level operations
# can hold it across create_message() and the resulting append_event().
LOG_LOCK = threading.RLock()

# State a message enters after each transition event.
EVENT_STATE = {
    "message.ack": "acknowledged",
    "message.started": "in_progress",
    "message.completed": "completed",
    "message.blocked": "blocked",
    "message.failed": "failed",
    "message.expired": "expired",
}

# States from which each transition is legal (the state machine of the paper).
ALLOWED_FROM = {
    "message.ack": {"queued"},
    "message.started": {"queued", "acknowledged", "blocked"},
    "message.completed": {"in_progress"},
    "message.blocked": {"in_progress"},
    "message.failed": {"acknowledged", "in_progress", "blocked"},
    "message.expired": {"queued", "acknowledged", "in_progress", "blocked"},
}

# Transitions that only the message recipient may perform (expiry is a
# system/operator action and is exempt).
OWNED_TRANSITIONS = {
    "message.ack",
    "message.started",
    "message.completed",
    "message.blocked",
    "message.failed",
}


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def die(message: str, code: int = 2) -> None:
    print(f"agent-bridge: {message}", file=sys.stderr)
    raise SystemExit(code)


def validate_agent_id(agent_id: str) -> None:
    if not SAFE_ID.match(agent_id):
        die("agent id must match [A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def filename_for(agent_id: str) -> str:
    validate_agent_id(agent_id)
    return f"{agent_id}.jsonl"


def json_filename_for(agent_id: str) -> str:
    validate_agent_id(agent_id)
    return f"{agent_id}.json"


def bridge_root(args: argparse.Namespace) -> Path:
    return Path(args.root).resolve()


def ensure_layout(root: Path) -> None:
    for name in ["agents", "inbox", "outbox", "events", "locks"]:
        (root / name).mkdir(parents=True, exist_ok=True)


def protocol_path(root: Path) -> Path:
    return root / "protocol.json"


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    if not path.exists():
        return records, errors
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            raw = line.strip()
            if not raw:
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append(f"{path}:{line_no}: malformed JSONL: {exc.msg}")
                continue
            if not isinstance(value, dict):
                errors.append(f"{path}:{line_no}: record is not an object")
                continue
            records.append(value)
    return records, errors


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        die(f"{path} must contain a JSON object")
    return value


def all_events(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in sorted((root / "events").glob("*.jsonl")):
        records, path_errors = read_jsonl(path)
        events.extend(records)
        errors.extend(path_errors)
    # Events are returned in physical append order: files are ordered by their
    # date name and lines are kept in write order. Because appends are
    # serialized by LOG_LOCK (see append_event), this order is the canonical
    # causal order, so replaying it reconstructs the correct derived state even
    # for events that share the same whole-second timestamp.
    return events, errors


def event_id(agent_id: str, event_type: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_type = event_type.replace(".", "_")
    return f"evt_{stamp}_{agent_id}_{safe_type}_{os.getpid()}"


def next_seq(root: Path, agent_id: str) -> int:
    seq = 0
    events, _ = all_events(root)
    for event in events:
        message = event.get("message")
        if isinstance(message, dict) and message.get("from") == agent_id:
            seq = max(seq, int(message.get("seq", 0) or 0))
    return seq + 1


def append_event(root: Path, agent_id: str, event_type: str, **payload: Any) -> dict[str, Any]:
    validate_agent_id(agent_id)
    with LOG_LOCK:
        event = {
            "schema": "agent-bridge.event.v1",
            "event_id": event_id(agent_id, event_type),
            "type": event_type,
            "agent_id": agent_id,
            "created_at": utcnow(),
            **payload,
        }
        append_jsonl(root / "events" / f"{today()}.jsonl", event)
    return event


def agent_exists(root: Path, agent_id: str) -> bool:
    return (root / "agents" / json_filename_for(agent_id)).exists()


def require_agent(root: Path, agent_id: str) -> None:
    if not agent_exists(root, agent_id):
        die(f"unknown agent_id: {agent_id}; run register first")


def load_agents(root: Path) -> dict[str, dict[str, Any]]:
    agents: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "agents").glob("*.json")):
        record = read_json(path)
        if record and isinstance(record.get("agent_id"), str):
            agents[record["agent_id"]] = record
    return agents


def load_state(root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    agents = load_agents(root)
    messages: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    events, event_errors = all_events(root)
    errors.extend(event_errors)
    for event in events:
        event_type = event.get("type")
        if event_type == "agent.registered":
            agent = event.get("agent")
            if isinstance(agent, dict) and isinstance(agent.get("agent_id"), str):
                agents[agent["agent_id"]] = {**agents.get(agent["agent_id"], {}), **agent}
        elif event_type == "agent.retired":
            target = event.get("target_agent_id")
            if isinstance(target, str) and target in agents:
                agents[target]["status"] = "retired"
        elif event_type == "heartbeat":
            agent_id = event.get("agent_id")
            if isinstance(agent_id, str):
                agents.setdefault(agent_id, {"agent_id": agent_id})["last_heartbeat"] = event.get("created_at")
                agents[agent_id]["current_task"] = event.get("task", "")
                agents[agent_id]["status"] = "active"
        elif event_type == "message.sent":
            message = event.get("message")
            if isinstance(message, dict) and isinstance(message.get("id"), str):
                messages[message["id"]] = {**message, "state": "queued", "state_at": event.get("created_at")}
        elif isinstance(event_type, str) and event_type.startswith("message."):
            message_id = event.get("message_id")
            if isinstance(message_id, str) and message_id in messages:
                state = {
                    "message.ack": "acknowledged",
                    "message.started": "in_progress",
                    "message.blocked": "blocked",
                    "message.completed": "completed",
                    "message.failed": "failed",
                    "message.expired": "expired",
                }.get(event_type)
                if state:
                    messages[message_id]["state"] = state
                    messages[message_id]["state_at"] = event.get("created_at")
                    if event.get("reason"):
                        messages[message_id]["reason"] = event.get("reason")
                    if event.get("artifacts"):
                        messages[message_id]["result_artifacts"] = event.get("artifacts")
    return agents, messages, errors


def parse_artifacts(values: list[str] | None) -> list[dict[str, str]]:
    artifacts = []
    for value in values or []:
        if ":" in value:
            kind, path = value.split(":", 1)
        else:
            kind, path = "file", value
        artifacts.append({"kind": kind, "path": path})
    return artifacts


def command_init(args: argparse.Namespace) -> None:
    root = bridge_root(args)
    ensure_layout(root)
    if not protocol_path(root).exists() or args.force:
        write_json_atomic(
            protocol_path(root),
            {
                "schema": "agent-bridge.protocol.v1",
                "created_at": utcnow(),
                "transport": "local-jsonl",
                "routing": "agent_id",
                "canonical_log": "events/YYYY-MM-DD.jsonl",
                "forbidden": ["secret transport", "implicit command execution"],
            },
        )
    print(root)


def command_register(args: argparse.Namespace) -> None:
    root = bridge_root(args)
    ensure_layout(root)
    validate_agent_id(args.agent_id)
    capabilities = [item.strip() for item in (args.capabilities or "").split(",") if item.strip()]
    agent = {
        "schema": "agent-bridge.agent.v1",
        "agent_id": args.agent_id,
        "runtime": args.runtime,
        "role": args.role,
        "display_name": args.display_name or args.agent_id,
        "capabilities": capabilities,
        "registered_at": utcnow(),
        "status": "active",
    }
    write_json_atomic(root / "agents" / json_filename_for(args.agent_id), agent)
    append_event(root, args.agent_id, "agent.registered", agent=agent)
    print(json.dumps(agent, indent=2, sort_keys=True))


def command_agents(args: argparse.Namespace) -> None:
    root = bridge_root(args)
    agents, _, errors = load_state(root)
    for error in errors:
        print(error, file=sys.stderr)
    if args.json:
        print(json.dumps(list(agents.values()), indent=2, sort_keys=True))
        return
    print("| Agent | Runtime | Role | Status | Last heartbeat | Current task |")
    print("|---|---|---|---|---|---|")
    for agent_id, agent in sorted(agents.items()):
        print(
            "| {agent} | {runtime} | {role} | {status} | {heartbeat} | {task} |".format(
                agent=agent_id,
                runtime=agent.get("runtime", ""),
                role=agent.get("role", ""),
                status=agent.get("status", ""),
                heartbeat=agent.get("last_heartbeat", ""),
                task=str(agent.get("current_task", "")).replace("|", "\\|"),
            )
        )


def create_message(args: argparse.Namespace, msg_type: str, to_agent: str) -> dict[str, Any]:
    root = bridge_root(args)
    require_agent(root, args.from_agent)
    if to_agent != "broadcast" and not to_agent.startswith("channel:"):
        require_agent(root, to_agent)
    if msg_type not in MESSAGE_TYPES:
        die(f"unsupported message type: {msg_type}")
    seq = next_seq(root, args.from_agent)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return {
        "schema": "agent-bridge.message.v1",
        "id": f"msg_{stamp}_{args.from_agent}_{seq:06d}",
        "thread_id": args.thread_id or f"thr_{stamp}_{args.from_agent}",
        "seq": seq,
        "from": args.from_agent,
        "to": to_agent,
        "type": msg_type,
        "priority": args.priority,
        "created_at": utcnow(),
        "expires_at": args.expires_at,
        "requires_ack": bool(args.requires_ack),
        "parent_id": args.parent_id,
        "subject": args.subject,
        "body": args.body or "",
        "artifacts": parse_artifacts(args.artifact),
        "status": "queued",
    }


def deliver_message(root: Path, message: dict[str, Any]) -> None:
    target = str(message["to"])
    sender = str(message["from"])
    inbox_name = "broadcast.jsonl" if target == "broadcast" else filename_for(target)
    append_jsonl(root / "inbox" / inbox_name, message)
    append_jsonl(root / "outbox" / filename_for(sender), message)
    append_event(root, sender, "message.sent", message_id=message["id"], message=message)


def command_send(args: argparse.Namespace) -> None:
    root = bridge_root(args)
    ensure_layout(root)
    with LOG_LOCK:
        message = create_message(args, args.type, args.to)
        deliver_message(root, message)
    print(message["id"])


def command_broadcast(args: argparse.Namespace) -> None:
    root = bridge_root(args)
    ensure_layout(root)
    with LOG_LOCK:
        message = create_message(args, "notice", "broadcast")
        deliver_message(root, message)
    print(message["id"])


def command_inbox(args: argparse.Namespace) -> None:
    root = bridge_root(args)
    require_agent(root, args.agent_id)
    _, messages, errors = load_state(root)
    for error in errors:
        print(error, file=sys.stderr)
    selected = [
        msg
        for msg in messages.values()
        if msg.get("to") in {args.agent_id, "broadcast"} and msg.get("state") not in TERMINAL_STATES
    ]
    selected.sort(key=lambda msg: str(msg.get("created_at", "")))
    if args.json:
        print(json.dumps(selected, indent=2, sort_keys=True))
        return
    if not selected:
        print("No pending messages.")
        return
    print("| ID | From | Type | State | Subject |")
    print("|---|---|---|---|---|")
    for msg in selected:
        print(
            "| {id} | {sender} | {type} | {state} | {subject} |".format(
                id=msg.get("id", ""),
                sender=msg.get("from", ""),
                type=msg.get("type", ""),
                state=msg.get("state", ""),
                subject=str(msg.get("subject", "")).replace("|", "\\|"),
            )
        )


def check_transition(
    messages: dict[str, dict[str, Any]], message_id: str, agent_id: str, event_type: str
) -> None:
    """Validate a message state transition. Raises ValueError if illegal.

    Enforces the state machine (only legal edges, no transitions out of a
    terminal state) and ownership (only the recipient may drive a directed
    message through its lifecycle).
    """
    message = messages.get(message_id)
    if message is None:
        raise ValueError(f"unknown message_id: {message_id}")
    current = str(message.get("state", "queued"))
    if current in TERMINAL_STATES:
        raise ValueError(f"message {message_id} is already {current}; no further transitions allowed")
    allowed = ALLOWED_FROM.get(event_type, set())
    if current not in allowed:
        action = event_type.split(".", 1)[-1]
        raise ValueError(f"illegal transition: cannot apply '{action}' to a message in state '{current}'")
    if event_type in OWNED_TRANSITIONS:
        target = str(message.get("to", ""))
        if not target.startswith("channel:") and target != "broadcast" and target != agent_id:
            raise ValueError(
                f"agent '{agent_id}' is not the recipient of {message_id} and cannot change its state"
            )


def message_event(args: argparse.Namespace, event_type: str, **payload: Any) -> None:
    root = bridge_root(args)
    require_agent(root, args.agent_id)
    with LOG_LOCK:
        _, messages, _ = load_state(root)
        try:
            check_transition(messages, args.message_id, args.agent_id, event_type)
        except ValueError as exc:
            die(str(exc))
        append_event(root, args.agent_id, event_type, message_id=args.message_id, **payload)
    print(f"{event_type} {args.message_id}")


def command_ack(args: argparse.Namespace) -> None:
    message_event(args, "message.ack")


def command_start(args: argparse.Namespace) -> None:
    message_event(args, "message.started")


def command_complete(args: argparse.Namespace) -> None:
    message_event(args, "message.completed", artifacts=parse_artifacts(args.artifact))


def command_block(args: argparse.Namespace) -> None:
    message_event(args, "message.blocked", reason=args.reason)


def command_fail(args: argparse.Namespace) -> None:
    message_event(args, "message.failed", reason=args.reason, recoverable=args.recoverable)


def command_expire(args: argparse.Namespace) -> None:
    root = bridge_root(args)
    require_agent(root, args.agent_id)
    expired: list[str] = []
    with LOG_LOCK:
        _, messages, _ = load_state(root)
        now = utcnow()
        for message_id, message in messages.items():
            expires_at = message.get("expires_at")
            if not expires_at or message.get("state") in TERMINAL_STATES:
                continue
            if str(expires_at) <= now:
                append_event(root, args.agent_id, "message.expired", message_id=message_id)
                expired.append(message_id)
    for message_id in expired:
        print(f"message.expired {message_id}")
    if not expired:
        print("no messages to expire")


def command_heartbeat(args: argparse.Namespace) -> None:
    root = bridge_root(args)
    require_agent(root, args.agent_id)
    append_event(root, args.agent_id, "heartbeat", task=args.task, status=args.status)
    print(f"heartbeat {args.agent_id}")


def command_handoff(args: argparse.Namespace) -> None:
    args.type = "handoff"
    args.subject = args.subject or f"Handoff from {args.from_agent}"
    args.body = args.body or ""
    args.requires_ack = True
    command_send(args)


def build_monitor(root: Path) -> str:
    agents, messages, errors = load_state(root)
    lines = ["# Agent Bridge Monitor", ""]
    if errors:
        lines.extend(["## Log Warnings", ""])
        lines.extend(f"- {error}" for error in errors)
        lines.append("")
    lines.extend(["## Agents", "", "| Agent | Runtime | Role | Status | Last heartbeat | Current task |", "|---|---|---|---|---|---|"])
    for agent_id, agent in sorted(agents.items()):
        lines.append(
            "| {agent} | {runtime} | {role} | {status} | {heartbeat} | {task} |".format(
                agent=agent_id,
                runtime=agent.get("runtime", ""),
                role=agent.get("role", ""),
                status=agent.get("status", ""),
                heartbeat=agent.get("last_heartbeat", ""),
                task=str(agent.get("current_task", "")).replace("|", "\\|"),
            )
        )
    pending = [msg for msg in messages.values() if msg.get("to") != "broadcast" and msg.get("state") not in TERMINAL_STATES]
    pending.sort(key=lambda msg: str(msg.get("created_at", "")))
    lines.extend(["", "## Pending Directed Messages", "", "| ID | From | To | Type | State | Subject |", "|---|---|---|---|---|---|"])
    for msg in pending:
        lines.append(
            "| {id} | {sender} | {target} | {type} | {state} | {subject} |".format(
                id=msg.get("id", ""),
                sender=msg.get("from", ""),
                target=msg.get("to", ""),
                type=msg.get("type", ""),
                state=msg.get("state", ""),
                subject=str(msg.get("subject", "")).replace("|", "\\|"),
            )
        )
    blocked = [msg for msg in messages.values() if msg.get("state") == "blocked"]
    lines.extend(["", "## Blocked Work", "", "| ID | Owner | Blocker | Since |", "|---|---|---|---|"])
    for msg in blocked:
        lines.append(
            "| {id} | {owner} | {reason} | {since} |".format(
                id=msg.get("id", ""),
                owner=msg.get("to", ""),
                reason=str(msg.get("reason", "")).replace("|", "\\|"),
                since=msg.get("state_at", ""),
            )
        )
    events, _ = all_events(root)
    lines.extend(["", "## Recent Events", "", "| Time | Event | Summary |", "|---|---|---|"])
    for event in events[-20:]:
        summary = event.get("message_id") or event.get("target_agent_id") or event.get("agent_id", "")
        lines.append(f"| {event.get('created_at', '')} | {event.get('type', '')} | {summary} |")
    lines.append("")
    return "\n".join(lines)


def command_monitor(args: argparse.Namespace) -> None:
    root = bridge_root(args)
    ensure_layout(root)
    text = build_monitor(root)
    monitor_path = root / "monitor.md"
    fd, tmp_name = tempfile.mkstemp(prefix=".monitor.", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, monitor_path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    print(text)


def command_validate(args: argparse.Namespace) -> None:
    root = bridge_root(args)
    _, _, errors = load_state(root)
    if protocol_path(root).exists():
        read_json(protocol_path(root))
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        raise SystemExit(1)
    print("agent-bridge validation passed")


def add_common_message_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--from", dest="from_agent", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body", default="")
    parser.add_argument("--thread-id")
    parser.add_argument("--parent-id")
    parser.add_argument("--priority", choices=["low", "normal", "high", "urgent"], default="normal")
    parser.add_argument("--expires-at")
    parser.add_argument("--artifact", action="append")
    parser.add_argument("--requires-ack", action=argparse.BooleanOptionalAction, default=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local multi-agent JSONL bridge")
    parser.add_argument("--root", default=".agent-bridge", help="bridge root directory")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=command_init)

    register = sub.add_parser("register")
    register.add_argument("--agent-id", required=True)
    register.add_argument("--runtime", required=True)
    register.add_argument("--role", required=True)
    register.add_argument("--display-name")
    register.add_argument("--capabilities", default="")
    register.set_defaults(func=command_register)

    agents = sub.add_parser("agents")
    agents.add_argument("--json", action="store_true")
    agents.set_defaults(func=command_agents)

    send = sub.add_parser("send")
    send.add_argument("--to", required=True)
    send.add_argument("--type", required=True, choices=sorted(MESSAGE_TYPES))
    add_common_message_args(send)
    send.set_defaults(func=command_send)

    broadcast = sub.add_parser("broadcast")
    add_common_message_args(broadcast)
    broadcast.set_defaults(func=command_broadcast)

    inbox = sub.add_parser("inbox")
    inbox.add_argument("--agent-id", required=True)
    inbox.add_argument("--json", action="store_true")
    inbox.set_defaults(func=command_inbox)

    for name, func in [("ack", command_ack), ("start", command_start)]:
        event = sub.add_parser(name)
        event.add_argument("--agent-id", required=True)
        event.add_argument("message_id")
        event.set_defaults(func=func)

    complete = sub.add_parser("complete")
    complete.add_argument("--agent-id", required=True)
    complete.add_argument("--artifact", action="append")
    complete.add_argument("message_id")
    complete.set_defaults(func=command_complete)

    block = sub.add_parser("block")
    block.add_argument("--agent-id", required=True)
    block.add_argument("--reason", required=True)
    block.add_argument("message_id")
    block.set_defaults(func=command_block)

    fail = sub.add_parser("fail")
    fail.add_argument("--agent-id", required=True)
    fail.add_argument("--reason", required=True)
    fail.add_argument("--recoverable", action=argparse.BooleanOptionalAction, default=True)
    fail.add_argument("message_id")
    fail.set_defaults(func=command_fail)

    expire = sub.add_parser("expire")
    expire.add_argument("--agent-id", required=True)
    expire.set_defaults(func=command_expire)

    heartbeat = sub.add_parser("heartbeat")
    heartbeat.add_argument("--agent-id", required=True)
    heartbeat.add_argument("--task", required=True)
    heartbeat.add_argument("--status", default="active")
    heartbeat.set_defaults(func=command_heartbeat)

    handoff = sub.add_parser("handoff")
    handoff.add_argument("--to", required=True)
    add_common_message_args(handoff)
    handoff.set_defaults(func=command_handoff)

    monitor = sub.add_parser("monitor")
    monitor.set_defaults(func=command_monitor)

    validate = sub.add_parser("validate")
    validate.set_defaults(func=command_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
