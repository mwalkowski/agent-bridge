import concurrent.futures
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import agent_bridge.core as core
import agent_bridge.server as server


def _start_server(root):
    core.ensure_layout(root)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.BridgeAPI)
    httpd.bridge_root = root
    httpd.base_url = f"http://127.0.0.1:{httpd.server_port}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


def _post(url, payload):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def test_protocol_and_join(tmp_path):
    httpd = _start_server(tmp_path / "bridge")
    try:
        base = httpd.base_url
        protocol = _get(base + "/protocol")
        assert protocol["schema"] == "agent-bridge.http-protocol.v1"
        assert "review.request" in protocol["message_types"]
        _post(base + "/general/join",
              {"agent_id": "agent.alpha", "runtime": "codex", "role": "researcher"})
        agents = _get(base + "/agents")
        assert any(a["agent_id"] == "agent.alpha" for a in agents["agents"])
        assert _get(base + "/general")["messages"]
    finally:
        httpd.shutdown()


def test_directed_message_and_inbox(tmp_path):
    httpd = _start_server(tmp_path / "bridge")
    try:
        base = httpd.base_url
        _post(base + "/general/join", {"agent_id": "a.one", "runtime": "cli", "role": "worker"})
        _post(base + "/general/join", {"agent_id": "a.two", "runtime": "cli", "role": "worker"})
        message = _post(base + "/messages", {
            "from": "a.one", "to": "a.two", "type": "question",
            "subject": "Need input", "body": "What is the plan?",
        })
        assert message["id"].startswith("msg_")
        inbox = _get(base + "/inbox/a.two")
        assert any(m["id"] == message["id"] for m in inbox["messages"])
    finally:
        httpd.shutdown()


def test_illegal_transition_returns_400(tmp_path):
    httpd = _start_server(tmp_path / "bridge")
    try:
        base = httpd.base_url
        _post(base + "/general/join", {"agent_id": "a.one", "runtime": "cli", "role": "worker"})
        _post(base + "/general/join", {"agent_id": "a.two", "runtime": "cli", "role": "worker"})
        message = _post(base + "/messages",
                        {"from": "a.one", "to": "a.two", "type": "task.request", "subject": "s"})
        try:
            _post(base + f"/messages/{message['id']}/complete", {"agent_id": "a.two"})
            raise AssertionError("completing before starting should be rejected")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
    finally:
        httpd.shutdown()


def test_concurrent_sends_have_unique_ids(tmp_path):
    # Regression for the id-collision race: many concurrent sends from the same
    # agent must still receive unique message ids.
    httpd = _start_server(tmp_path / "bridge")
    try:
        base = httpd.base_url
        _post(base + "/general/join", {"agent_id": "a.one", "runtime": "cli", "role": "worker"})
        _post(base + "/general/join", {"agent_id": "a.two", "runtime": "cli", "role": "worker"})

        def send(_):
            return _post(base + "/messages",
                         {"from": "a.one", "to": "a.two", "type": "status", "subject": "s"})["id"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(send, range(40)))
        assert len(ids) == 40
        assert len(set(ids)) == 40
    finally:
        httpd.shutdown()
