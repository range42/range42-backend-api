import json
from app.core.events import EventsWriter, SENTINEL


def test_writer_appends_sealed_lines(tmp_path):
    w = EventsWriter(tmp_path / "events.jsonl")
    w.append({"event_type": "log_line", "payload": {"text": "a"}}, attempt_id="att-1")
    w.append({"event_type": "log_line", "payload": {"text": "b"}}, attempt_id="att-1")
    data = (tmp_path / "events.jsonl").read_bytes()
    assert data.endswith(SENTINEL)
    lines = [line for line in data.split(b"\n") if line and line != SENTINEL.strip(b"\n")]
    assert len(lines) == 2
    for i, line in enumerate(lines, start=1):
        obj = json.loads(line)
        assert obj["event_seq"] == i
        assert obj["attempt_id"] == "att-1"
        assert obj["ts"].endswith("Z")


def test_writer_fsyncs_on_stage_boundary(tmp_path, monkeypatch):
    fsync_calls = []
    orig = __import__("os").fsync
    monkeypatch.setattr("os.fsync", lambda fd: (fsync_calls.append(fd), orig(fd)))
    w = EventsWriter(tmp_path / "events.jsonl")
    w.append({"event_type": "log_line"}, attempt_id="a")
    before = len(fsync_calls)
    w.append({"event_type": "phase_transition", "payload": {"from": "network", "to": "router"}}, attempt_id="a")
    assert len(fsync_calls) == before + 1
    w.append({"event_type": "task_end", "payload": {"task_name": "playbook_on_stats"}}, attempt_id="a")
    assert len(fsync_calls) == before + 2


def test_writer_resumes_seq_from_file(tmp_path):
    path = tmp_path / "events.jsonl"
    w = EventsWriter(path)
    w.append({"event_type": "log_line"}, attempt_id="a")
    w.append({"event_type": "log_line"}, attempt_id="a")
    w2 = EventsWriter(path)
    w2.append({"event_type": "log_line"}, attempt_id="a")
    lines = [json.loads(line) for line in path.read_bytes().splitlines() if line.strip() and line.strip() != b'""']
    assert [ln["event_seq"] for ln in lines] == [1, 2, 3]
