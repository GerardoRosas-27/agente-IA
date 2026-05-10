from __future__ import annotations

from pathlib import Path

from harness.events import emit_event, event_counts_by_outcome, read_events


def test_emit_event_appends_jsonl(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"

    emit_event("cycle.started", path=target, feature_id=1, feature_name="x")
    emit_event(
        "verifier.verdict",
        path=target,
        feature_id=1,
        feature_name="x",
        outcome="pass",
        score=0.91,
    )

    events = read_events(path=target)
    assert len(events) == 2
    assert events[0]["event"] == "cycle.started"
    assert events[1]["outcome"] == "pass"
    assert events[1]["score"] == 0.91


def test_event_counts_by_outcome(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    for outcome in ["pass", "pass", "fail", "pass", "ambiguous"]:
        emit_event("cycle.finished", path=target, outcome=outcome)

    counts = event_counts_by_outcome(path=target)
    assert counts["pass"] == 3
    assert counts["fail"] == 1
    assert counts["ambiguous"] == 1


def test_read_events_handles_missing_file(tmp_path: Path) -> None:
    assert read_events(path=tmp_path / "nope.jsonl") == []


def test_read_events_limit_returns_last_items(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    for index in range(10):
        emit_event("cycle.finished", path=target, outcome=f"outcome_{index}")

    events = read_events(path=target, limit=3)

    assert [event["outcome"] for event in events] == ["outcome_7", "outcome_8", "outcome_9"]


def test_emit_event_skips_none_fields(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    emit_event("test.event", path=target, score=None, label="x")

    events = read_events(path=target)
    assert "score" not in events[0]
    assert events[0]["label"] == "x"


def test_emit_event_serializes_nested_non_json_values(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    emit_event(
        "nested.event",
        path=target,
        extra={"path": tmp_path / "file.txt", "items": {tmp_path / "a", "b"}},
    )

    events = read_events(path=target)
    assert events[0]["extra"]["path"].endswith("file.txt")
    assert sorted(events[0]["extra"]["items"])[-1] == "b"
