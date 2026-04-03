from app.core.runner import build_logs


def test_build_logs_extracts_stdout():
    events = [
        {"stdout": "PLAY [all] ***"},
        {"stdout": "TASK [ping] ***"},
        {"other": "data"},
        {"stdout": "ok: [host1]"},
    ]
    log_ansi, log_plain = build_logs(events)
    assert "PLAY [all]" in log_ansi
    assert "ok: [host1]" in log_plain


def test_build_logs_empty_events():
    log_ansi, log_plain = build_logs([])
    assert log_ansi == ""
    assert log_plain == ""


def test_build_logs_strips_ansi():
    events = [{"stdout": "\x1b[32mok\x1b[0m: [host1]"}]
    log_ansi, log_plain = build_logs(events)
    assert "\x1b[32m" in log_ansi
    assert "\x1b[32m" not in log_plain
    assert "ok" in log_plain
