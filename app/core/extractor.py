"""Extract structured results from Ansible runner events.

Provides :func:`extract_action_results`, which filters ``runner_on_ok``
events for a specific action key and collects the matching result data.
"""


def extract_action_results(events: list[dict], action_to_search: str) -> list:
    """Find all ``runner_on_ok`` events containing the specified action key.

    Iterates over the Ansible runner event list and extracts the value
    stored under ``event_data.res.<action_to_search>`` for every
    successful task event.

    :param events: List of Ansible runner event dicts.
    :type events: list[dict]
    :param action_to_search: The action key to search for in each event's
        ``res`` dictionary (e.g. ``"vm_list"``, ``"vm_start"``).
    :type action_to_search: str
    :returns: List of extracted result values from matching events.
    :rtype: list
    """
    out = []
    for ev in events:
        if ev.get("event") != "runner_on_ok":
            continue
        res = (ev.get("event_data") or {}).get("res")
        if isinstance(res, dict) and action_to_search in res:
            out.append(res[action_to_search])
    return out
