from app.overlay import compose


def test_compose_vectors(compose_vectors):
    assert compose_vectors, "no compose vectors loaded"
    for vec in compose_vectors:
        label = f"{vec['__file__']} — {vec['name']}"
        # Plan B: edge vectors are now first-class pass vectors.
        got = compose(vec["input"]["base"], vec["input"]["overlay"])
        assert got == vec["expected"], f"vector failed: {label}"
