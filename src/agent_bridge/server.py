#!/usr/bin/env python3
"""Unauthenticated HTTP API for the local agent bridge."""

from __future__ import annotations

import argparse
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from agent_bridge import core as agent_bridge


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
CHANNEL_PREFIX = "channel:"
GENERAL_CHANNEL = "general"


def channel_target(channel: str) -> str:
    agent_bridge.validate_agent_id(channel)
    return f"{CHANNEL_PREFIX}{channel}"


def is_channel_target(target: str) -> bool:
    return target.startswith(CHANNEL_PREFIX)


def channel_name_from_target(target: str) -> str:
    return target.removeprefix(CHANNEL_PREFIX)


def api_protocol(base_url: str) -> dict[str, Any]:
    return {
        "schema": "agent-bridge.http-protocol.v1",
        "transport": "http-json",
        "auth": "none",
        "base_url": base_url,
        "channels": {
            "list": "GET /channels",
            "create": "POST /channels",
            "read": "GET /channels/{channel}/messages",
            "send": "POST /channels/{channel}/messages",
            "routing": "Channel messages use to=channel:{channel}. The general channel is channel:general.",
        },
        "general_channel": {
            "join": "POST /general/join",
            "read": "GET /general",
            "purpose": "Agents announce presence and ask where work is happening before moving to a topic channel.",
        },
        "identity": {
            "register": "POST /agents",
            "list": "GET /agents",
            "agent_id": "Stable id matching [A-Za-z0-9][A-Za-z0-9_.:-]{0,127}",
        },
        "messaging": {
            "send": "POST /messages",
            "inbox": "GET /inbox/{agent_id}",
            "ack": "POST /messages/{message_id}/ack",
            "start": "POST /messages/{message_id}/start",
            "complete": "POST /messages/{message_id}/complete",
            "block": "POST /messages/{message_id}/block",
            "fail": "POST /messages/{message_id}/fail",
            "channel": "Use POST /messages with to=channel:{channel}, or POST /channels/{channel}/messages.",
        },
        "message_types": sorted(agent_bridge.MESSAGE_TYPES),
        "rules": [
            "No authentication is required.",
            "Register or join general before sending directed messages.",
            "Use agent_id for routing; runtime is metadata only.",
            "Use channel:general for discovery, then move topic work into a named channel.",
            "Do not place secrets in message bodies.",
            "Read /instructions before talking to other agents.",
        ],
    }


def instructions_text(base_url: str) -> str:
    protocol = api_protocol(base_url)
    return f"""# Agent Bridge HTTP Instructions

Use this unauthenticated HTTP server to coordinate with other agents.

Base URL: {base_url}
Protocol JSON: GET {base_url}/protocol

## First steps

1. Read these instructions from `GET /instructions`.
2. Announce yourself in the general channel (`channel:general`):

```bash
curl -sS -X POST {base_url}/general/join \\
  -H 'Content-Type: application/json' \\
  -d '{{"agent_id":"agent.alpha","runtime":"codex","role":"researcher","display_name":"Agent Alpha","capabilities":["research","review"],"task":"standing by"}}'
```

3. Ask on general where work is happening, for example "where are you working on shellfire crypto?".
4. Move to a named channel, such as `shellfire.crypto`, and talk there.
5. Send directed messages with `POST /messages`; read your directed inbox with `GET /inbox/{{agent_id}}`.

## Channels

- `GET /channels` lists channels seen by the server.
- `POST /channels` creates/announces a channel: `{{"name":"shellfire.crypto","created_by":"agent.alpha","purpose":"crypto-bunker work"}}`.
- `GET /channels/general/messages` reads the general channel.
- `POST /channels/shellfire.crypto/messages` sends to that channel.
- `GET /general` is an alias for `GET /channels/general/messages`.

Channel message:

```json
{{
  "from": "agent.alpha",
  "type": "question",
  "subject": "Workspace",
  "body": "I am working on crypto-bunker. Join channel shellfire.crypto."
}}
```

## Message body

```json
{{
  "from": "agent.alpha",
  "to": "agent.beta",
  "type": "question",
  "subject": "Need review",
  "body": "Can you review this finding?",
  "priority": "normal"
}}
```

Supported message types: {", ".join(protocol["message_types"])}

## State updates

Use `POST /messages/{{message_id}}/ack`, `/start`, `/complete`, `/block`, or `/fail` with JSON body `{{"agent_id":"agent.beta"}}`.
For block/fail include `reason`; for complete include optional `artifacts`.
"""


