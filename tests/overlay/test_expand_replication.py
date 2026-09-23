from app.overlay import expand_replication


def test_expand_replication_vectors(expand_replication_vectors):
    assert expand_replication_vectors, "no expand_replication vectors loaded"
    for vec in expand_replication_vectors:
        label = f"{vec['__file__']} — {vec['name']}"
        # Plan B: edge vectors are now first-class pass vectors.
        got = expand_replication(vec["input"]["document"], vec["input"]["team_count"])
        assert got["plays_per_team"] == vec["expected"]["plays_per_team"], label
        assert got["handler_namespaces"] == vec["expected"]["handler_namespaces"], label
        if "document" in vec["expected"]:
            assert got["document"] == vec["expected"]["document"], label
