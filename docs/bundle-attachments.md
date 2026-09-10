# Executable VM bundle attachments

The library resolves a pinned source bundle against an installed runtime release. It does not copy an arbitrary bundle into the server's public bundle directory. The selected bundle directory must match the installed directory byte for byte, including executable bits and contained symbolic links. Its source repository URL follows the normal approved-host policy and the resolver fetches the exact requested commit.

`POST /v1/catalog/sources/{source_id}/bundles/resolve` accepts:

```json
{"path":"bundles/ctf/cve/web/apache/CVE-2021-42013","sha":"<full commit SHA>","target_kind":"VM"}
```

The result contains `source_id`, `source_sha`, `path`, an `entrypoint` relative to `RANGE42_BUNDLE_DIR`, `bundle_kind`, descriptor `params`, managed `target_vars`, `proof_kind: "content_match"`, and `runtime: {fingerprint, dependencies, proof}`. Dependency records expose component name, revision and SHA-256, without server paths. They describe the **installed dependency profile**, not a claim that all upstream dependencies are identical to the selected source's original environment.

The source must identify exactly one repository and use public HTTPS or PAT authentication. Git checkout and filesystem verification run in a worker; cancellation does not orphan the checkout. Source paths are bounded, bundle directories are limited to 512 files and 16 MiB, and escaping links are rejected.

## Runtime installation

Set `RANGE42_BUNDLE_RUNTIME_MANIFEST=/opt/range42/bundle-runtime.json`. Generate the profile under the same environment as the API service after installing the release:

```sh
python -m app.core.bundle_runtime \
  --output /opt/range42/bundle-runtime.json \
  --component playbooks=/opt/range42/range42-playbooks \
  --component controller=/opt/range42/range42-ansible_roles-proxmox_controller \
  --component catalog=/opt/range42/range42-catalog \
  --component range42=/opt/range42/range42 \
  --component collections=/opt/range42/collections
```

If `ANSIBLE_CONFIG` points outside those trees, declare its file as another component. The generator records `.range42-revision` when present and hashes actual source bytes regardless of Git metadata availability. It captures `RANGE42_BUNDLE_DIR`, configured Ansible role/collection/filter/module/config paths, and `RANGE42_INVENTORY__*` / `RANGE42_GITDIR__*` dependency paths. Referenced paths must exist and belong to the declared profile. Runtime checks recompute hashes and compare the environment; missing profiles or drift fail with `BUNDLE_RUNTIME_UNAVAILABLE`.

For the shared catalog, CTF content requires:

```sh
RANGE42_INVENTORY__DOCKER__CTF=/opt/range42/range42-catalog/03_container_layer/docker/_ctf
```

Install component trees read-only for the API service and change releases while no attempts run. Profile checks do not make concurrent operator edits atomic. Source hashes ignore `.git` and interpreter/test/lint cache directories; externally downloaded packages, container tags and remote repositories remain dependencies of each bundle's implementation. They are not made reproducible by this profile. Components are bounded to 100,000 files and 1 GiB each.

## Saved scenario contract

The UI stores a VM attachment's complete resolution and caller parameters. The emitter writes `manifest/scenario_bundles.json`:

```json
{
  "version": 1,
  "attachments": [{
    "vm_id": 3191,
    "inventory_host": "guest-one",
    "resolution": {"...": "complete resolver response"},
    "parameters": {"SEND_POC_DIR": "NO"}
  }]
}
```

`configure.yml` imports the verified entrypoint using `{{ lookup('env', 'RANGE42_BUNDLE_DIR') }}/<entrypoint>`. Its variables must exactly match the saved caller parameters plus managed target variables. The validator binds VMID to manifest VM name and to the inventory host's IP. It rejects duplicate VM/bundle attachments, missing or extra bundle imports, changes to import order, additional import control fields and variable mismatches. The manifest accepts at most 64 attachments and must fit within 2 MiB.

The backend checks the proof during both preflight and launch for full/configure operations. Teardown remains available if an old runtime changed. Proofs seal the source, path, entrypoint, scope, parameter declarations, target variables and installed dependency fingerprint using the backend credential key. They contain no credential values and can be saved to Git. Key rotation, moving to another runtime/key or changing installed dependency content requires resolving attachments again.

Parameter values remain typed: string/str, integer, boolean, list or dict. `bool_style: "yesno"` requires uppercase `YES` or `NO`; other booleans require JSON booleans. `allowed` choices take precedence over `enum`. Required inputs without defaults must be supplied. Target variables and `from_vault` values cannot be supplied as caller parameters. Jinja expressions in caller values are rejected. Required vault secrets and external application prerequisites still belong to workspace/bundle provisioning; a successful resolution does not assert that every guest prerequisite exists.

## Supported scope and validation

This first executable slice accepts bundles whose plays target one recognized VM variable. Group, infrastructure, mixed and unknown scopes are rejected. Playbook imports, sibling bundle lookups and dynamic task/environment/role dependencies are rejected until their complete source dependency closure can be verified. Broad repository-parent environment lookups are also rejected unless their entire reachable directory is an explicit hashed component. Literal task imports inside the selected bundle and installed static role dependencies are supported.

The shared operator remains a trusted author of Ansible code; this is a provenance and generated-attachment contract, not a sandbox for arbitrary project playbooks. Non-bundle scripts, files and authored playbooks continue through their existing paths.

Validation: 43 focused core/HTTP cases cover real pinned Git fetch, nonblocking resolution, source/runtime mismatch, content and environment drift, proof forgery, target binding, descriptor types, malformed manifests and import scope. An archive-only check against the integration repositories resolved 18 VM bundles (16 CTF bundles plus Kong and Wazuh). Two deployer-install bundles lacked their broader code-root environment and were explicitly rejected. This check did not execute those bundles on a hypervisor.
