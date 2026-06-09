from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core.middleware_trace import TraceIdMiddleware


def _app():
    a = FastAPI()
    a.add_middleware(TraceIdMiddleware)

    @a.get("/ping")
    def ping():
        import structlog
        log = structlog.get_logger()
        log.info("ping")
        return {"ok": True}

    return a


def test_trace_id_generated_when_absent():
    c = TestClient(_app())
    r = c.get("/ping")
    assert r.status_code == 200
    tid = r.headers["x-range42-trace-id"]
    assert len(tid) >= 16


def test_trace_id_echoed_when_provided():
    c = TestClient(_app())
    r = c.get("/ping", headers={"X-Range42-Trace-Id": "my-trace-42"})
    assert r.headers["x-range42-trace-id"] == "my-trace-42"
