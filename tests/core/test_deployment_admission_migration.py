"""Upgrade retains credentials; downgrade cannot erase a retained pause."""

import asyncio
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from app.core.db import build_engine, session_factory
from app.core.models import Source, Project, ProxmoxHost, Deployment
from app.core.allocation_models import DeploymentAllocation
from datetime import datetime, timezone


def test_upgrade_and_restart_keep_admission_and_refuse_unsafe_downgrade(
    tmp_path, monkeypatch
):
    database = tmp_path / "migration.db"
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    environment = {**os.environ, "RANGE42_DB_URL": f"sqlite+aiosqlite:///{database}"}

    def migrate(*args, success=True):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert (result.returncode == 0) == success, result.stderr
        return result

    migrate("upgrade", "0013_physical_nodes")

    async def seed():
        engine = build_engine(environment["RANGE42_DB_URL"])
        try:
            async with session_factory(engine)() as session:
                session.add(
                    Source(
                        id="s",
                        provider="github",
                        base_url="https://github.com",
                        auth_kind="none",
                    )
                )
                session.add(
                    ProxmoxHost(
                        id="h",
                        name="h",
                        api_url="https://fixture.invalid",
                        node_name="pve",
                        token_ref="synthetic@pve!key=PRIVATE",
                    )
                )
                await session.flush()
                session.add(
                    Project(
                        id="p",
                        name="p",
                        source_id="s",
                        branch_strategy="shared_repo_subdir",
                    )
                )
                await session.flush()
                session.add(
                    Deployment(
                        id="d",
                        codename="DEMO",
                        scenario_label="lab",
                        project_id="p",
                        target_host_id="h",
                        project_sha="a" * 40,
                        workspace_path=str(tmp_path / "d"),
                    )
                )
                await session.flush()
                session.add(
                    DeploymentAllocation(
                        id="c",
                        deployment_id="d",
                        project_sha="a" * 40,
                        host_id="h",
                        api_url="https://fixture.invalid",
                        node_name="pve",
                        assignments=[{"vm_id": 45000, "nics": []}],
                        created_at=datetime.now(timezone.utc),
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(seed())
    with sqlite3.connect(database) as connection:
        original_ciphertext = connection.execute(
            "SELECT token_ref FROM proxmox_hosts"
        ).fetchone()[0]
        assert "PRIVATE" not in original_ciphertext
    migrate("upgrade", "head")

    async def pause():
        from app.core import deployment_admission

        engine = build_engine(environment["RANGE42_DB_URL"])
        try:
            async with session_factory(engine)() as session:
                return await deployment_admission.change(
                    session,
                    "d",
                    action="pause",
                    expected_revision=0,
                    request_id="1" * 32,
                    actor="director",
                )
        finally:
            await engine.dispose()

    first = asyncio.run(pause())
    repeated = asyncio.run(pause())
    assert first["pause_id"] == repeated["pause_id"] and repeated["revision"] == 1
    result = migrate("downgrade", "0013_physical_nodes", success=False)
    assert "Retained deployment admission decisions exist" in result.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT state,revision,pause_id FROM deployment_admissions"
        ).fetchone() == ("closed", 1, first["pause_id"])
        assert (
            connection.execute("SELECT token_ref FROM proxmox_hosts").fetchone()[0]
            == original_ciphertext
        )
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
