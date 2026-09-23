import sys

import pytest

from app.core import locks
from app.core.errors import Range42Error
from app.core.runner_detached import DetachedRunner


def test_provisioning_lock_serializes_different_deployments(tmp_path):
    with locks.ProvisioningLock(tmp_path) as first:
        assert first.fd >= 0
        with pytest.raises(Range42Error) as exc:
            with locks.ProvisioningLock(tmp_path):
                pass
        assert exc.value.code == "PROVISIONING_BUSY"
    with locks.ProvisioningLock(tmp_path):
        pass


@pytest.mark.asyncio
async def test_detached_process_retains_lock_when_api_closes_its_descriptor(tmp_path):
    executable = tmp_path / "runner"
    executable.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
    executable.chmod(0o700)
    handle = None
    try:
        with locks.ProvisioningLock(tmp_path / "locks") as lock:
            handle = await DetachedRunner(runner_bin=str(executable)).start(
                private_data_dir=tmp_path / "artifact", extravars={},
                envvars={"RANGE42_PROVISIONING_LOCK_FD": str(lock.fd)},
            )
        with pytest.raises(Range42Error):
            with locks.ProvisioningLock(tmp_path / "locks"):
                pass
        await handle.kill()
        with locks.ProvisioningLock(tmp_path / "locks"):
            pass
    finally:
        if handle is not None:
            await handle.kill()
