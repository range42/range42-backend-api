# Shared native SDN acceptance — 23 September 2026

This qualifies the paired application branches against the selected
`pve01-dev_deployer_ui_lab` context. Only backend API and deployer UI source was
changed. The reviewed upstream native sources were installed unchanged.

## Installed revisions

| Component | Revision |
| --- | --- |
| Backend API | `4e70f1358c7ef5dedab122e8443b3e6bf9abbe86` |
| Deployer UI | `86e1499fce7cf1920d4d939f63c70072af7630df` |
| Playbooks | `6dcf31b5b53600f41be1ce7553d489dba4ad803b` |
| Proxmox controller | `617b57cddecbe4ddedd1f72becc7023bbcdf2114` |
| Core roles | `3ba5d6bd42c08c181cc7f726fb57be979fbd8f0f` |
| Catalog | `7f40f5c918e3d0ae92a582bd880a37bbb0574ac0` |

Native contract: `native-sdn-20260921`. Runtime fingerprint:
`321e0528bcbacd66ac2752ea9a579f1f2153beeedf84134bf6e7772ac399deeb`.
Context code-base devkit reference: `ce77237201b43b6d5b14d1f76e1a91aba70dc0e3`
(local development checkout: `61941035a9d51dd6394a6d19b06d24f68f3434b5`).
Devkit was not executed in this API-owned workflow and is not qualified by this run.

The managed backend update retained its credential files, schema
`0008_audit_records`, read-only root, UID 1000 and existing state. The updater's
admission/drain, backup, readiness and rollback guards remained enabled. Before
updating, the old installation record needed a metadata-only normalization of
Docker's three DNS arrays from null to empty lists, after exact container/image,
release and credential verification. The original record was retained.

The UI runs in its existing Docker Compose service. Its runtime backend URL,
node configuration and existing canvas changes were preserved. Historical host
systemd units and stale host release files did not drive the rollout.

## Fixture and completed deployment

The actual served UI reviewed a two-guest concrete scenario with a single SDN
NIC per guest, template-inherited CPU/RAM/disk, explicit cloud-init user/DNS and
an uploaded marker file. Thirteen generated files were saved to a dedicated
branch of an existing private acceptance repository. The project revision was
`4e2242ef1f6af243041c0fe83f7879f0efa44859`; later branch changes cannot retarget
that pinned full deployment.

Deployment `c723a4bfb3014757` owns VMIDs 60020/60021 and VNet `r42a923` in the
existing shared zone `r42smoke`, subnet `10.42.73.0/28`. Its VNet ownership alias
was created by reviewed lifecycle attempt `586cd66723454ae5`.
Full attempt `eea105c214844e0e` succeeded with rc 0. Both guests received the first
marker revision, accepted SSH, reached each other's SSH port and opened fresh
TCP connections to an external HTTPS endpoint.

The baseline contained 48 unrelated guests. After provisioning there were 50;
all original guest identities/statuses, template configuration, host/DC firewall
options and unrelated raw POSTROUTING rules were preserved.

## Integration fixes found by this run

- Accept supported Docker NAT rules with negated egress while rejecting mixed
  raw rule shapes for the same source. Compare source CIDRs without the Jinja
  regex-backreference ambiguity.
- Put the temporary SSH askpass helper inside the private owned agent directory;
  the managed container mounts `/tmp` with `noexec`. Helper and agent cleanup
  remain tied to attempt completion.
- Normalize unordered Proxmox inventory collections by stable identities before
  reviewing and comparing hashes. A response row reorder must not invalidate an
  otherwise identical plan; address/ownership/attachment changes still do.
- Expose the pinned scenario and workspace vault through each full/configure/
  teardown attempt's isolated native config directory. A stale or absent
  workspace manifest must not select native firewall targets.
- Bypass GitHub's 60-second HTTP cache for branch, editor lease and review reads.
  A real Chromium regression reproduced the old branch/lock response after a
  server change. The corrected provider sees fresh values, and CI now executes
  that regression with browser caching enabled.

- Drain once more when runner exit occurs during the event watcher's awaited
  progress flush. A deterministic regression verifies that final events are
  redacted and durably recorded before raw output is removed.

The initial SSH/manifest/review failures remain in attempt history. They were
inspected before retrying; no failed request was blindly resubmitted.

## Validation

- Final API regression suite: 1,612 passed, 14 opt-in skips. The pinned native
  contract/context/lifecycle run passed all 20 cases, covering nine of those
  opt-in skips. All three Docker checks passed separately, including encrypted
  key unlock with read-only root and `noexec` `/tmp`. The reviewed cloud-init/clone
  consumer checks also passed before the final watcher-only change. Exact
  installed API head passed both CI workflows.
- Native manifest regression: full, configure and teardown execute the reviewed
  native report unchanged against inert PVE calls, select the pinned manifest
  over stale workspace metadata and retain the existing vault.
