"""Tests for response/reply Pydantic schemas."""

import pytest
from pydantic import ValidationError

from app.schemas.vms import (
    VmActionReply, VmActionItemReply,
    VmCreateReply, VmCreateItemReply,
    VmDeleteReply, VmDeleteItemReply,
    VmCloneReply, VmCloneItemReply,
    VmListUsageReply, VmListUsageItemReply,
)
from app.schemas.debug import DebugPingReply


class TestDebugPingReply:
    """Test DebugPingReply -- one of the few reply schemas with log_multiline."""

    def test_valid_reply(self):
        reply = DebugPingReply(rc=0, log_multiline=["PLAY RECAP", "ok=1"])
        assert reply.rc == 0
        assert len(reply.log_multiline) == 2

    def test_missing_rc_raises(self):
        with pytest.raises(ValidationError):
            DebugPingReply(log_multiline=["line"])

    def test_missing_log_multiline_raises(self):
        with pytest.raises(ValidationError):
            DebugPingReply(rc=0)


class TestVmActionReply:
    """Test VmActionReply with properly structured result items."""

    def test_valid_reply(self):
        item = VmActionItemReply(
            action="vm_start",
            source="proxmox",
            proxmox_node="pve01",
            vm_id="100",
            vm_name="test-vm",
        )
        reply = VmActionReply(rc=0, result=[item])
        assert reply.rc == 0
        assert len(reply.result) == 1
        assert reply.result[0].action == "vm_start"

    def test_invalid_action_raises(self):
        with pytest.raises(ValidationError):
            VmActionItemReply(
                action="invalid_action",
                source="proxmox",
                proxmox_node="pve01",
                vm_id="100",
                vm_name="test-vm",
            )


class TestVmCreateReply:
    """Test VmCreateReply schema."""

    def test_valid_reply(self):
        item = VmCreateItemReply(
            action="vm_create",
            source="proxmox",
            proxmox_node="pve01",
            vm_id=100,
            vm_name="new-vm",
            vm_cpu="host",
            vm_cores=2,
            vm_sockets=1,
            vm_memory=2048,
            vm_net0="virtio,bridge=vmbr0",
            vm_scsi0="local-lvm:32,format=raw",
            raw_data="UPID:pve01:...",
        )
        reply = VmCreateReply(rc=0, result=[item])
        assert reply.rc == 0


class TestVmDeleteReply:
    """Test VmDeleteReply schema."""

    def test_valid_reply(self):
        item = VmDeleteItemReply(
            action="vm_delete",
            source="proxmox",
            proxmox_node="pve01",
            vm_id=100,
            vm_name="old-vm",
            raw_data="UPID:pve01:...",
        )
        reply = VmDeleteReply(rc=0, result=[item])
        assert reply.rc == 0


class TestVmCloneReply:
    """Test VmCloneReply schema."""

    def test_valid_reply(self):
        item = VmCloneItemReply(
            action="vm_clone",
            source="proxmox",
            proxmox_node="pve01",
            vm_id=200,
            vm_id_clone_from=100,
            vm_name="cloned-vm",
            vm_description="A clone",
            raw_info="UPID:pve01:...",
        )
        reply = VmCloneReply(rc=0, result=[item])
        assert reply.rc == 0


class TestVmListUsageReply:
    """Test VmListUsageReply schema."""

    def test_valid_reply(self):
        item = VmListUsageItemReply(
            action="vm_list_usage",
            source="proxmox",
            proxmox_node="pve01",
            vm_id=100,
            vm_name="test-vm",
            cpu_allocated=2,
            cpu_current_usage=10,
            disk_current_usage=5000000,
            disk_max=34359738368,
            disk_read=1000,
            disk_write=500,
            net_in=280531583,
            net_out=6330590,
            ram_current_usage=1910544625,
            ram_max=4294967296,
            vm_status="running",
            vm_uptime=79940,
        )
        reply = VmListUsageReply(rc=0, result=[item])
        assert reply.rc == 0
        assert reply.result[0].vm_name == "test-vm"
