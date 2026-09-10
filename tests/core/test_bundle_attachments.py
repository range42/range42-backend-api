"""Executable bundle attachments bind a pinned source to the installed release."""

import copy
import json

import pytest
import yaml

from app.core.errors import Range42Error


def write(root, path, content):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    from app.core.bundle_runtime import runtime_environment

    for key in runtime_environment():
        monkeypatch.delenv(key)
    runtime = tmp_path / "runtime"
    source = tmp_path / "source"
    path = "bundles/generic/software.install.demo"
    playbook = "- hosts: '{{ global_vm_ssh_name }}'\n  roles: [demo]\n"
    descriptor = {
        "params": [
            {"name": "global_vm_ssh_name", "type": "string", "required": True},
            {"name": "global_vm_ci_ip", "type": "string", "required": True},
            {"name": "ENABLED", "type": "bool", "required": False, "default": True},
            {"name": "COUNT", "type": "int", "required": True},
            {"name": "LABEL", "type": "string", "required": False},
            {
                "name": "vault_password",
                "type": "string",
                "required": True,
                "from_vault": True,
            },
        ]
    }
    for root in (runtime, source):
        write(root, f"{path}/main.yml", playbook)
        write(root, f"{path}/bundle_parameters.json", json.dumps(descriptor))
    write(
        runtime,
        "roles/demo/tasks/main.yml",
        "- ansible.builtin.debug:\n    msg: demo\n",
    )
    monkeypatch.setenv("RANGE42_BUNDLE_DIR", str(runtime / "bundles"))
    monkeypatch.setenv("ANSIBLE_ROLES_PATH", str(runtime / "roles"))
    profile = tmp_path / "runtime.json"
    monkeypatch.setenv("RANGE42_BUNDLE_RUNTIME_MANIFEST", str(profile))
    return {"runtime": runtime, "source": source, "path": path, "profile": profile}


def prepare(bundle):
    from app.core.bundle_runtime import build_runtime_manifest

    profile = build_runtime_manifest({"release": bundle["runtime"]})
    bundle["profile"].write_text(json.dumps(profile))
    return profile


def resolve(bundle):
    from app.core.bundle_attachments import resolve_bundle

    return resolve_bundle(
        bundle["source"], source_id="source1", sha="a" * 40, path=bundle["path"]
    )


def scenario(tmp_path, resolution):
    root = tmp_path / "scenario"
    write(
        root,
        "manifest/scenario_vms.json",
        json.dumps(
            {"vms": [{"vm_id": 3191, "vm_name": "guest-one", "ip": "10.42.1.2"}]}
        ),
    )
    write(
        root,
        "hosts.yml",
        yaml.safe_dump(
            {
                "all": {
                    "children": {
                        "scenario_guests": {
                            "hosts": {"guest-one": {"ansible_host": "10.42.1.2"}}
                        }
                    }
                }
            }
        ),
    )
    attachment = {
        "vm_id": 3191,
        "inventory_host": "guest-one",
        "resolution": resolution,
        "parameters": {"COUNT": 2},
    }
    write(
        root,
        "manifest/scenario_bundles.json",
        json.dumps({"version": 1, "attachments": [attachment]}),
    )
    write(
        root,
        "configure.yml",
        yaml.safe_dump(
            [
                {
                    "ansible.builtin.import_playbook": "{{ lookup('env', 'RANGE42_BUNDLE_DIR') }}/"
                    + resolution["entrypoint"],
                    "vars": {
                        "COUNT": 2,
                        "global_vm_ssh_name": "guest-one",
                        "global_vm_ci_ip": "10.42.1.2",
                    },
                }
            ]
        ),
    )
    return root


def test_matched_bundle_returns_bound_runtime_dependencies_and_validates_import(
    bundle, tmp_path
):
    from app.core.bundle_attachments import validate_scenario_bundles

    prepare(bundle)
    result = resolve(bundle)
    assert result["entrypoint"] == "generic/software.install.demo/main.yml"
    assert result["proof_kind"] == "content_match"
    assert result["target_vars"] == ["global_vm_ssh_name", "global_vm_ci_ip"]
    assert result["runtime"]["dependencies"][0]["name"] == "release"
    assert result["runtime"]["proof"]
    validate_scenario_bundles(scenario(tmp_path, result))


def test_dependency_drift_invalidates_existing_attachment(bundle, tmp_path):
    from app.core.bundle_attachments import validate_scenario_bundles

    prepare(bundle)
    root = scenario(tmp_path, resolve(bundle))
    write(
        bundle["runtime"],
        "roles/demo/tasks/main.yml",
        "- ansible.builtin.debug:\n    msg: changed\n",
    )
    with pytest.raises(Range42Error, match="runtime"):
        validate_scenario_bundles(root)


