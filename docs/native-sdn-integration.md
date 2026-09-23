# Native SDN application integration

The backend and UI adapt the native SDN release without changing core, playbooks,
controller or devkit sources. Native recognition is in `app/core/native_sdn.py`;
the existing installation dependency manifest still verifies the whole runtime.

The reviewed native VM bootstrap supports its single management NIC and template
resource sizes. It does not consume `global_vm_extra_config` or `global_vm_disk`.
The authoring capability endpoint reports no `extra_nics`, `resources` or
`disk_resize` support for that release. Preflight refuses those combinations;
the editor retains the requested values and offers explicit template inheritance.
This change does not implement a new VM provisioner.

## Reviewed firewall stages

UI scenarios that select native firewall preparation include
`manifest/scenario_firewall.json`:

```json
{"version":1,"prepare_management_access":false,"arm_vms":false,"ssh_sources":null}
```

The sequence is status and optional shared management accepts; SDN bootstrap;
VM bootstrap; ownership-checked guest SSH declaration; guest configuration;
optional ownership-checked guest arming; final status. Guest arming defaults to
NO, pinned from the saved policy at Ansible extra-vars precedence, including when
the workspace vault says YES. Configure-only attempts do not run those stages.
Existing scenarios without the policy retain their previous execution path.

`ssh_sources: null` inherits `range42_fw_vm_ssh_sources` from the existing private
workspace vault; an explicit empty list selects unrestricted SSH, and a nonempty
list permits up to 64 distinct canonical IPv4 /32 addresses. The app adds the
exact networks of every manifest NIC. It calls the native per-VM SSH declaration
primitive rather than the native /24 manifest sweep, so it never fabricates
template IPs or rounds a /23 or /25 to /24. Inherited sources are checked before
the first mutation. Existing broader rules remain; this is additive preparation,
not proof that a restrictive policy has replaced existing rules.

Shared node/DC management accepts require both the reviewed policy opt-in and
administrator setting `RANGE42_SCENARIO_MANAGEMENT_ACCESS=1`. The default is off.
These accepts do not arm host/DC switches. Deployment operators remain trusted
authors of Ansible; this contract does not sandbox arbitrary project code.

## Verification and remaining acceptance

`tests/core/test_native_sdn_contract.py` exercises recognized and shadowed source
trees and native completion readback. Set `RANGE42_NATIVE_CONTRACT_FIXTURE` to an
export containing `playbooks/bundles` and `controller/roles` at the pinned commits
to run the exact-source cases. Synthetic source-tree cases always run in CI.

`tests/overlay/test_native_firewall_scenario.py` imports the paired UI's actual
generator and executes its generated guards and expressions through Ansible.
Only PVE reads and native mutation boundaries are simulated. It covers default
NO versus vault YES, restricted /23 sources, foreign ownership, reassignment
after configuration and malformed inherited sources. Both paired schema CI jobs
run these checks. Desktop/mobile browser coverage is in the UI's
`e2e/authoring-controls.spec.ts`.

Shared range42-context guest deployment and end-to-end traffic acceptance remain
the release gate in API #74. Rule counts and switch observations do not prove
forwarding or filtering. The application now implements reviewed SDN create/apply/
delete (#41), host/DC and alias-policy workflows (#83), typed declared/live reports
(#140), and paired UI controls (#101). See [scoped runtime operations](scoped-runtime-operations.md)
for the eight supported kinds, ownership restrictions and remaining live acceptance.
