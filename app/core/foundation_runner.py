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


async def run_attempt(configuration, request, *, network_configuration=None, network_request=None):
    result = await run_original(configuration, request)
    if network_request is not None:
        from app.core.environment_network_execution import run_original as run_networks
        network = await run_networks(network_configuration, network_request)
        result = {**result, 'network_ready': network['network_ready']}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request-file', required=True)
    parser.add_argument('--target-host-id', required=True)
    parser.add_argument('--agent-revision', required=True)
    arguments = parser.parse_args()
    try:
        request = json.loads(protected_bytes(Path(arguments.request_file)))
        bundle = None
        if request.get('protocol') == 'range42-foundation-attempt-v1':
            if set(request) != {'protocol', 'foundation', 'environment_networks'}:
                raise ValueError('Original foundation attempt envelope required')
            bundle, request = request['environment_networks'], request['foundation']
            if bundle['request']['foundation_request'] != request:
                raise ValueError('Original foundation and environment must match')
        config = agent_configuration(arguments.target_host_id, request['plan']['target_node'])
        if config.revision != arguments.agent_revision:
            raise ValueError('Original protected service configuration changed')
        network_configuration = None
        if bundle is not None:
            from app.core.environment_network_transport import configuration
            network_configuration = configuration(arguments.target_host_id, request['plan']['target_node'])
            if network_configuration.revision != bundle['transport_revision']:
                raise ValueError('Original optional network service changed')
        result = asyncio.run(run_attempt(config, request, network_configuration=network_configuration,
            network_request=bundle['request'] if bundle is not None else None))
        print(json.dumps({'status': result['status'], 'request_id': result['request_id'], 'foundation_ready': True,
            **({'network_ready': result['network_ready']} if bundle is not None else {})}))
    except Exception:
        # Never publish raw native stderr, credential paths or source config.
        print('Foundation preparation is unresolved. Reconcile its original node status in Overview.', file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
