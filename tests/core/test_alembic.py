import subprocess
from pathlib import Path


def test_alembic_upgrade_downgrade_roundtrip(tmp_path, monkeypatch):
    db = tmp_path / "mig.db"
    monkeypatch.setenv("RANGE42_DB_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("RANGE42_WORKSPACE_ROOT", str(tmp_path))
    repo = Path(__file__).resolve().parents[2]
    subprocess.run(["alembic", "upgrade", "head"], cwd=repo, check=True,
                       capture_output=True, text=True)
    assert db.exists()
    subprocess.run(["alembic", "downgrade", "base"], cwd=repo, check=True,
                       capture_output=True, text=True)
