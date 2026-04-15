import pytest

from app.overlay import compose, NotImplementedOperator


def test_compose_vectors(compose_vectors):
    assert compose_vectors, "no compose vectors loaded"
    for vec in compose_vectors:
        label = f"{vec['__file__']} — {vec['name']}"
        if vec.get("edge"):
            with pytest.raises(NotImplementedOperator):
                compose(vec["input"]["base"], vec["input"]["overlay"])
        else:
            got = compose(vec["input"]["base"], vec["input"]["overlay"])
            assert got == vec["expected"], f"vector failed: {label}"
