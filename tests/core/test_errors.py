from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core.errors import (
    Range42Error, VmidProtectedError, WorkspaceNonLocalFsError,
    install_exception_handlers,
)
from app.core.middleware_trace import TraceIdMiddleware


def _app():
    a = FastAPI()
    a.add_middleware(TraceIdMiddleware)
    install_exception_handlers(a)

    @a.get("/vmid")
    def v():
        raise VmidProtectedError(
            details=[{"field": "vm.vm_id", "reason": "100 is in protected range"}]
        )

    @a.get("/fs")
    def f():
        raise WorkspaceNonLocalFsError(message="NFS mount detected")

    @a.get("/custom")
    def c():
        raise Range42Error(
            error="git_unreachable", code="GIT_UNREACHABLE",
            status=502, details=[{"field": "source_id", "reason": "DNS"}],
        )

    return a


def test_vmid_protected_envelope():
    c = TestClient(_app())
    r = c.get("/vmid")
    assert r.status_code == 409
    j = r.json()
    assert j["error"] == "vmid_protected"
    assert j["code"] == "VMID_PROTECTED"
    assert j["details"][0]["field"] == "vm.vm_id"
    assert j["trace_id"] == r.headers["x-range42-trace-id"]
    assert j["timestamp"].endswith("Z")


def test_fs_invariant_envelope():
    c = TestClient(_app())
    r = c.get("/fs")
    assert r.status_code == 409
    assert r.json()["code"] == "WORKSPACE_NON_LOCAL_FS"


def test_custom_status_honoured():
    c = TestClient(_app())
    assert c.get("/custom").status_code == 502


def test_validation_envelope():
    a = FastAPI()
    a.add_middleware(TraceIdMiddleware)
    install_exception_handlers(a)

    from pydantic import BaseModel

    class Body(BaseModel):
        name: str

    @a.post("/p")
    def p(body: Body):
        return {"ok": True}

    c = TestClient(a)
    r = c.post("/p", json={})
    assert r.status_code == 422
    j = r.json()
    assert j["code"] == "VALIDATION"
    assert j["error"] == "validation_error"
    assert isinstance(j["details"], list) and len(j["details"]) >= 1
    assert "field" in j["details"][0] and "reason" in j["details"][0]
