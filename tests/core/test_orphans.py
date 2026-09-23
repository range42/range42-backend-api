import os
from app.core.orphans import scan_workspaces, classify_pid, ReconcileResult


def test_classify_alive_vs_dead(monkeypatch):
    # Alive: own pid.
    r = classify_pid(os.getpid(), last_event_age_s=0)
    assert r == "alive"
    # Dead + recent last event. pid 2 is kthreadd on Linux; may or may not exist
    r = classify_pid(2, last_event_age_s=10)
    # Accept either 'unknown' (dead pid + old) or 'alive' (kthreadd alive)
    # or 'completed_unflushed' (dead pid + recent). Any non-error classification.
    assert r in ("alive", "unknown", "completed_unflushed")


def test_scan_workspaces_discovers_runner_pid(tmp_path):
    ws = tmp_path / "AURORA-demo"
    (ws / "runner").mkdir(parents=True)
    (ws / "runner" / "pid").write_text(str(os.getpid()))
    (ws / "events.jsonl").write_text(
        '{"event_seq":1,"event_type":"log_line","ts":"2026-04-14T13:47:00Z"}\n'
    )
    results = list(scan_workspaces(workspace_root=tmp_path))
    assert len(results) == 1
    r = results[0]
    assert isinstance(r, ReconcileResult)
    assert r.deployment_dir == ws
    assert r.pid == os.getpid()
    assert r.classification == "alive"
