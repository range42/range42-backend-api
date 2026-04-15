"""Vector loader for the shared TS/Python parity harness."""
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
VECTORS_ROOT = REPO_ROOT / "range42-deployer-ui" / "schema" / "test-vectors"


def _load_vectors(subdir: str) -> list[dict]:
    root = VECTORS_ROOT / subdir
    if not root.exists():
        pytest.skip(f"vectors dir missing: {root}")
    out = []
    for p in sorted(root.glob("*.json")):
        with p.open() as f:
            vec = json.load(f)
        vec["__file__"] = p.name
        out.append(vec)
    return out


@pytest.fixture(scope="session")
def compose_vectors() -> list[dict]:
    return _load_vectors("compose")


@pytest.fixture(scope="session")
def expand_replication_vectors() -> list[dict]:
    return _load_vectors("expand_replication")


@pytest.fixture(scope="session")
def redaction_vectors() -> list[dict]:
    return _load_vectors("redaction")