@pytest.mark.parametrize("change", ["source", "path", "params", "scope", "proof"])
def test_forged_resolution_is_rejected(bundle, tmp_path, change):
    from app.core.bundle_attachments import validate_scenario_bundles

    prepare(bundle)
    result = resolve(bundle)
    if change == "source":
        result["source_sha"] = "b" * 40
    if change == "path":
        result["entrypoint"] = "other/main.yml"
    if change == "params":
        result["params"].append({"name": "ansible_host", "type": "string"})
    if change == "scope":
        result["bundle_kind"] = "INFRA"
    if change == "proof":
        result["runtime"]["proof"] += "x"
    with pytest.raises(Range42Error):
        validate_scenario_bundles(scenario(tmp_path, result))


@pytest.mark.parametrize(
    "change",
    [
        "host",
        "ip",
        "vm_id",
        "duplicate",
        "parameter",
        "unknown_parameter",
        "secret_parameter",
        "template_parameter",
        "import",
        "extra_import",
    ],
)
def test_manifest_scope_and_callsite_must_match_the_verified_attachment(
    bundle, tmp_path, change
):
    from app.core.bundle_attachments import validate_scenario_bundles

    prepare(bundle)
    root = scenario(tmp_path, resolve(bundle))
    path = root / "manifest/scenario_bundles.json"
    doc = json.loads(path.read_text())
    entry = doc["attachments"][0]
    if change == "host":
        entry["inventory_host"] = "other"
    if change == "vm_id":
        entry["vm_id"] = 9999
    if change == "duplicate":
        doc["attachments"].append(copy.deepcopy(entry))
    if change == "parameter":
        entry["parameters"]["COUNT"] = "2"
    if change == "unknown_parameter":
        entry["parameters"]["OTHER"] = 1
    if change == "secret_parameter":
        entry["parameters"]["vault_password"] = "unsafe"
    if change == "template_parameter":
        entry["parameters"]["LABEL"] = "{{ inventory_hostname }}"
    path.write_text(json.dumps(doc))
    if change == "ip":
        (root / "hosts.yml").write_text(
            "all:\n  hosts:\n    guest-one:\n      ansible_host: 10.42.1.99\n"
        )
    if change in {"import", "extra_import"}:
        plays = yaml.safe_load((root / "configure.yml").read_text())
        if change == "import":
            plays[0]["vars"]["global_vm_ssh_name"] = "other"
        else:
            plays.append(copy.deepcopy(plays[0]))
        (root / "configure.yml").write_text(yaml.safe_dump(plays))
    with pytest.raises(Range42Error):
        validate_scenario_bundles(root)


@pytest.mark.parametrize(
    "change", ["different", "sibling", "symlink", "group", "missing_role"]
)
def test_unmatched_or_unbounded_bundle_cannot_be_resolved(bundle, tmp_path, change):
    if change == "different":
        write(
            bundle["source"],
            f"{bundle['path']}/main.yml",
            '- hosts: "{{ global_vm_ssh_name }}"\n  tasks: []\n',
        )
    if change == "sibling":
        for root in (bundle["runtime"], bundle["source"]):
            write(
                root,
                f"{bundle['path']}/extra.yml",
                "- import_playbook: ../other/main.yml\n",
            )
    if change == "symlink":
        private = write(tmp_path, "private", "not bundle content")
        (bundle["source"] / bundle["path"] / "escape").symlink_to(private)
    if change == "group":
        for root in (bundle["runtime"], bundle["source"]):
            write(
                root,
                f"{bundle['path']}/main.yml",
                '- hosts: "{{ target_group }}"\n  tasks: []\n',
            )
    if change == "missing_role":
        (bundle["runtime"] / "roles/demo/tasks/main.yml").unlink()
    prepare(bundle)
    with pytest.raises(Range42Error):
        resolve(bundle)


def test_no_manifest_does_not_require_bundle_runtime_for_plain_scenarios(
    tmp_path, monkeypatch
):
    from app.core.bundle_attachments import validate_scenario_bundles

    monkeypatch.delenv("RANGE42_BUNDLE_RUNTIME_MANIFEST", raising=False)
    validate_scenario_bundles(tmp_path)


def test_missing_runtime_is_explicit(bundle):
    with pytest.raises(Range42Error, match="runtime"):
        resolve(bundle)


def test_runtime_fingerprint_covers_environment_and_rejects_escaping_links(
    bundle, tmp_path, monkeypatch
):
    from app.core.bundle_runtime import runtime_snapshot

    prepare(bundle)
    monkeypatch.setenv("ANSIBLE_ROLES_PATH", str(tmp_path / "other-roles"))
    with pytest.raises(Range42Error, match="runtime"):
        runtime_snapshot()
    monkeypatch.setenv("ANSIBLE_ROLES_PATH", str(bundle["runtime"] / "roles"))
    private = write(tmp_path, "outside", "private")
    (bundle["runtime"] / "leak").symlink_to(private)
    with pytest.raises(Range42Error, match="runtime"):
        runtime_snapshot()


