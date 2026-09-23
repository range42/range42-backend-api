# SDN inventory for scenario planning

The authenticated v1 API exposes read-only configuration from a registered Proxmox host:

- `GET /v1/proxmox/hosts/{host_id}/sdn/zones`
- `GET /v1/proxmox/hosts/{host_id}/sdn/vnets`
- `GET /v1/proxmox/hosts/{host_id}/sdn/vnets/{vnet}/subnets`

All routes accept `view=running` (default) or `view=pending`, plus `offset` and `limit` (1–500). Responses include `items`, `total`, `offset`, `limit`, `view` and `visibility: credential_filtered`. An empty response describes only what the registered PVE credential can see. It is not proof that no cluster networks exist.

The API requests the matching native PVE view, whitelists configuration fields and reports pending state. Malformed, duplicate, oversized or unreadable upstream inventories fail with `SDN_INVENTORY_UNAVAILABLE`; raw PVE responses and credentials are excluded. Named viewers and operators may read these routes. Host registration remains an administrator action.

The Scenario editor explicitly loads the pending view to identify unapplied changes. It can copy a settled Simple zone and one IPv4 subnet's VNet, gateway and outbound NAT setting into a selected draft network. Zone node restrictions must include the registered host's node. Replication permits copying only into shared networks. A saved allocation pins the host when it belongs to the current backend. Backend/credential changes invalidate loaded selections.

Copying does not apply SDN, reserve an address, change guest addresses, adopt ownership or prove forwarding. Users still review the generated scenario and choose the same deployment host. Existing deployment preflight rechecks pending changes, network conflicts, target compatibility and the installed bundle contract. No Hyde playbook/controller implementation is changed.

This provides the supported v1 inventory equivalent of the old zone-discovery request in issue #50. It does not revive v0 execution or add zone creation/deletion, aliases, datacenter/node firewall management, arbitrary SDN types or cross-host orchestration.

Native contracts: [PVE zones](https://github.com/proxmox/pve-network/blob/master/src/PVE/API2/Network/SDN/Zones.pm), [VNets](https://github.com/proxmox/pve-network/blob/master/src/PVE/API2/Network/SDN/Vnets.pm), [subnets](https://github.com/proxmox/pve-network/blob/master/src/PVE/API2/Network/SDN/Subnets.pm).
