"""Guided bundle authoring -- the grammar, enforced.

Bundles are about to be mass-created, and the failure mode is naming anarchy
(verb synonyms, verb-first names, snake_case). The grammar is
``<tier>/<subject>.<verb>[.<object>]`` with the verb ALWAYS second, drawn from
closed vocabularies. This module is what a "create a new bundle" wizard calls,
so its error messages are a UX surface: each one is asserted on here, not just
its exception type.

``ctf/`` is exempt by design -- a challenge is identity-addressed by CVE, not
action-addressed -- so grammar-parsing a ctf name is itself an error.
"""

import shutil
import subprocess

import pytest
import yaml

from app.core.scenario_renderer import BundleKind
from app.core.scenario_renderer.authoring import parse_bundle_name, scaffold_bundle

#: What a call-site must pass for each kind -- the kind IS this contract. Supplying
#: them is what makes the templated `hosts:` resolvable, exactly as a scenario's
#: `import_playbook ... vars:` does at deploy time.
CALL_SITE_VARS = {
    BundleKind.VM: {"global_vm_ssh_name": "r42.example-vm", "global_vm_ci_ip": "192.168.142.10"},
    BundleKind.GROUP: {"target_group": "r42_admin_group"},
    BundleKind.INFRA: {},
}


def syntax_check(files, kind, tmp_path):
    """Write a scaffold to disk and have Ansible itself judge whether it runs.

    Only the TEST touches the filesystem; the module under test is pure. `-i
    localhost,` keeps ansible from moaning about an absent inventory -- it is the
    playbook's *syntax* that is under test, not our ability to reach a VM.
    """
    for relative_path, content in files.items():
        (tmp_path / relative_path).write_text(content)

    command = [
        "ansible-playbook",
        "--syntax-check",
        str(tmp_path / "main.yml"),
        "-i",
        "localhost,",
    ]
    for key, value in CALL_SITE_VARS[kind].items():
        command += ["-e", f"{key}={value}"]
    return subprocess.run(command, capture_output=True, text=True)


class TestParseConformingNames:
    def test_conforming_name_decomposes_into_tier_subject_verb_object(self):
        # admin/software.install.gitea -- the shape every admin/ bundle already has
        parsed = parse_bundle_name("admin/software.install.gitea")

        assert (parsed.tier, parsed.subject, parsed.verb, parsed.object) == (
            "admin",
            "software",
            "install",
            "gitea",
        )

    def test_object_is_optional_and_absent_means_the_generic_form(self):
        # core/vm.bootstrap -- no object: THE way to bootstrap a VM, not a variant
        parsed = parse_bundle_name("core/vm.bootstrap")

        assert (parsed.subject, parsed.verb) == ("vm", "bootstrap")
        assert parsed.object is None

    def test_kebab_case_joins_words_inside_one_segment_not_across_segments(self):
        # the '-' is INSIDE the object; it does not create a fourth segment
        parsed = parse_bundle_name("admin/software.install.deployer-api-backend")

        assert (parsed.verb, parsed.object) == ("install", "deployer-api-backend")


class TestVerbVocabularyIsClosed:
    """The anarchy failure mode: verb synonyms. `setup`/`deploy`/`provision` are
    all `install`. The error must name the legal verbs, or the contributor just
    invents another synonym.
    """

    @pytest.mark.parametrize("synonym", ["setup", "deploy", "provision"])
    def test_verb_synonym_is_rejected_and_the_legal_verbs_are_named(self, synonym):
        with pytest.raises(ValueError) as err:
            parse_bundle_name(f"admin/software.{synonym}.gitea")

        message = str(err.value)
        assert f"unknown verb '{synonym}'" in message
        # the message must carry the whole closed vocabulary, not just a complaint
        for verb in ("install", "build", "create", "configure", "baseline", "clone", "bootstrap"):
            assert verb in message

    def test_a_known_synonym_is_told_which_verb_it_should_have_been(self):
        with pytest.raises(ValueError, match="did you mean 'install'"):
            parse_bundle_name("admin/software.deploy.gitea")


class TestVerbMustBeSecond:
    """The whole point of the grammar is verb-second, so the likeliest mistake is
    putting the verb somewhere else. Both misplacements parse as "plausible"
    segments, so they must be caught by position, not by vocabulary alone.
    """

    def test_verb_in_the_subject_position_is_caught_and_the_fix_is_spelled_out(self):
        with pytest.raises(ValueError) as err:
            parse_bundle_name("admin/install.software.docker")

        message = str(err.value)
        assert "'install' is a verb" in message
        assert "second" in message
        assert "admin/software.install.docker" in message  # the reordered name

    def test_verb_in_the_object_position_is_caught_and_the_fix_is_spelled_out(self):
        # the "english word order" mistake: software.docker.install
        with pytest.raises(ValueError) as err:
            parse_bundle_name("admin/software.docker.install")

        message = str(err.value)
        assert "'install' is a verb" in message
        assert "second" in message
        assert "admin/software.install.docker" in message

    def test_two_segment_name_with_the_verb_first_is_caught_too(self):
        # bootstrap.vm instead of vm.bootstrap -- no object to disambiguate
        with pytest.raises(ValueError) as err:
            parse_bundle_name("core/bootstrap.vm")

        assert "core/vm.bootstrap" in str(err.value)


