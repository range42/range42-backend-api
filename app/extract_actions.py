

### TEMP EXTRACTION

def extract_action_results(events, action_to_search: str) -> list:
    """
    extract actions data if runner_on_ok.
    """

    out = []
    for ev in events:
        # print (ev)
        if ev.get("event") == "runner_on_ok":
            res = (ev.get("event_data") or {}).get("res")
            if isinstance(res, dict):
                if action_to_search in res:
                    out.append(res[action_to_search])

                # elif "msg" in res and isinstance(res["msg"], str):
                #     try:
                #         j = json.loads(res["msg"])
                #         if isinstance(j, dict) and action in j:
                #             out.append(j[action_to_search])
                #     except Exception:
                #         pass

    return out