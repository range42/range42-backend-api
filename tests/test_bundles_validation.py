"""Tests for bundle VM validation functions in app.routes.bundles."""

import pytest
from types import SimpleNamespace
from fastapi import HTTPException

from app.routes.bundles import _check_admin_vms, _check_vuln_vms, _check_student_vms


def _make_vm_spec(vm_id=1000, vm_ip="192.168.42.100", vm_description="test"):
    return SimpleNamespace(vm_id=vm_id, vm_ip=vm_ip, vm_description=vm_description)


ADMIN_KEYS = {
    "admin-wazuh", "admin-web-api-kong", "admin-web-builder-api",
    "admin-web-deployer-ui", "admin-web-emp",
}
VULN_KEYS = {"vuln-box-00", "vuln-box-01", "vuln-box-02", "vuln-box-03", "vuln-box-04"}
STUDENT_KEYS = {"student-box-01"}


class TestCheckAdminVms:
    """Tests for _check_admin_vms()."""

    def test_valid_admin_vms_passes(self):
        req = SimpleNamespace(vms={k: _make_vm_spec() for k in ADMIN_KEYS})
        _check_admin_vms(req)  # should not raise

    def test_empty_vms_raises(self):
        req = SimpleNamespace(vms={})
        with pytest.raises(HTTPException) as exc_info:
            _check_admin_vms(req)
        assert exc_info.value.status_code == 400

    def test_missing_required_key_raises(self):
        vms = {k: _make_vm_spec() for k in ADMIN_KEYS}
        del vms["admin-wazuh"]
        req = SimpleNamespace(vms=vms)
        with pytest.raises(HTTPException) as exc_info:
            _check_admin_vms(req)
        assert "Missing required vm key" in exc_info.value.detail

    def test_unauthorized_key_raises(self):
        vms = {k: _make_vm_spec() for k in ADMIN_KEYS}
        vms["rogue-vm"] = _make_vm_spec()
        req = SimpleNamespace(vms=vms)
        with pytest.raises(HTTPException) as exc_info:
            _check_admin_vms(req)
        assert "Unauthorized vm key" in exc_info.value.detail

    def test_missing_vm_id_raises(self):
        vms = {k: _make_vm_spec() for k in ADMIN_KEYS}
        vms["admin-wazuh"] = _make_vm_spec(vm_id=None)
        req = SimpleNamespace(vms=vms)
        with pytest.raises(HTTPException) as exc_info:
            _check_admin_vms(req)
        assert "missing key vm_id" in exc_info.value.detail

    def test_missing_vm_description_raises(self):
        vms = {k: _make_vm_spec() for k in ADMIN_KEYS}
        vms["admin-wazuh"] = _make_vm_spec(vm_description=None)
        req = SimpleNamespace(vms=vms)
        with pytest.raises(HTTPException) as exc_info:
            _check_admin_vms(req)
        assert "missing key vm_description" in exc_info.value.detail

    def test_missing_vm_ip_raises(self):
        vms = {k: _make_vm_spec() for k in ADMIN_KEYS}
        vms["admin-wazuh"] = _make_vm_spec(vm_ip=None)
        req = SimpleNamespace(vms=vms)
        with pytest.raises(HTTPException) as exc_info:
            _check_admin_vms(req)
        assert "missing key vm_ip" in exc_info.value.detail


class TestCheckVulnVms:
    """Tests for _check_vuln_vms()."""

    def test_valid_vuln_vms_passes(self):
        req = SimpleNamespace(vms={k: _make_vm_spec() for k in VULN_KEYS})
        _check_vuln_vms(req)

    def test_empty_vms_raises(self):
        req = SimpleNamespace(vms={})
        with pytest.raises(HTTPException):
            _check_vuln_vms(req)

    def test_missing_required_key_raises(self):
        vms = {k: _make_vm_spec() for k in VULN_KEYS}
        del vms["vuln-box-00"]
        req = SimpleNamespace(vms=vms)
        with pytest.raises(HTTPException) as exc_info:
            _check_vuln_vms(req)
        assert "Missing required vm key" in exc_info.value.detail

    def test_unauthorized_key_raises(self):
        vms = {k: _make_vm_spec() for k in VULN_KEYS}
        vms["vuln-box-99"] = _make_vm_spec()
        req = SimpleNamespace(vms=vms)
        with pytest.raises(HTTPException) as exc_info:
            _check_vuln_vms(req)
        assert "Unauthorized vm key" in exc_info.value.detail


class TestCheckStudentVms:
    """Tests for _check_student_vms()."""

    def test_valid_student_vms_passes(self):
        req = SimpleNamespace(vms={k: _make_vm_spec() for k in STUDENT_KEYS})
        _check_student_vms(req)

    def test_empty_vms_raises(self):
        req = SimpleNamespace(vms={})
        with pytest.raises(HTTPException):
            _check_student_vms(req)

    def test_missing_required_key_raises(self):
        req = SimpleNamespace(vms={})
        with pytest.raises(HTTPException) as exc_info:
            _check_student_vms(req)
        assert exc_info.value.status_code == 400

    def test_unauthorized_key_raises(self):
        vms = {k: _make_vm_spec() for k in STUDENT_KEYS}
        vms["student-box-99"] = _make_vm_spec()
        req = SimpleNamespace(vms=vms)
        with pytest.raises(HTTPException) as exc_info:
            _check_student_vms(req)
        assert "Unauthorized vm key" in exc_info.value.detail
