"""Private native-attempt entrypoint; only the enrolled fixed SSH service is used."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

# Resolve the backend package from this installed file, never from a scenario's
# working directory or its editable repository assets.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.foundation_execution import run_original  # noqa: E402
from app.core.preparation_transport import agent_configuration, protected_bytes  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request-file', required=True)
    parser.add_argument('--target-host-id', required=True)
    parser.add_argument('--agent-revision', required=True)
    arguments = parser.parse_args()
    try:
        request = json.loads(protected_bytes(Path(arguments.request_file)))
        config = agent_configuration(arguments.target_host_id, request['plan']['target_node'])
        if config.revision != arguments.agent_revision:
            raise ValueError('Original protected service configuration changed')
        result = asyncio.run(run_original(config, request))
        print(json.dumps({'status': result['status'], 'request_id': result['request_id'], 'foundation_ready': True}))
    except Exception:
        # Never publish raw native stderr, credential paths or source config.
        print('Foundation preparation is unresolved. Reconcile its original node status in Overview.', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