@pytest.mark.parametrize(
    "change",
    [
        "resolution_list",
        "params_list",
        "unknown_version",
        "yaml_mapping",
        "missing_file",
        "extra_import_field",
    ],
)
def test_malformed_attachment_contract_fails_with_actionable_error(
    bundle, tmp_path, change
):
    from app.core.bundle_attachments import validate_scenario_bundles

    prepare(bundle)
    root = scenario(tmp_path, resolve(bundle))
    path = root / "manifest/scenario_bundles.json"
    data = json.loads(path.read_text())
    if change == "resolution_list":
        data["attachments"][0]["resolution"] = []
    if change == "params_list":
        data["attachments"][0]["parameters"] = []
    if change == "unknown_version":
        data["version"] = True
    path.write_text(json.dumps(data))
    if change == "yaml_mapping":
        (root / "configure.yml").write_text("unexpected: object\n")
    if change == "missing_file":
        (root / "hosts.yml").unlink()
    if change == "extra_import_field":
        plays = yaml.safe_load((root / "configure.yml").read_text())
        plays[0]["when"] = False
        (root / "configure.yml").write_text(yaml.safe_dump(plays))
    with pytest.raises(Range42Error):
        validate_scenario_bundles(root)


@pytest.mark.parametrize(
    "statement",
    [
        "- ansible.builtin.include_tasks: '{{ target }}.yml'",
        "- ansible.builtin.debug:\n    msg: \"{{ lookup('env', chosen_dependency) }}\"",
    ],
)
def test_dynamic_dependency_paths_are_rejected(bundle, statement):
    for root in (bundle["source"], bundle["runtime"]):
        write(root, f"{bundle['path']}/tasks.yml", statement)
    prepare(bundle)
    with pytest.raises(Range42Error):
        resolve(bundle)


def test_bundle_import_without_a_resolution_manifest_is_rejected(tmp_path):
    from app.core.bundle_attachments import validate_scenario_bundles

    write(
        tmp_path,
        "configure.yml",
        "- import_playbook: \"{{ lookup('env', 'RANGE42_BUNDLE_DIR') }}/generic/software.install.demo/main.yml\"\n",
    )
    with pytest.raises(Range42Error):
        validate_scenario_bundles(tmp_path)


def test_comments_do_not_create_false_sibling_dependencies(bundle):
    for root in (bundle["source"], bundle["runtime"]):
        path = root / bundle["path"] / "main.yml"
        path.write_text(
            "# Example import: {{ lookup('env', 'RANGE42_BUNDLE_DIR') }}/this/main.yml\n"
            + path.read_text()
        )
    prepare(bundle)
    assert resolve(bundle)["bundle_kind"] == "VM"


@pytest.mark.parametrize(
    "value,valid", [("YES", True), ("NO", True), (True, False), ("yes", False)]
)
def test_yesno_parameters_follow_bundle_contract_without_coercion(value, valid):
    from app.core.bundle_attachments import _parameters

    resolution = {
        "target_vars": [],
        "params": [{"name": "INSTALL_DEMO", "type": "bool", "bool_style": "yesno"}],
    }
    if valid:
        _parameters(resolution, {"INSTALL_DEMO": value})
    else:
        with pytest.raises(Range42Error):
            _parameters(resolution, {"INSTALL_DEMO": value})


def test_allowed_parameter_choices_are_enforced():
    from app.core.bundle_attachments import _parameters

    resolution = {
        "target_vars": [],
        "params": [
            {"name": "MODE", "type": "string", "allowed": ["safe"], "enum": ["ignored"]}
        ],
    }
    _parameters(resolution, {"MODE": "safe"})
    with pytest.raises(Range42Error):
        _parameters(resolution, {"MODE": "ignored"})


def test_bundle_cannot_claim_unhashed_sibling_code_via_common_parent_env(
    bundle, tmp_path, monkeypatch
):
    monkeypatch.setenv("RANGE42_GITDIR__ROOT_DIR", str(tmp_path))
    for root in (bundle["source"], bundle["runtime"]):
        path = root / bundle["path"] / "main.yml"
        path.write_text(
            path.read_text()
            + "  vars:\n    LOCAL_CODE_PATH: \"{{ lookup('env', 'RANGE42_GITDIR__ROOT_DIR') }}/unhashed-app\"\n"
        )
    prepare(bundle)
    with pytest.raises(Range42Error):
        resolve(bundle)
