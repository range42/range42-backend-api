"""Canonical allocation transfer must survive either side losing its reply."""

from copy import deepcopy
from importlib.util import find_spec
import json

import pytest
from sqlalchemy import func, select

from app.core.db import build_engine, session_factory
from app.core.models import Base, Source, Project, ProxmoxHost, Deployment
from app.core.allocation_models import DeploymentAllocation
from app.core.errors import Range42Error
from app.core.physical_nodes import digest


def service():
    assert find_spec("app.core.workshop_ownership"), (
        "Missing original native ownership import into the canonical allocation ledger"
    )
    from app.core import workshop_ownership

    return workshop_ownership


def proof():
    assignments = []
    for offset in range(5):
        for increment, role, suffix in [(0, "proxmox", "pve"), (1, "deployer", "cli")]:
            vmid = 43010 + offset * 2 + increment
            assignments.append(
                {
                    "instance_id": f"user{offset + 1:02}",
                    "vm_id": vmid,
                    "vm_name": f"workshop-user{offset + 1:02}-{suffix}",
                    "nics": [
                        {
                            "index": 0,
                            "bridge": f"vmbr{430 + offset}",
                            "ip": f"10.77.32.{offset * 16 + 2 + increment}",
                            "prefix": 28,
                        }
                    ],
                    "generation": {
                        "owner": f"range42-workshop-20261023:user{offset + 1:02}:{role}",
                        "vmgenid": f"11111111-2222-4333-8444-{vmid:012}",
                        "receipt_sha256": "b" * 64,
                        "configuration_sha256": "c" * 64,
                    },
                }
            )
    result = {
        "version": 1,
        "cohort": {
            "id": "cohort",
            "project_id": "project",
            "project_sha": "a" * 40,
            "target_host_id": "host",
        },
        "node_name": "pve42-1",
        "api_certificate_sha256": "d" * 64,
        "node_certificate_sha256": "e" * 64,
        "assignments": assignments,
    }
    result["review_revision"] = digest(result)
    result["handoff"] = None
    return result


class Controller:
    def __init__(self):
        self.calls = []
        self.import_id = None
        self.fail_begin = False
        self.fail_commit = False
        self.review_revision = None
        self.factory = None
        self.phase = None
        self.allocation_id = None

    async def __call__(self, request):
        self.calls.append(deepcopy(request))
        if request["action"] == "ownership-begin":
            if self.import_id is None:
                self.import_id = request["import_id"]
            self.review_revision = request["review_revision"]
            self.phase = "pending"
            assert self.import_id == request["import_id"]
            if self.fail_begin:
                self.fail_begin = False
                raise Range42Error(
                    status=503,
                    code="WORKSHOP_UNAVAILABLE",
                    message="Synthetic lost begin response",
                )
            return {
                "import_id": self.import_id,
                "phase": "pending",
                "review_revision": request["review_revision"],
                "allocation_id": None,
            }
        assert (
            request["action"] == "ownership-commit"
            and request["import_id"] == self.import_id
        )
        async with self.factory() as session:
            claim = await session.scalar(select(DeploymentAllocation))
            self.allocation_id = claim.id
        self.phase = "acknowledged"
        if self.fail_commit:
            self.fail_commit = False
            raise Range42Error(
                status=503,
                code="WORKSHOP_UNAVAILABLE",
                message="Synthetic lost acknowledgement response",
            )
        return {
            "import_id": self.import_id,
            "phase": "acknowledged",
            "allocation_id": self.allocation_id,
            "review_revision": self.review_revision,
        }