class TestTier:
    def test_unknown_tier_is_rejected_and_the_legal_tiers_are_named(self):
        with pytest.raises(ValueError) as err:
            parse_bundle_name("student/software.install.gitea")

        message = str(err.value)
        assert "unknown tier 'student'" in message
        assert "'admin'" in message and "'core'" in message

    def test_ctf_is_exempt_from_the_grammar_and_says_so(self):
        # a challenge is identity-addressed (which CVE), not action-addressed --
        # grammar-parsing one is a category error, not a naming mistake to fix
        with pytest.raises(ValueError) as err:
            parse_bundle_name("ctf/cve/web/tomcat/CVE-2025-24813")

        message = str(err.value)
        assert "exempt" in message
        assert "identity-addressed" in message

    def test_the_slash_separates_tier_from_name_and_is_the_only_slash(self):
        with pytest.raises(ValueError, match="exactly one '/'"):
            parse_bundle_name("admin/software/install/gitea")

    def test_a_bare_name_with_no_tier_is_rejected(self):
        with pytest.raises(ValueError, match="exactly one '/'"):
            parse_bundle_name("software.install.gitea")


class TestSubjectVocabularyIsClosed:
    def test_unknown_subject_is_rejected_and_the_legal_subjects_are_named(self):
        with pytest.raises(ValueError) as err:
            parse_bundle_name("admin/docker.install.gitea")

        message = str(err.value)
        assert "unknown subject 'docker'" in message
        for subject in ("software", "system", "network", "credentials", "repo", "template", "vm"):
            assert subject in message


class TestSegmentCount:
    @pytest.mark.parametrize(
        "name",
        [
            "admin/software",  # one segment: no verb at all
            "admin/software.install.gitea.postgres",  # four: object is one segment
        ],
    )
    def test_only_two_or_three_dot_segments_are_legal(self, name):
        with pytest.raises(ValueError) as err:
            parse_bundle_name(name)

        message = str(err.value)
        assert "2 or 3" in message
        assert "<tier>/<subject>.<verb>[.<object>]" in message


