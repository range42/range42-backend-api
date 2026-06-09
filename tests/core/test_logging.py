import json
import structlog
from app.core.logging import configure_logging, get_logger, bind_context


def test_configure_logging_json_renderer(capsys):
    configure_logging(json_output=True)
    log = get_logger("test")
    log.info("hello", foo="bar")
    out = capsys.readouterr().out.strip()
    obj = json.loads(out)
    assert obj["event"] == "hello"
    assert obj["foo"] == "bar"
    assert obj["logger"] == "test"
    assert obj["level"] == "info"
    assert "timestamp" in obj


def test_bind_context_propagates_trace_id(capsys):
    configure_logging(json_output=True)
    with bind_context(trace_id="abc", deployment_id="dep-1", attempt_id="att-1"):
        log = get_logger("test")
        log.info("bound")
    out = capsys.readouterr().out.strip()
    obj = json.loads(out)
    assert obj["trace_id"] == "abc"
    assert obj["deployment_id"] == "dep-1"
    assert obj["attempt_id"] == "att-1"


def test_context_cleared_after_exit(capsys):
    configure_logging(json_output=True)
    with bind_context(trace_id="abc"):
        pass
    get_logger("test").info("after")
    out = capsys.readouterr().out.strip()
    obj = json.loads(out)
    assert "trace_id" not in obj