@pytest.fixture
async def env(tmp_path, monkeypatch):
    mod = service()
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/ownership.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = session_factory(engine)
    from app.core import db

    monkeypatch.setattr(db, "_engine", engine)
    monkeypatch.setattr(db, "_Session", factory)
    async with factory() as session:
        session.add(
            Source(
                id="source",
                provider="github",
                base_url="https://github.com",
                auth_kind="none",
            )
        )
        session.add(
            ProxmoxHost(
                id="host",
                name="host",
                api_url="https://fixture.invalid:8006",
                node_name="pve42-1",
                token_ref="synthetic@pve!key=PRIVATE",
                protected_vmids_override_json=json.dumps(
                    [[100, 43009], [43030, 999999999]]
                ),
            )
        )
        await session.flush()
        session.add(
            Project(
                id="project",
                name="project",
                source_id="source",
                branch_strategy="shared_repo_subdir",
            )
        )
        await session.flush()
        session.add(
            Deployment(
                id="cohort",
                codename="COHORT",
                scenario_label="legacy",
                project_id="project",
                target_host_id="host",
                project_sha="a" * 40,
                state="succeeded",
                workspace_path=str(tmp_path / "cohort"),
                native={
                    "path": "scenarios/range42-native-workshop",
                    "parameters": {"WORKSHOP_ONLY_SEAT": 0},
                    "features": {"EXTRA_SEATS": False},
                    "descriptor": {"declared_vmids": list(range(43010, 43030))},
                },
            )
        )
        await session.commit()
    rpc = Controller()
    rpc.factory = factory
    yield mod, factory, engine, rpc
    await engine.dispose()


async def observe(env, value=None):
    mod, factory, _, _ = env
    async with factory() as session:
        host = await session.get(ProxmoxHost, "host")
        witness = value or proof()
        if value is None and env[3].import_id is not None:
            rpc = env[3]
            witness["handoff"] = {
                "import_id": rpc.import_id,
                "phase": rpc.phase,
                "review_revision": rpc.review_revision,
                "allocation_id": rpc.allocation_id,
            }
        return await mod.review_observation(
            session,
            "cohort",
            mod.OwnershipWitness.model_validate(witness),
            observed_target_revision=mod.target_revision(host),
        )


async def save(env, observed, revision=None):
    mod, factory, _, rpc = env
    async with factory() as session:
        return await mod.save_observation(
            session,
            observed,
            review_revision=revision or observed.revision,
            actor="director",
            rpc=rpc,
        )


def test_native_ownership_import_service_exists():
    service()


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_reply", ["begin", "commit"])
async def test_lost_handoff_reply_retains_one_intent_and_recovers_one_canonical_claim(
    env, lost_reply
):
    mod, factory, engine, rpc = env
    observed = await observe(env)
    setattr(rpc, "fail_" + lost_reply, True)
    with pytest.raises(Range42Error):
        await save(env, observed)
    async with factory() as session:
        pending = await mod.status(session, "cohort")
        assert pending["phase"] == (
            "pending_controller" if lost_reply == "begin" else "claim_saved"
        )
        assert pending["import_id"] == rpc.import_id
        assert pending["capacity_borrowing_allowed"] is False
        assert await session.scalar(
            select(func.count()).select_from(DeploymentAllocation)
        ) == (0 if lost_reply == "begin" else 1)
    await engine.dispose()
    recovered = await save(env, await observe(env), observed.revision)
    assert recovered["phase"] == "active" and recovered["changed_by"] == "director"
    assert recovered["capacity_borrowing_allowed"] is False
    async with factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(DeploymentAllocation))
            == 1
        )
        claim = await session.scalar(select(DeploymentAllocation))
        assert [row["vm_id"] for row in claim.assignments] == list(range(43010, 43020))
        assert claim.deployment_id == "cohort"
    assert len({call["import_id"] for call in rpc.calls}) == 1


@pytest.mark.asyncio
async def test_fresh_generation_drift_refuses_an_old_review_before_any_handoff(env):
    observed = await observe(env)
    changed = proof()
    changed["assignments"][0]["generation"]["vmgenid"] = (
        "99999999-2222-4333-8444-555555555555"
    )
    changed["review_revision"] = digest(
        {
            key: value
            for key, value in changed.items()
            if key not in {"review_revision", "handoff"}
        }
    )
    with pytest.raises(Range42Error) as error:
        await save(env, await observe(env, changed), observed.revision)
    assert error.value.code == "WORKSHOP_OWNERSHIP_REVIEW_REQUIRED"
    assert not env[3].calls


