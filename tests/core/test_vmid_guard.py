"""Tests for protected-VMID guard (spec §7 teardown, §8 vmid guard)."""
import pytest

from app.core.vmid_guard import (
    DEFAULT_PROTECTED_RANGES,
    VmidProtectedError,
    assert_vmid_safe,
    filter_safe_vmids,
)


def test_default_ranges_protect_100_and_101():
    with pytest.raises(VmidProtectedError) as ei:
        assert_vmid_safe(100, host_overrides=None)
    assert ei.value.details[0]["reason"].startswith("100 is in protected range")
    with pytest.raises(VmidProtectedError):
        assert_vmid_safe(101, host_overrides=None)


def test_default_ranges_reject_9000_and_1111():
    for vmid in (1000, 1023, 1111, 4000, 4004, 9000, 9999):
        with pytest.raises(VmidProtectedError):
            assert_vmid_safe(vmid, host_overrides=None)


def test_non_protected_passes():
    assert assert_vmid_safe(4010, host_overrides=None) is None
    assert assert_vmid_safe(200, host_overrides=None) is None


def test_host_override_extends_default():
    override = [[300, 305]]
    with pytest.raises(VmidProtectedError):
        assert_vmid_safe(301, host_overrides=override)
    # 100 still protected via default.
    with pytest.raises(VmidProtectedError):
        assert_vmid_safe(100, host_overrides=override)


def test_filter_safe_vmids_partitions():
    safe, blocked = filter_safe_vmids([100, 200, 201, 9000], host_overrides=None)
    assert safe == [200, 201]
    assert blocked == [100, 9000]


def test_default_ranges_exported():
    # Sanity: explicitly protect 100-101 per user memory (pmg01, zbx01).
    assert (100, 101) in DEFAULT_PROTECTED_RANGES