- Concurrent deployment after ten minutes was refused with
  `ATTEMPT_IN_PROGRESS`; heartbeat age was under 30 seconds and no new attempt
  was reserved. All nonempty registered Git and Proxmox DB credentials were
  encrypted. Unauthenticated deployment, SSE, reports and host requests returned
  401.
- Protected default VMIDs and host-specific protected ranges are refused before
  teardown can construct a runner or unlock SSH keys.
- Attached VNet deletion returned `SDN_NETWORK_ATTACHED`. Unknown host/DC switch
  values prevented a host-wide firewall plan. Stale policy reviews and referenced
  alias rename/delete were refused without reserving another attempt.

## Policy acceptance

All owned VM policy operations completed with rc 0 and verified desired readback:
create two rules, update while clearing an old source restriction, reorder,
delete both rules, then rename the now-unreferenced alias. The original SSH rule
chain was restored. Alias create and delete were reviewed and acknowledged in
the actual UI. The deletion returned HTTP 201 without browser page errors and
completed successfully as attempt `3979f1e11b164e45`.

The create observer originally used Playwright's default 30-second response wait;
it expired during backend preparation. Attempt `50cfec96cc1b4707` was recovered
from history and verified successful, rather than submitting another create.
The subsequent browser check used a longer observation timeout.

## NAT traffic acceptance

NAT disable (`fbb088f5a40e4138`) and enable (`db627459f8ff4d2f`) both succeeded.
Each guest lost fresh external TCP/443 connectivity with NAT disabled and regained
it after enable; peer SSH remained reachable in both states. Raw selected-source
NAT counts changed 1 → 0 → 1. Both snapshots preserved every unrelated raw rule,
original guest, template and host/DC switch setting.

## Guest switches, content and Git

Single-VM enable (`18a447f58ce9430d`), scenario enable (`6c67ff32ccd04053`),
scenario disable (`94f8bb28ceef46df`) and read-only native observation
(`2312e74263a34878`) succeeded with verified readback. Guest switches and NIC
flags were checked; shared host/DC settings stayed unchanged. Peer SSH and
outbound HTTPS still passed after the guest switch cycle.

Configure attempt `38ea9e9f96df4e4b` applied only the marker's second revision
from commit `78889e129403c73eb89c8b992c123e3005b70576`. Both existing guests
returned the new content. VM/network manifests and inventory stayed unchanged.
Owned teardown `d2ccfc9919f84e2b` then succeeded; the guest count returned to 48.

The corrected served UI explicitly recovered the expired lease from its failed
save, saved the project as `b68e8bd0c407f345bf819f1f8a06da90c5f7abfa`, and saved
the sanitized alias-deletion result as `fb4964015a07222d1422666be7d7525851f9239a`.
The runtime commit changes only its result record and the project file index.
No browser page errors occurred. The browser was then closed; its last persisted
editor lease expired without a further content change.

The corrected UI passed 1,927 unit tests (10 skipped), migrated type checks,
production build, 109 focused Git cases and the actual-cache browser regression.
Two catalog waits timed out in a heavily concurrent local run; both files and
the full suite passed when rerun with bounded workers and no concurrent build.

## Cleanup and final watcher qualification

Owned network deletion `02e24da40b4b463e` succeeded. The owned subnet, VNet and
NAT rule are absent, and the committed allocation was explicitly released
(subsequent GET returns 404). The 48 original guests, both unrelated VNets,
shared zone, template, host/DC switches and every unrelated raw NAT rule match
the baseline.

The full, configure and teardown cycle ran on API `69ac8cb`. Final inspection
found two unprocessed trailing events from scenario enable `6c67ff32ccd04053`;
cleanup had correctly retained their raw output. API `4e70f13` fixes the watcher
race and was then installed through the same managed updater. For the historical
attempt, the exact two file hashes and successful terminal state were verified
before writing sanitized receipts: assertion success and recap completion only,
with raw text withheld. Normal guarded cleanup then removed both raw event files
and stdout. No resource operation was repeated to repair history.

Read-only native observation `f5664018307c457a` succeeded on the corrected image.
Its HTTP observer expired during preparation; the accepted attempt was recovered
from history without resubmission. Final checks found no active attempt, workspace
lock, owned SSH agent directory or temporary credential/raw-output files. Both
guests and the network remain absent; a fresh PVE snapshot still matches the
preservation baseline. Health and authenticated readiness pass.

The application PR stacks remain unmerged. This acceptance record does not
replace review, branch integration or release promotion.

## Coverage boundary

This is one single-node context. Host/DC switches were not changed, and no
multi-node lifecycle or guest firewall enforcement claim is made. Reports retain
`traffic_verified: false`: external traffic probes are separate evidence from
configuration and native rule observations. Raw private captures, credentials,
reservation tokens and guest configuration dumps are not published.

The final served UI loaded the runtime report at 1280px and 390px without page
errors or write requests; both panels fit their viewport. Shared report reads
were slow during concurrent acceptance activity (one measured 83 seconds); this
run verifies correctness, not a response-time target.
