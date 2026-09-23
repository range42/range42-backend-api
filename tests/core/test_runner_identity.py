import json
import os

from app.core import runner_detached


def test_identity_only_matches_the_same_process(tmp_path):
    runner_detached.record_process_identity(tmp_path, os.getpid())
    assert runner_detached.process_matches(tmp_path, os.getpid())
    identity = json.loads((tmp_path / "process.json").read_text())
    identity["start_time"] = "different-process"
    (tmp_path / "process.json").write_text(json.dumps(identity))
    assert not runner_detached.process_matches(tmp_path, os.getpid())


def test_unverified_or_invalid_pid_is_never_adopted(tmp_path):
    assert not runner_detached.process_matches(tmp_path, os.getpid())
    assert not runner_detached.process_matches(tmp_path, 0)
    assert not runner_detached.process_matches(tmp_path, -1)