class TestSegmentCharacters:
    """Segments are lowercase kebab. Anything else and the on-disk directory names
    stop being predictable -- which is the anarchy this module exists to prevent.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "admin/Software.install.gitea",  # uppercase subject
            "admin/software.Install.gitea",  # uppercase verb
            "admin/software.install.Gitea",  # uppercase object
            "admin/software.install.deployer_api",  # snake_case
            "admin/software.install.deployer api",  # space
            "admin/software.install.",  # empty object segment
            "Admin/software.install.gitea",  # uppercase tier
        ],
    )
    def test_non_kebab_segments_are_rejected(self, name):
        with pytest.raises(ValueError) as err:
            parse_bundle_name(name)

        assert "kebab" in str(err.value)

    def test_the_charset_error_names_what_is_allowed(self):
        with pytest.raises(ValueError) as err:
            parse_bundle_name("admin/software.install.deployer_api_backend")

        message = str(err.value)
        assert "'deployer_api_backend'" in message
        assert "lowercase" in message
        assert "deployer-api-backend" in message  # the fix, spelled out


class TestScaffoldIsRunnable:
    """The scaffold's whole value is that a contributor can `git add` it and it
    already runs. Asserting "valid YAML" would not prove that -- a play with a
    templated `hosts:` and no tasks is valid YAML and fatal to Ansible. So the
    scaffold is checked by ansible-playbook itself.
    """

    @pytest.mark.skipif(
        shutil.which("ansible-playbook") is None, reason="ansible-playbook not on PATH"
    )
    @pytest.mark.parametrize(
        ("name", "kind"),
        [
            ("admin/software.install.gitea", BundleKind.VM),
            ("core/system.baseline.default", BundleKind.GROUP),
            ("core/template.build.alpine", BundleKind.INFRA),
            ("core/vm.bootstrap", BundleKind.VM),  # the no-object generic form
        ],
    )
    def test_scaffold_passes_ansible_playbook_syntax_check(self, name, kind, tmp_path):
        files = scaffold_bundle(name, kind)

        result = syntax_check(files, kind, tmp_path)

        assert result.returncode == 0, result.stderr

    def test_scaffold_main_yml_is_a_list_of_plays(self):
        plays = yaml.safe_load(scaffold_bundle("admin/software.install.gitea", BundleKind.VM)["main.yml"])

        assert isinstance(plays, list) and plays
        assert all("hosts" in play for play in plays)


class TestScaffoldMatchesTheKindsCallSiteContract:
    def test_vm_bundle_targets_the_single_vm_it_is_told_about(self):
        files = scaffold_bundle("admin/software.install.gitea", BundleKind.VM)

        play = yaml.safe_load(files["main.yml"])[0]
        assert play["hosts"] == "{{ global_vm_ssh_name }}"
        # the header must document what the call-site has to pass, or the next
        # contributor learns the contract by deploying and watching it fail
        assert "global_vm_ssh_name" in files["main.yml"]
        assert "global_vm_ci_ip" in files["main.yml"]

    def test_group_bundle_targets_the_tier_group_it_is_told_about(self):
        files = scaffold_bundle("core/system.baseline.default", BundleKind.GROUP)

        play = yaml.safe_load(files["main.yml"])[0]
        assert play["hosts"] == "{{ target_group }}"
        assert "target_group" in files["main.yml"]
        assert "global_vm_ssh_name" not in files["main.yml"]

    def test_infra_bundle_runs_on_proxmox_and_takes_no_host_parameter(self):
        files = scaffold_bundle("core/template.build.alpine", BundleKind.INFRA)

        play = yaml.safe_load(files["main.yml"])[0]
        assert play["hosts"] == "proxmox"
        assert "global_vm_ssh_name" not in files["main.yml"]
        assert "target_group" not in files["main.yml"]

    def test_infra_play_does_not_force_become(self):
        # the connection to the proxmox host is already privileged -- the one real
        # `hosts: proxmox` play in the catalog (gitea, play 1) sets no become either.
        # A VM play does need it: cloud-init lands us as an unprivileged operator.
        play = yaml.safe_load(scaffold_bundle("core/template.build.alpine", BundleKind.INFRA)["main.yml"])[0]
        assert "become" not in play

        vm_play = yaml.safe_load(scaffold_bundle("core/vm.bootstrap", BundleKind.VM)["main.yml"])[0]
        assert vm_play["become"] is True

    def test_xtier_is_not_scaffoldable_and_says_why(self):
        # an xtier bundle acts on a group ASSEMBLED across tiers (wazuh_clients_group)
        # -- the scenario builds that group, so there is no generic skeleton for it
        with pytest.raises(ValueError) as err:
            scaffold_bundle("admin/software.install.wazuh-agent", BundleKind.XTIER)

        message = str(err.value)
        assert "xtier" in message.lower()
        assert "cross-tier" in message or "across tiers" in message


class TestScaffoldRejectsBadNamesBeforeEmittingAnything:
    def test_an_invalid_name_raises_rather_than_scaffolding_anarchy(self):
        with pytest.raises(ValueError, match="unknown verb 'setup'"):
            scaffold_bundle("admin/software.setup.gitea", BundleKind.VM)

    def test_a_ctf_name_is_refused_by_the_scaffolder_too(self):
        with pytest.raises(ValueError, match="exempt"):
            scaffold_bundle("ctf/cve/web/tomcat/CVE-2025-24813", BundleKind.VM)


class TestScaffoldReadme:
    def test_readme_documents_name_kind_required_vars_and_the_call_site(self):
        files = scaffold_bundle(
            "admin/software.install.gitea", BundleKind.VM, description="Gitea git server."
        )

        readme = files["README.md"]
        assert "bundles/admin/software.install.gitea" in readme
        assert "Gitea git server." in readme
        assert "vm" in readme
        assert "global_vm_ssh_name" in readme and "global_vm_ci_ip" in readme
        # the call-site block must be the real, env-anchored import a scenario writes
        assert (
            "- import_playbook: \"{{ lookup('env', 'RANGE42_GITDIR__ROOT_DIR') }}"
            "/range42-playbooks/bundles/admin/software.install.gitea/main.yml\"" in readme
        )

    def test_readme_call_site_block_is_valid_yaml(self):
        readme = scaffold_bundle("core/system.baseline.default", BundleKind.GROUP)["README.md"]

        block = readme.split("```yaml")[1].split("```")[0]
        imports = yaml.safe_load(block)

        assert imports[0]["import_playbook"].endswith(
            "bundles/core/system.baseline.default/main.yml"
        )
        assert imports[0]["vars"] == {"target_group": "r42_admin_group"}

    def test_infra_readme_says_there_are_no_required_vars(self):
        readme = scaffold_bundle("core/template.build.alpine", BundleKind.INFRA)["README.md"]

        assert "None" in readme
