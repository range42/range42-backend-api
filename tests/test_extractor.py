from app.core.extractor import extract_action_results


def test_extract_finds_matching_action():
    events = [
        {"event": "runner_on_ok", "event_data": {"res": {"vm_list": [{"vmid": 100}]}}},
        {"event": "runner_on_ok", "event_data": {"res": {"other_action": "data"}}},
    ]
    result = extract_action_results(events, "vm_list")
    assert result == [[{"vmid": 100}]]


def test_extract_returns_empty_for_no_match():
    events = [{"event": "runner_on_ok", "event_data": {"res": {"other": "data"}}}]
    result = extract_action_results(events, "vm_list")
    assert result == []


def test_extract_skips_non_ok_events():
    events = [
        {
            "event": "runner_on_failed",
            "event_data": {"res": {"vm_list": [{"vmid": 100}]}},
        }
    ]
    result = extract_action_results(events, "vm_list")
    assert result == []


def test_extract_handles_empty_events():
    assert extract_action_results([], "vm_list") == []


def test_extract_handles_missing_event_data():
    events = [{"event": "runner_on_ok"}]
    result = extract_action_results(events, "vm_list")
    assert result == []