@pytest.mark.asyncio
async def test_another_controller_or_target_cannot_import_the_original_guest_ids(env):
    for field, new in [
        ("id", "other"),
        ("project_id", "other"),
        ("project_sha", "f" * 40),
        ("target_host_id", "other"),
    ]:
        changed = proof()
        changed["cohort"][field] = new
        changed["review_revision"] = digest(
            {
                key: value
                for key, value in changed.items()
                if key not in {"review_revision", "handoff"}
            }
        )
        with pytest.raises(Range42Error):
            await observe(env, changed)
    assert not env[3].calls


@pytest.mark.asyncio
async def test_public_import_core_accepts_an_original_native_scope_without_friday_names_or_ranges(
    env,
):
    mod, factory, _, _ = env
    async with factory() as session:
        dep = await session.get(Deployment, "cohort")
        dep.native = {
            "path": "labs/original-native-lab",
            "descriptor": {"declared_vmids": [55000]},
            "features": {},
            "parameters": {},
        }
        host = await session.get(ProxmoxHost, "host")
        host.protected_vmids_override_json = "[]"
        await session.commit()
    value = proof()
    value["assignments"] = value["assignments"][:1]
    value["assignments"][0].update(
        vm_id=55000,
        vm_name="original-native-guest",
        nics=[{"index": 0, "bridge": "vmbr700", "ip": "10.88.0.2", "prefix": 24}],
    )
    value["assignments"][0]["generation"]["owner"] = "original-native-owner"
    value["review_revision"] = digest(
        {
            key: val
            for key, val in value.items()
            if key not in {"review_revision", "handoff"}
        }
    )
    observed = await observe(env, value)
    assert observed.witness.assignments[0].vm_id == 55000


@pytest.mark.parametrize("field", ["version", "nic-index"])
def test_boolean_values_cannot_masquerade_as_integer_protocol_fields(field):
    mod = service()
    value = proof()
    if field == "version":
        value["version"] = True
    else:
        value["assignments"][0]["nics"][0]["index"] = False
    with pytest.raises(ValueError):
        mod.OwnershipWitness.model_validate(value)


@pytest.mark.asyncio
async def test_foreign_extra_fields_in_controller_handoff_never_leak_or_mark_ready(env):
    mod, factory, _, _ = env
    observed = await observe(env)

    async def bad_reply(request):
        return {
            "import_id": request["import_id"],
            "phase": "pending",
            "review_revision": observed.witness.review_revision,
            "allocation_id": None,
            "credential": "DO-NOT-LEAK",
        }

    async with factory() as session:
        with pytest.raises(Range42Error) as error:
            await mod.save_observation(
                session,
                observed,
                review_revision=observed.revision,
                actor="director",
                rpc=bad_reply,
            )
        assert "DO-NOT-LEAK" not in str(error.value)
    async with factory() as session:
        assert (await mod.status(session, "cohort"))["phase"] == "pending_controller"
        assert (
            await session.scalar(select(func.count()).select_from(DeploymentAllocation))
            == 0
        )


@pytest.mark.asyncio
async def test_import_will_not_replace_existing_canonical_provenance(env):
    mod, factory, _, rpc = env
    from datetime import datetime, timezone

    async with factory() as session:
        session.add(
            DeploymentAllocation(
                id="f" * 32,
                deployment_id="cohort",
                host_id="host",
                node_name="pve42-1",
                api_url="https://fixture.invalid:8006",
                project_sha="a" * 40,
                assignments=[],
                source_assignments=[{"original": "AUTHORING-PROVENANCE"}],
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
    with pytest.raises(Range42Error):
        await save(env, await observe(env))
    assert not rpc.calls
    async with factory() as session:
        assert (
            await session.get(DeploymentAllocation, "f" * 32)
        ).source_assignments == [{"original": "AUTHORING-PROVENANCE"}]
