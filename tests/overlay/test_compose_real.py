import json
from pathlib import Path
import pytest
from app.overlay.compose import compose


VECTORS_ROOT = (
    Path(__file__).resolve().parents[2].parent
    / "range42-deployer-ui" / "schema" / "test-vectors" / "compose"
)


@pytest.mark.parametrize(
    "vector_path",
    sorted(VECTORS_ROOT.glob("*.json")) if VECTORS_ROOT.exists() else [],
)
def test_compose_matches_vector(vector_path):
    raw = json.loads(vector_path.read_text())
    inp = raw["input"]
    got = compose(inp["base"], inp["overlay"])
    assert got == raw["expected"], f"divergence in {vector_path.name}"
