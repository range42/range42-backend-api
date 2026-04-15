import asyncio
import pytest

from app.core.allocation import (
    allocate_vmids,
    allocate_vmids_locked,
    ssh_controlmaster_env,
)


@pytest.mark.asyncio
async def test_allocate_vmids_is_contiguous_and_skips_protected():
    reserved = {100, 101, 4000, 4001}
    out = allocate_vmids(
        start=99, count=5, reserved=reserved, host_overrides=None
    )
    # Must not include any reserved or default-protected vmid.
    for v in out:
        assert v not in reserved
        assert not (100 <= v <= 101)
        assert not (4000 <= v <= 4004)
    assert len(out) == 5
    assert len(set(out)) == 5


@pytest.mark.asyncio
async def test_mutex_serialises_allocations():
    # Two concurrent allocations should not overlap.
    reserved: set[int] = set()

    async def work(start):
        vmids = await allocate_vmids_locked(
            start=start, count=3, reserved=reserved, host_overrides=None
        )
        reserved.update(vmids)
        return vmids

    a, b = await asyncio.gather(work(200), work(200))
    assert set(a).isdisjoint(set(b))


@pytest.mark.asyncio
async def test_host_overrides_are_skipped():
    out = allocate_vmids(
        start=200, count=3, reserved=set(), host_overrides=[[200, 202]]
    )
    for v in out:
        assert v not in {200, 201, 202}
    assert out == [203, 204, 205]


@pytest.mark.asyncio
async def test_exhaustion_raises():
    with pytest.raises(RuntimeError):
        allocate_vmids(
            start=99998, count=5, reserved=set(), host_overrides=None
        )


def test_ssh_controlmaster_env_is_namespaced(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    env = ssh_controlmaster_env(deployment_id="dep-abc")
    assert "ANSIBLE_SSH_ARGS" in env
    assert "control-dep-abc" in env["ANSIBLE_SSH_ARGS"]
    assert "ControlMaster=auto" in env["ANSIBLE_SSH_ARGS"]


def test_ssh_controlmaster_env_explicit_home(tmp_path):
    env = ssh_controlmaster_env(deployment_id="dep-xyz", home=tmp_path)
    # Directory must be created.
    assert (tmp_path / ".ssh" / "range42").is_dir()
    assert str(tmp_path) in env["ANSIBLE_SSH_ARGS"]
    assert "control-dep-xyz" in env["ANSIBLE_SSH_ARGS"]
