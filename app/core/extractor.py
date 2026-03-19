"""Extract structured results from Ansible runner events."""


def extract_action_results(events: list[dict], action_to_search: str) -> list:
    """Find all runner_on_ok events containing the specified action key."""
    out = []
    for ev in events:
        if ev.get("event") != "runner_on_ok":
            continue
        res = (ev.get("event_data") or {}).get("res")
        if isinstance(res, dict) and action_to_search in res:
            out.append(res[action_to_search])
    return out
