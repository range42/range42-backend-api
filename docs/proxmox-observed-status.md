# Guest run-state readback

`GET /v1/proxmox/hosts/{host_id}/vms/{vmid}/status?vmtype=qemu|lxc`
uses the registered host credentials and node to read Proxmox's guest
`status/current` endpoint. It returns only `vmid`, `node`, `type` and
`status` (`running`, `stopped`, `paused` or `unknown`). This is read-only,
uses normal API authentication, and has no status cache.

QEMU's process status alone cannot prove that the guest is running: its
process remains running while QMP reports `paused` or `suspended`. Those
states map to `paused`; `running` maps to `running`. A stopped process maps
to `stopped`. Missing or other QMP states map to `unknown`. LXC uses its
current container status. A denied or unsuccessful upstream read remains
an API error; it never becomes a successful `stopped` response.

The [Proxmox API schema](https://pve.proxmox.com/pve-docs/api-viewer/)
describes QMP status as optional in the QEMU list and current-status
responses. A bounded read-only check on 2026-09-14 found no QMP status in
any of the installed host's 48 list entries, while a running guest's
current-status response provided it. No guest state was changed.

The paired UI reads current status after successful lifecycle tasks. Its
visible editor refresh groups inventory reads by selected host and reads
current status for targeted running QEMU guests. It pauses while hidden,
refreshes on resume and discards responses after project, guest, host or
credential changes. These observations do not modify desired configuration
or create Git autosaves. A successful delete task remains the deletion
confirmation; an absent guest in a list alone is not deletion proof.
