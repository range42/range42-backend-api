"""Tests for consolidated schema modules — validates fields, patterns, and backward-compat aliases."""

import pytest
from pydantic import ValidationError

# ===========================================================================
# vms.py
# ===========================================================================


def test_vm_list_request():
    from app.schemas.vms import VmListRequest

    req = VmListRequest(proxmox_node="px-testing")
    assert req.proxmox_node == "px-testing"
    assert req.as_json is True


def test_vm_action_request_validates_vm_id():
    from app.schemas.vms import VmActionRequest

    req = VmActionRequest(proxmox_node="px-testing", vm_id="100")
    assert req.vm_id == "100"


def test_vm_action_request_rejects_invalid_vm_id():
    from app.schemas.vms import VmActionRequest

    with pytest.raises(ValidationError):
        VmActionRequest(proxmox_node="px-testing", vm_id="abc")


def test_vm_create_request():
    from app.schemas.vms import VmCreateRequest

    req = VmCreateRequest(
        proxmox_node="px-testing",
        vm_id="200",
        vm_name="test-vm",
        vm_cpu="host",
        vm_cores=2,
        vm_sockets=1,
        vm_memory=2048,
    )
    assert req.vm_name == "test-vm"


def test_vm_create_request_rejects_low_memory():
    from app.schemas.vms import VmCreateRequest

    with pytest.raises(ValidationError):
        VmCreateRequest(
            proxmox_node="px-testing",
            vm_id="200",
            vm_name="test-vm",
            vm_cpu="host",
            vm_cores=2,
            vm_sockets=1,
            vm_memory=64,  # below ge=128
        )


def test_proxmox_node_rejects_special_chars():
    from app.schemas.vms import VmListRequest

    with pytest.raises(ValidationError):
        VmListRequest(proxmox_node="node; rm -rf /")


def test_vm_delete_request():
    from app.schemas.vms import VmDeleteRequest

    req = VmDeleteRequest(proxmox_node="px-testing", vm_id="1111")
    assert req.vm_id == "1111"


def test_vm_clone_request():
    from app.schemas.vms import VmCloneRequest

    req = VmCloneRequest(
        proxmox_node="px-testing",
        vm_id="2000",
        vm_new_id="3000",
        vm_name="test-cloned",
    )
    assert req.vm_new_id == "3000"
    assert req.vm_description == "cloned-vm"  # default


# ===========================================================================
# vm_config.py
# ===========================================================================


def test_vm_get_config_request():
    from app.schemas.vm_config import VmGetConfigRequest

    req = VmGetConfigRequest(proxmox_node="px-testing", vm_id="1000")
    assert req.as_json is True


def test_vm_set_tag_request():
    from app.schemas.vm_config import VmSetTagRequest

    req = VmSetTagRequest(
        proxmox_node="px-testing",
        vm_id="1111",
        vm_tag_name="group_01,group_02",
    )
    assert "group_01" in req.vm_tag_name


def test_vm_set_tag_rejects_bad_pattern():
    from app.schemas.vm_config import VmSetTagRequest

    with pytest.raises(ValidationError):
        VmSetTagRequest(
            proxmox_node="px-testing",
            vm_id="1111",
            vm_tag_name="tag;evil",
        )


# ===========================================================================
# snapshots.py
# ===========================================================================


def test_snapshot_create_request():
    from app.schemas.snapshots import SnapshotCreateRequest

    req = SnapshotCreateRequest(
        proxmox_node="px-testing",
        vm_id="1000",
        vm_snapshot_name="MY_SNAP",
    )
    assert req.vm_snapshot_name == "MY_SNAP"


def test_snapshot_list_request():
    from app.schemas.snapshots import SnapshotListRequest

    req = SnapshotListRequest(proxmox_node="px-testing", vm_id="1000")
    assert req.vm_id == "1000"


# ===========================================================================
# firewall.py
# ===========================================================================


def test_firewall_rule_request():
    from app.schemas.firewall import FirewallRuleApplyRequest

    req = FirewallRuleApplyRequest(
        proxmox_node="px-testing",
        vm_id="100",
        vm_fw_action="ACCEPT",
        vm_fw_type="in",
        vm_fw_proto="tcp",
        vm_fw_dport="22",
        vm_fw_enable=1,
    )
    assert req.vm_fw_action == "ACCEPT"


def test_firewall_rule_rejects_invalid_action():
    from app.schemas.firewall import FirewallRuleApplyRequest

    with pytest.raises(ValidationError):
        FirewallRuleApplyRequest(
            proxmox_node="px-testing",
            vm_id="100",
            vm_fw_action="ALLOW",  # invalid — must be ACCEPT|DROP|REJECT
            vm_fw_type="in",
            vm_fw_proto="tcp",
            vm_fw_dport="22",
            vm_fw_enable=1,
        )


def test_firewall_alias_add_request():
    from app.schemas.firewall import FirewallAliasAddRequest

    req = FirewallAliasAddRequest(
        proxmox_node="px-testing",
        vm_id="1000",
        vm_fw_alias_name="test",
        vm_fw_alias_cidr="192.168.123.0/24",
        vm_fw_alias_comment="this_comment",
    )
    assert req.vm_fw_alias_cidr == "192.168.123.0/24"


# ===========================================================================
# network.py
# ===========================================================================


def test_node_network_list_request():
    from app.schemas.network import NodeNetworkListRequest

    req = NodeNetworkListRequest(proxmox_node="px-testing")
    assert req.as_json is True


