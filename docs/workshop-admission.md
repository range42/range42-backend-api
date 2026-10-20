# Durable legacy workshop queue admission

Administrator-only `GET /v1/admin/workshop/admission` reads the installed
controller boundary. `POST` changes it using strict desired input:

```json
{"action":"pause","expected_revision":0,"request_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
```

A resume requires the current revision, exact `pause_id`, another unique request
ID and `confirmation: "RESUME"`. Unknown fields, caller paths, commands, boolean
revisions and unsolicited confirmations are refused. Responses use `no-store`
and a typed projection with no credentials or raw worker errors.

The controller serializes admission and queue claims durably. Its states are
`open`, `draining` and `paused`, with full queued/running counts. Accepted work
drains, emergency `revoke`/`close` remains available, and a later pause cannot be
released with an older proof. Fixed refusal codes are `WORKSHOP_PAUSED`(423),
`WORKSHOP_ADMISSION_CHANGED`(409), and `WORKSHOP_DRAINING`(409).

The deployment Overview shows the control only for its associated legacy cohort.
Old controllers report no supported admission capability; the UI never infers an
effective pause from a local flag. SSH keeps the existing fixed alias/forced-key
scope with explicit strict host-key checking. No controller installation is
performed by this API change.

The scope is **legacy_controller_queue**. Direct deployment attempts, Proxmox
power/configuration routes and external hypervisor writers have independent
paths. A paused idle queue does not reserve RAM, freeze those writers or attest
that an interrupted remote operation ended. Physical rehearsal still requires
their enforcement, exact guest/task readback, persistent node identity and
durable capacity admission. This change does not enable a managed environment's
`execution_ready` flag or qualify infrastructure.

Run the focused route checks with
`python -m pytest -q tests/routes/test_workshop_admission.py tests/routes/test_workshop_management.py`.
The disposable browser test uses actual source API and verified SSH/dispatcher
with synthetic roster/job completion; it is not physical deployment acceptance.
