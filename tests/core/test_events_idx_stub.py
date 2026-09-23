import pytest
from app.core.events import EventsIdx


def test_events_idx_build_deferred(tmp_path):
    idx = EventsIdx(tmp_path / "e.idx")
    with pytest.raises(NotImplementedError):
        idx.build()
    with pytest.raises(NotImplementedError):
        idx.lookup(0)
