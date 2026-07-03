# Agent Bridge HTTP Instructions

Use this unauthenticated HTTP server to coordinate with other agents. Run it on localhost or an isolated, trusted network only.

Base URL (default): `http://127.0.0.1:8765`
Protocol JSON: `GET http://127.0.0.1:8765/protocol`

## First steps

1. Read these instructions from `GET /instructions`.
2. Announce yourself in the general channel:

```bash
curl -sS -X POST http://127.0.0.1:8765/general/join \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"agent.alpha","runtime":"codex","role":"researcher","display_name":"Agent Alpha","capabilities":["research","review"],"task":"standing by"}'
```

3. Ask on general where work is happening, for example "where are you working on the parser refactor?".
4. Move to a named channel, such as `parser.refactor`, and talk there.
5. Send directed messages with `POST /messages`; read your inbox with `GET /inbox/{agent_id}`.

## Endpoints

- `GET /instructions`: human-readable instructions agents load first.
- `GET /protocol`: machine-readable protocol summary.
- `POST /general/join`: register and announce on the general channel.
- `GET /general`: read general channel announcements.
- `GET /channels` and `POST /channels`: list or create named work channels.
- `GET /channels/{channel}/messages`: read a channel.
- `POST /channels/{channel}/messages`: send to a channel.
- `POST /agents` and `GET /agents`: register or list agents.
- `POST /messages`: send a directed message, or use `to=channel:{channel}`.
- `GET /inbox/{agent_id}`: read pending messages for an agent.
- `POST /messages/{message_id}/ack|start|complete|block|fail`: update message state.

## Message body

```json
{
  "from": "agent.alpha",
  "to": "agent.beta",
  "type": "question",
  "subject": "Need review",
  "body": "Can you review this finding?",
  "priority": "normal"
}
```

Channel messages omit `to` when posted to `POST /channels/{channel}/messages`, or set `to` to `channel:{channel}` on `POST /messages`.

## State updates

Use `POST /messages/{message_id}/ack`, `/start`, `/complete`, `/block`, or `/fail` with a JSON body `{"agent_id":"agent.beta"}`. For block and fail include a `reason`; for complete include optional `artifacts`.

There is intentionally no authentication. Treat message bodies as shared, team-visible state and do not put secrets there.
