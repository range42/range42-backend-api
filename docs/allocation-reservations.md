# Stable NIC identities in authoring reservations

`POST /v1/proxmox/hosts/{host_id}/reservations` accepts an optional `nic_key`
on each VM NIC and returns that key with the allocated address. Use the stable
source canvas edge key, containing 1–128 ASCII letters, digits, dots, underscores
or hyphens. Keys must be unique within a VM and supplied for all its NICs or none.
The same key may be used by different VM instances.

`index` still determines the concrete interface position. Once a lease records
keys, renewal matches the previous address by key instead of index. Adding a new
secondary NIC or reordering existing NICs therefore retains the surviving NICs'
addresses. VM identity continues to use `node_id`; expanded scenarios can use
their stable VM instance keys for that field.

Legacy clients and unkeyed leases keep the existing positional behavior. When
adding keys to a legacy lease, the allocator can identify an old NIC by its
unchanged network only when one unmatched old NIC and one unresolved requested
NIC remain on that network. Explicit requested IPs can disambiguate parallel
interfaces. Ambiguous adoption returns `ALLOCATION_NIC_IDENTITY_REQUIRED` and
preserves the old lease. An established keyed lease cannot silently revert to
positional renewal by dropping its keys.

Keys do not bypass availability checks. A changed network ID, bridge or subnet
does not implicitly retain an old automatic address. A retained address that has
become a gateway, reservation or observed occupied address rejects the renewal;
the previous lease remains intact for review. Manual addresses still undergo the
same subnet and collision validation.

Keys are stored inside the existing assignments JSON; no database migration is
required. They are public authored identifiers, not ownership tokens. The
reservation token remains separate and must never be published to project Git.

These are expiring draft reservations. [Deployment handoff](deployment-allocations.md)
can consume a reviewed lease into a separate nonexpiring deployment record.
Stable NIC keys alone do not add contiguous scenario blocks, optimistic edit concurrency,
deployed-VM reuse, grow/resume or deletion authorization. The installation still
rechecks visible and hidden VM occupancy and installed scenario reservations;
external Proxmox writers and unobserved guest/DHCP addresses remain outside its
coordination boundary.
