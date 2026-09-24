"""Read every reviewed identity before allowing the next Ansible task."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import re
from urllib.parse import urlsplit

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.urls import open_url


def verify_guard(guard, data):
    if "digest" in guard:
        if "sort_key" in guard:
            key = guard["sort_key"]
            if (not isinstance(data, list)
                    or any(not isinstance(row, dict) or not isinstance(row.get(key), str) for row in data)
                    or len({row[key] for row in data}) != len(data)):
                raise ValueError("Invalid collection identity")
            data = sorted(data, key=lambda row: row[key])
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        matches = digest == guard["digest"]
    elif "resources" in guard:
        matches = sorted(f"{row['vmid']}:{row['node']}:{row['type']}" for row in data) == guard["resources"]
    else:
        matches = sorted(row["node"] for row in data) == guard["nodes"]
    if not matches:
        raise ValueError("Reviewed identity changed")


def main():
    module = AnsibleModule(argument_spec={
        "api_host": {"type": "str", "required": True},
        "authorization": {"type": "str", "required": True, "no_log": True},
        "guards": {"type": "list", "elements": "dict", "required": True, "no_log": True},
        "ca_path": {"type": "path"},
    }, supports_check_mode=True)
    try:
        host = module.params["api_host"]
        origin = urlsplit("https://" + host)
        if not origin.hostname or origin.netloc != host or origin.username or origin.password or origin.path or origin.query or origin.fragment:
            raise ValueError("Invalid API origin")
        guards = module.params["guards"]
        if not guards or len(guards) > 8192:
            raise ValueError("Invalid guard count")
        for guard in guards:
            path = guard["path"]
            parsed = urlsplit(path)
            if not path.startswith("/") or parsed.scheme or parsed.netloc or parsed.fragment:
                raise ValueError("Invalid guard path")
            if sum(key in guard for key in ("digest", "resources", "nodes")) != 1:
                raise ValueError("Invalid guard comparison")
            if "digest" in guard and not re.fullmatch(r"[0-9a-f]{64}", guard["digest"]):
                raise ValueError("Invalid digest")

        def read(guard):
            # No method parameter or redirect can turn this verifier into a write.
            with open_url("https://" + host + "/api2/json" + guard["path"], method="GET",
                          headers={"Authorization": module.params["authorization"]},
                          validate_certs=True, ca_path=module.params["ca_path"],
                          follow_redirects="none", timeout=30) as response:
                if response.getcode() != 200:
                    raise ValueError("Identity read failed")
                document = json.load(response)
            verify_guard(guard, document["data"])

        # One module process avoids per-identity Ansible startup and TLS setup
        # serialization. Completion of every read is required before success.
        pool = ThreadPoolExecutor(max_workers=8)
        try:
            futures = [pool.submit(read, guard) for guard in guards]
            for future in as_completed(futures):
                future.result()
        finally:
            # An out-of-order failure must not wait for the entire queued
            # inventory. Only the at-most-eight running GETs can finish.
            pool.shutdown(wait=True, cancel_futures=True)
    except Exception:
        module.fail_json(msg="Cannot verify the reviewed network, permissions or guest identities. Refresh the plan before changing resources.")
    module.exit_json(changed=False, checked=len(guards))


if __name__ == "__main__":
    main()