def test_vm_network_list_request():
    from app.schemas.network import VmNetworkListRequest

    req = VmNetworkListRequest(proxmox_node="px-testing", vm_id="1001")
    assert req.vm_id == "1001"


# ===========================================================================
# storage.py
# ===========================================================================


def test_storage_list_request():
    from app.schemas.storage import StorageListRequest

    req = StorageListRequest(
        proxmox_node="px-testing",
        storage_name="local",
    )
    assert req.storage_name == "local"


def test_storage_download_iso_request():
    from app.schemas.storage import StorageDownloadIsoRequest

    req = StorageDownloadIsoRequest(
        proxmox_node="px-testing",
        proxmox_storage="local",
        iso_file_content_type="iso",
        iso_file_name="ubuntu-24.04-live-server-amd64.iso",
        iso_url="https://releases.ubuntu.com/24.04/ubuntu-24.04-live-server-amd64.iso",
    )
    assert req.iso_file_name.endswith(".iso")


# ===========================================================================
# debug.py
# ===========================================================================


def test_debug_ping_request():
    from app.schemas.debug import DebugPingRequest

    req = DebugPingRequest(proxmox_node="px-testing", hosts="all")
    assert req.as_json is False  # default


# ===========================================================================
# base.py — ProxmoxBaseRequest
# ===========================================================================


def test_proxmox_base_request():
    from app.schemas.base import ProxmoxBaseRequest

    req = ProxmoxBaseRequest(proxmox_node="px-testing")
    assert req.as_json is True


def test_proxmox_base_request_rejects_bad_node():
    from app.schemas.base import ProxmoxBaseRequest

    with pytest.raises(ValidationError):
        ProxmoxBaseRequest(proxmox_node="node; rm -rf /")


# ===========================================================================
# Backward-compatibility aliases
# ===========================================================================


def test_backward_compat_aliases_vms():
    """Old class names must still be importable."""
    from app.schemas.vms import Request_ProxmoxVms_VmList, VmListRequest

    assert Request_ProxmoxVms_VmList is VmListRequest

    from app.schemas.vms import Reply_ProxmoxVmList, VmListReply

    assert Reply_ProxmoxVmList is VmListReply

    from app.schemas.vms import Request_ProxmoxVmsVMID_Create, VmCreateRequest

    assert Request_ProxmoxVmsVMID_Create is VmCreateRequest

    from app.schemas.vms import (
        Request_ProxmoxVmsVMID_StartStopPauseResume,
        VmActionRequest,
    )

    assert Request_ProxmoxVmsVMID_StartStopPauseResume is VmActionRequest


def test_backward_compat_aliases_vm_config():
    from app.schemas.vm_config import (
        Request_ProxmoxVmsVMID_VmGetConfig,
        VmGetConfigRequest,
    )

    assert Request_ProxmoxVmsVMID_VmGetConfig is VmGetConfigRequest

    from app.schemas.vm_config import Request_ProxmoxVmsVMID_VmSetTag, VmSetTagRequest

    assert Request_ProxmoxVmsVMID_VmSetTag is VmSetTagRequest


def test_backward_compat_aliases_snapshots():
    from app.schemas.snapshots import (
        Request_ProxmoxVmsVMID_CreateSnapshot,
        SnapshotCreateRequest,
    )

    assert Request_ProxmoxVmsVMID_CreateSnapshot is SnapshotCreateRequest

    from app.schemas.snapshots import (
        Request_ProxmoxVmsVMID_RevertSnapshot,
        SnapshotRevertRequest,
    )

    assert Request_ProxmoxVmsVMID_RevertSnapshot is SnapshotRevertRequest


def test_backward_compat_aliases_firewall():
    from app.schemas.firewall import (
        FirewallRuleApplyRequest,
        Request_ProxmoxFirewall_ApplyIptablesRules,
    )

    assert Request_ProxmoxFirewall_ApplyIptablesRules is FirewallRuleApplyRequest

    from app.schemas.firewall import (
        FirewallEnableVmRequest,
        Request_ProxmoxFirewall_EnableFirewallVm,
    )

    assert Request_ProxmoxFirewall_EnableFirewallVm is FirewallEnableVmRequest


def test_backward_compat_aliases_network():
    from app.schemas.network import (
        NodeNetworkAddRequest,
        Request_ProxmoxNetwork_WithNodeName_AddNetworkInterface,
    )

    assert (
        Request_ProxmoxNetwork_WithNodeName_AddNetworkInterface is NodeNetworkAddRequest
    )

    from app.schemas.network import (
        Request_ProxmoxNetwork_WithVmId_ListNetwork,
        VmNetworkListRequest,
    )

    assert Request_ProxmoxNetwork_WithVmId_ListNetwork is VmNetworkListRequest


def test_backward_compat_aliases_storage():
    from app.schemas.storage import Request_ProxmoxStorage_List, StorageListRequest

    assert Request_ProxmoxStorage_List is StorageListRequest

    from app.schemas.storage import (
        Request_ProxmoxStorage_ListIso,
        StorageListIsoRequest,
    )

    assert Request_ProxmoxStorage_ListIso is StorageListIsoRequest


def test_backward_compat_aliases_debug():
    from app.schemas.debug import DebugPingRequest, Request_DebugPing

    assert Request_DebugPing is DebugPingRequest

    from app.schemas.debug import DebugPingReply, Reply_DebugPing

    assert Reply_DebugPing is DebugPingReply
