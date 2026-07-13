"""Render a canvas-authored scenario into a concrete bundle-scenario directory.

Genericity lives here at author time: instead of a runtime engine interpreting a
topology (the retired ``_universal`` path), this package emits the same concrete
scenario files a human authors today, so the plain scenario runner deploys the
result unchanged. Rendered scenarios are portable -- bundle imports are anchored on
``RANGE42_GITDIR__ROOT_DIR``, so a scenario can live in a project repo rather than
in range42-playbooks.
"""

from app.core.scenario_renderer.baseline import render_active_group, render_baseline
from app.core.scenario_renderer.bootstrap import render_bootstrap_group
from app.core.scenario_renderer.flags import render_feature_flags
from app.core.scenario_renderer.manifest import render_scenario_vms
from app.core.scenario_renderer.orchestration import (
    finalize_group_build_filename,
    finalize_import_filename,
    render_finalize,
    render_finalize_group,
    render_main,
    render_templates_bootstrap,
    render_tier_stage00,
    render_tier_stage01,
)
from app.core.scenario_renderer.registry import (
    BundleKind,
    bundle_kind,
    is_vm_level,
    known_bundles,
    resolve_bundle_playbook,
)
from app.core.scenario_renderer.stage01 import render_vm_software
from app.core.scenario_renderer.writer import render_scenario
from app.core.scenario_renderer.types import (
    BundleRef,
    CrossTierGroup,
    ScenarioSpec,
    TemplateSpec,
    TierSpec,
    VmSpec,
)

__all__ = [
    "BundleKind",
    "BundleRef",
    "ScenarioSpec",
    "TemplateSpec",
    "TierSpec",
    "VmSpec",
    "bundle_kind",
    "is_vm_level",
    "known_bundles",
    "render_active_group",
    "render_baseline",
    "render_bootstrap_group",
    "render_feature_flags",
    "CrossTierGroup",
    "finalize_group_build_filename",
    "finalize_import_filename",
    "render_finalize",
    "render_finalize_group",
    "render_templates_bootstrap",
    "render_main",
    "render_scenario",
    "render_scenario_vms",
    "render_tier_stage00",
    "render_tier_stage01",
    "render_vm_software",
    "resolve_bundle_playbook",
]
