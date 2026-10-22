# Prepare resources for any native scenario

Use the normal Catalog → Project → Deploy → Overview flow. Reusable templates,
resource ownership and environment networking are platform capabilities; an
installation workshop is one recipe that consumes them. The native CLI and UI
continue to use the same saved playbooks and controller. This adds no allocator,
worker, credential store or replacement installer.

## Register resource bounds

In Settings → Native provisioning environments, select **Custom template set**.
Choose the trusted runtime and target, then record its guest VMID ranges, storage,
network pool and named templates. Each template declares its destination VMID,
configured memory, vCPUs and SCSI disk sizes. Review, then save the environment.
A hardware or credential edit requires a fresh review.

The equivalent administrator API input to `POST /v1/environments/review` is:

```json
{
  "label": "Monitoring",
  "base_context_id": "trusted-runtime",
  "target_host_id": "monitoring-target",
  "provisioning_profile": {
    "version": 1,
    "id": "monitoring",
    "vmid_ranges": [[46000, 46015]],
    "storage": "ceph-range",
    "templates": {
      "sensor": {"vm_id": 9250, "memory_mib": 2048, "cores": 1, "disks_gib": [24]},
      "collector": {"vm_id": 9251, "memory_mib": 8192, "cores": 3, "disks_gib": [48, 96]}
    },
    "networks": []
  }
}
```

Save by posting the same input plus the returned `review_revision` to
`POST /v1/environments`. Existing environment records retain their original
profile and review digest. The legacy `workshop_profile` field remains readable;
new callers must select exactly one profile representation. The generic profile
has no participant, Keycloak, Headscale or installation-product fields.

The installation workshop recipe also consumes this profile when its named
`proxmox` and `deployer` templates match the recipe’s reviewed hardware. A
recipe adapter derives its existing template fields without changing registration
or inventing a second profile. Different hardware is refused instead of silently
replacing the operator’s choices.

## Prepare the approved templates

Open the `template_preparation` catalog recipe. Its
`manifest/preparation.json` declares version `1`, kind
`native-template-preparation` and a title. It has no fixed VMIDs or networks.
The native deployment's existing `foundation` input contains a namespace,
source node and a `sources` mapping from every approved template role to its
original source VMID. Destination IDs, storage and hardware remain server-owned.

The existing `/native-scenario/foundation/preview` and `/reservations` project
routes compile this template set and reserve its exact NIC-less outputs through
the Core allocator. The private reservation header and the original physical
capacity hold are unchanged. Overview retains the preparation envelope before
any dispatch. The existing native attempt drives the optional bounded node module
and reconciles its original generation and volume proofs. Partial or uncertain
operations keep their full allocation and capacity commitment.

The node administrator must enroll the same exact source generations,
configuration revisions, destination IDs and hardware. The current implementation
supports one to sixteen templates, each with one or two SCSI disks and a 4 MiB
cloud-init volume, on one approved source node. Sources must be stopped with CPU
`host` and their guest agent enabled. Unsupported shapes are refused rather than
converted implicitly. This capability does not start guests or retire templates.

## Optional network and access providers

Environment bridge planning uses its approved resource profile, not the scenario's
purpose. The current bounded isolated-bridge provider supports approved private
/28s and a separate /29 management network. It requires independent node enrollment
and retains environment ownership outside individual deployment claims. Its
network journal is still attached to a template preparation deployment; standalone
network-only preparation is not yet implemented. Native SDN scenarios continue
through their existing network lifecycle; this provider is not imposed on them.

Gateway configuration, VM materialization and retirement still need independent
implementation and live acceptance. Shared gateway identity types are outside the
workshop module, but identity types alone do not implement a gateway lifecycle.
VPN enrollment, participant accounts and application admission remain optional
integration capabilities and are not prerequisites for resource preparation.

Registering an environment enables planning only. Generic profiles preserve the
same execution gate as legacy registrations; a profile or capacity acceptance does
not qualify arbitrary guest execution.
