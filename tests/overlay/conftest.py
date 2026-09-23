"""Vector loader for the shared TS/Python parity harness."""
import json
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
VECTORS_ROOT = REPO_ROOT / "range42-deployer-ui" / "schema" / "test-vectors"


def _load_vectors(subdir: str) -> list[dict]:
    root = VECTORS_ROOT / subdir
    if not root.exists():
        # In CI the sibling checkout is guaranteed, so a missing vectors dir
        # means the parity harness silently verified nothing — the exact
        # failure mode the workflow's checkout fallback exists to prevent.
        # Locally (partial clone, no sibling repo) skipping is still right.
        msg = f"vectors dir missing: {root}"
        if os.getenv("CI"):
            pytest.fail(msg)
        pytest.skip(msg)
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