class BridgeAPI(BaseHTTPRequestHandler):
    server_version = "AgentBridgeHTTP/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}", file=sys.stderr)

    @property
    def root(self) -> Path:
        return self.server.bridge_root  # type: ignore[attr-defined]

    @property
    def base_url(self) -> str:
        return self.server.base_url  # type: ignore[attr-defined]

    def send_json(self, status: HTTPStatus, payload: Any) -> None:
        encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_text(self, status: HTTPStatus, text: str) -> None:
        encoded = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON body: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def handle_error(self, exc: Exception) -> None:
        status = HTTPStatus.BAD_REQUEST
        if isinstance(exc, KeyError):
            message = f"missing required field: {exc.args[0]}"
        else:
            message = str(exc)
        self.send_json(status, {"error": message})

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/":
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "service": "agent-bridge",
                        "instructions": f"{self.base_url}/instructions",
                        "protocol": f"{self.base_url}/protocol",
                    },
                )
            elif path == "/instructions":
                self.send_text(HTTPStatus.OK, instructions_text(self.base_url))
            elif path == "/protocol":
                self.send_json(HTTPStatus.OK, api_protocol(self.base_url))
            elif path == "/agents":
                agents, _, errors = agent_bridge.load_state(self.root)
                self.send_json(HTTPStatus.OK, {"agents": list(agents.values()), "warnings": errors})
            elif path == "/general":
                self.list_channel_messages(GENERAL_CHANNEL)
            elif path == "/channels":
                self.list_channels()
            elif path.startswith("/channels/") and path.endswith("/messages"):
                channel = self.channel_from_messages_path(path)
                self.list_channel_messages(channel)
            elif path.startswith("/inbox/"):
                agent_id = path.removeprefix("/inbox/")
                agent_bridge.require_agent(self.root, agent_id)
                _, messages, errors = agent_bridge.load_state(self.root)
                selected = [
                    msg
                    for msg in messages.values()
                    if msg.get("to") == agent_id and msg.get("state") not in agent_bridge.TERMINAL_STATES
                ]
                selected.sort(key=lambda msg: str(msg.get("created_at", "")))
                self.send_json(HTTPStatus.OK, {"messages": selected, "warnings": errors})
            else:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except Exception as exc:
            self.handle_error(exc)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self.read_body()
            if path == "/agents":
                self.register_agent(body)
            elif path == "/general/join":
                self.join_general(body)
            elif path == "/channels":
                self.create_channel(body)
            elif path.startswith("/channels/") and path.endswith("/messages"):
                channel = self.channel_from_messages_path(path)
                body = {**body, "to": channel_target(channel)}
                self.create_http_message(body)
            elif path == "/messages":
                self.create_http_message(body)
            elif path.startswith("/messages/"):
                self.update_message(path, body)
            else:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except Exception as exc:
            self.handle_error(exc)

    def save_agent(self, body: dict[str, Any]) -> dict[str, Any]:
        agent_id = str(body["agent_id"])
        agent_bridge.validate_agent_id(agent_id)
        agent = {
            "schema": "agent-bridge.agent.v1",
            "agent_id": agent_id,
            "runtime": str(body["runtime"]),
            "role": str(body["role"]),
            "display_name": str(body.get("display_name") or agent_id),
            "capabilities": body.get("capabilities") or [],
            "registered_at": agent_bridge.utcnow(),
            "status": str(body.get("status") or "active"),
        }
        if not isinstance(agent["capabilities"], list):
            raise ValueError("capabilities must be a list")
        agent_bridge.write_json_atomic(self.root / "agents" / agent_bridge.json_filename_for(agent_id), agent)
        agent_bridge.append_event(self.root, agent_id, "agent.registered", agent=agent)
        return agent

    def register_agent(self, body: dict[str, Any]) -> None:
        agent = self.save_agent(body)
        self.send_json(HTTPStatus.CREATED, agent)

    def join_general(self, body: dict[str, Any]) -> None:
        self.save_agent(body)
        self.ensure_channel(GENERAL_CHANNEL, str(body["agent_id"]), "General discovery channel")
        body = {**body, "from": body["agent_id"], "to": channel_target(GENERAL_CHANNEL), "type": "notice", "subject": body.get("subject") or "Agent joined general channel"}
        body["body"] = body.get("body") or f"{body['agent_id']} joined as {body.get('role', 'agent')}. Current task: {body.get('task', '')}"
        self.create_http_message(body, status=HTTPStatus.CREATED)

    def create_http_message(self, body: dict[str, Any], status: HTTPStatus = HTTPStatus.CREATED) -> None:
        from_agent = str(body["from"])
        to_agent = str(body["to"])
        msg_type = str(body.get("type") or "notice")
        agent_bridge.require_agent(self.root, from_agent)
        if is_channel_target(to_agent):
            self.ensure_channel(channel_name_from_target(to_agent), from_agent, "")
        elif to_agent != "broadcast":
            agent_bridge.require_agent(self.root, to_agent)
        if msg_type not in agent_bridge.MESSAGE_TYPES:
            raise ValueError(f"unsupported message type: {msg_type}")
        args = argparse.Namespace(
            root=str(self.root),
            from_agent=from_agent,
            thread_id=body.get("thread_id"),
            parent_id=body.get("parent_id"),
            priority=body.get("priority") or "normal",
            expires_at=body.get("expires_at"),
            requires_ack=body.get("requires_ack", True),
            subject=str(body["subject"]),
            body=str(body.get("body") or ""),
            artifact=body.get("artifact") or [],
        )
        with agent_bridge.LOG_LOCK:
            message = agent_bridge.create_message(args, msg_type, to_agent)
            if "artifacts" in body:
                artifacts = body["artifacts"]
                if not isinstance(artifacts, list):
                    raise ValueError("artifacts must be a list")
                message["artifacts"] = artifacts
            agent_bridge.deliver_message(self.root, message)
        self.send_json(status, message)

    def channel_from_messages_path(self, path: str) -> str:
        prefix = "/channels/"
        suffix = "/messages"
        channel = unquote(path[len(prefix) : -len(suffix)])
        agent_bridge.validate_agent_id(channel)
        return channel

    def ensure_channel(self, name: str, created_by: str, purpose: str) -> None:
        agent_bridge.validate_agent_id(name)
        agent_bridge.append_event(
            self.root,
            created_by,
            "channel.created",
            channel={"name": name, "target": channel_target(name), "purpose": purpose, "created_by": created_by},
        )

    def create_channel(self, body: dict[str, Any]) -> None:
        name = str(body["name"])
        created_by = str(body["created_by"])
        agent_bridge.require_agent(self.root, created_by)
        purpose = str(body.get("purpose") or "")
        self.ensure_channel(name, created_by, purpose)
        self.send_json(HTTPStatus.CREATED, {"name": name, "target": channel_target(name), "purpose": purpose, "created_by": created_by})

    def list_channels(self) -> None:
        events, errors = agent_bridge.all_events(self.root)
        channels: dict[str, dict[str, Any]] = {
            GENERAL_CHANNEL: {"name": GENERAL_CHANNEL, "target": channel_target(GENERAL_CHANNEL), "purpose": "General discovery channel"}
        }
        for event in events:
            channel = event.get("channel")
            if isinstance(channel, dict) and isinstance(channel.get("name"), str):
                current = channels.get(channel["name"], {})
                merged = {**current, **channel}
                if current.get("purpose") and not channel.get("purpose"):
                    merged["purpose"] = current["purpose"]
                channels[channel["name"]] = merged
            message = event.get("message")
            if isinstance(message, dict) and isinstance(message.get("to"), str) and is_channel_target(message["to"]):
                name = channel_name_from_target(message["to"])
                channels.setdefault(name, {"name": name, "target": message["to"], "purpose": ""})
        self.send_json(HTTPStatus.OK, {"channels": list(channels.values()), "warnings": errors})

    def list_channel_messages(self, channel: str) -> None:
        target = channel_target(channel)
        _, messages, errors = agent_bridge.load_state(self.root)
        selected = [msg for msg in messages.values() if msg.get("to") == target]
        selected.sort(key=lambda msg: str(msg.get("created_at", "")))
        self.send_json(HTTPStatus.OK, {"channel": channel, "target": target, "messages": selected, "warnings": errors})

    def update_message(self, path: str, body: dict[str, Any]) -> None:
        parts = path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "messages":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        message_id, action = parts[1], parts[2]
        agent_id = str(body["agent_id"])
        event_type = {
            "ack": "message.ack",
            "start": "message.started",
            "complete": "message.completed",
            "block": "message.blocked",
            "fail": "message.failed",
        }.get(action)
        if event_type is None:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        agent_bridge.require_agent(self.root, agent_id)
        with agent_bridge.LOG_LOCK:
            _, messages, _ = agent_bridge.load_state(self.root)
            agent_bridge.check_transition(messages, message_id, agent_id, event_type)
            payload: dict[str, Any] = {}
            if action in {"block", "fail"}:
                payload["reason"] = str(body["reason"])
            if action == "fail":
                payload["recoverable"] = bool(body.get("recoverable", True))
            if action == "complete":
                payload["artifacts"] = body.get("artifacts") or []
            event = agent_bridge.append_event(self.root, agent_id, event_type, message_id=message_id, **payload)
        self.send_json(HTTPStatus.OK, event)


def run_server(host: str, port: int, root: Path, public_host: str | None = None) -> None:
    agent_bridge.ensure_layout(root)
    base_host = public_host or host
    server = ThreadingHTTPServer((host, port), BridgeAPI)
    server.bridge_root = root  # type: ignore[attr-defined]
    server.base_url = f"http://{base_host}:{server.server_port}"  # type: ignore[attr-defined]
    print(f"agent-bridge HTTP server listening on {server.base_url}")
    print(f"instructions endpoint: {server.base_url}/instructions")
    server.serve_forever()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unauthenticated HTTP server for agent bridge")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--root", default=".agent-bridge")
    parser.add_argument("--public-host", help="host advertised in generated instructions")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_server(args.host, args.port, Path(args.root).resolve(), args.public_host)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
