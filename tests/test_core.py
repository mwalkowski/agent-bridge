from pathlib import Path

import pytest

import agent_bridge.core as core


def _init_two_agents(root):
    core.main(["--root", root, "init"])
    core.main(["--root", root, "register", "--agent-id", "claude.planner",
               "--runtime", "claude-code", "--role", "planner"])
    core.main(["--root", root, "register", "--agent-id", "codex.reviewer",
               "--runtime", "codex", "--role", "reviewer"])


def _send(root, capsys, mtype="review.request", extra=()):
    capsys.readouterr()
    core.main(["--root", root, "send", "--from", "claude.planner",
               "--to", "codex.reviewer", "--type", mtype,
               "--subject", "Review", "--body", "Please review.", *extra])
    return capsys.readouterr().out.strip()


def test_message_lifecycle(tmp_path, capsys):
    root = str(tmp_path / "bridge")
    _init_two_agents(root)
    mid = _send(root, capsys)
    assert mid.startswith("msg_")
    core.main(["--root", root, "ack", "--agent-id", "codex.reviewer", mid])
    core.main(["--root", root, "start", "--agent-id", "codex.reviewer", mid])
    core.main(["--root", root, "complete", "--agent-id", "codex.reviewer", mid])
    agents, messages, errors = core.load_state(Path(root))
    assert errors == []
    assert {"claude.planner", "codex.reviewer"} <= set(agents)
    assert messages[mid]["state"] == "completed"


def test_replay_preserves_causal_order(tmp_path, capsys):
    # Regression for the sub-second ordering bug: send + ack + start + complete
    # within the same whole second must replay to 'completed', not 'queued'.
    root = str(tmp_path / "bridge")
    _init_two_agents(root)
    mid = _send(root, capsys)
    for action in ("ack", "start", "complete"):
        core.main(["--root", root, action, "--agent-id", "codex.reviewer", mid])
    _, messages, _ = core.load_state(Path(root))
    assert messages[mid]["state"] == "completed"


def test_illegal_transition_rejected(tmp_path, capsys):
    root = str(tmp_path / "bridge")
    _init_two_agents(root)
    mid = _send(root, capsys)
    # complete before start is illegal
    with pytest.raises(SystemExit):
        core.main(["--root", root, "complete", "--agent-id", "codex.reviewer", mid])
    # no transitions out of a terminal state
    core.main(["--root", root, "ack", "--agent-id", "codex.reviewer", mid])
    core.main(["--root", root, "start", "--agent-id", "codex.reviewer", mid])
    core.main(["--root", root, "fail", "--agent-id", "codex.reviewer", "--reason", "boom", mid])
    with pytest.raises(SystemExit):
        core.main(["--root", root, "complete", "--agent-id", "codex.reviewer", mid])


def test_only_recipient_can_transition(tmp_path, capsys):
    root = str(tmp_path / "bridge")
    _init_two_agents(root)
    mid = _send(root, capsys)
    # the sender is not the recipient and must not change the state
    with pytest.raises(SystemExit):
        core.main(["--root", root, "ack", "--agent-id", "claude.planner", mid])


def test_expire_marks_overdue_messages(tmp_path, capsys):
    root = str(tmp_path / "bridge")
    _init_two_agents(root)
    mid = _send(root, capsys, mtype="task.request",
                extra=("--expires-at", "2000-01-01T00:00:00Z"))
    core.main(["--root", root, "expire", "--agent-id", "claude.planner"])
    _, messages, _ = core.load_state(Path(root))
    assert messages[mid]["state"] == "expired"


def test_validate_rejects_bad_agent_id():
    with pytest.raises(SystemExit):
        core.validate_agent_id("bad id with spaces")


def test_unknown_message_type_is_rejected(tmp_path):
    root = str(tmp_path / "bridge")
    _init_two_agents(root)
    with pytest.raises(SystemExit):
        core.main(["--root", root, "send", "--from", "claude.planner", "--to", "codex.reviewer",
                   "--type", "not-a-type", "--subject", "x"])


def test_event_log_is_append_only(tmp_path):
    root = tmp_path / "bridge"
    core.main(["--root", str(root), "init"])
    core.main(["--root", str(root), "register", "--agent-id", "a.one",
               "--runtime", "cli", "--role", "worker"])
    logs = list((root / "events").glob("*.jsonl"))
    assert logs, "an event log file should exist after registration"
    first = logs[0].read_text()
    core.main(["--root", str(root), "register", "--agent-id", "a.two",
               "--runtime", "cli", "--role", "worker"])
    second = logs[0].read_text()
    assert second.startswith(first)  # earlier records are never rewritten
