import json
import subprocess

SPEC = dict(id='alpha', domain='alpha.example.test', vmid_start=31000,
            subnet='10.81.0.0/24', gateway='10.81.0.1', bridge='r42alpha',
            template_vmid=9221, node='pve', ssh_user='alice', profile='core')


def test_stack_preview_requires_installed_component_runtime(client, monkeypatch):
    monkeypatch.delenv('RANGE42_PLATFORM_PLAYBOOKS_DIR', raising=False)
    response = client.post('/v1/platform/components/preview', json=SPEC)
    assert response.status_code == 503
    assert response.json()['code'] == 'PLATFORM_RUNTIME_UNAVAILABLE'


def test_stack_preview_uses_only_operator_installed_code(client, monkeypatch, tmp_path):
    (tmp_path / 'range42_stack').mkdir()
    (tmp_path / 'range42_stack/project.py').write_text('# installed component')
    monkeypatch.setenv('RANGE42_PLATFORM_PLAYBOOKS_DIR', str(tmp_path))
    result = dict(version=1, plan={'id': 'alpha'}, scenario={'version': 1, 'path': 'platforms/alpha'}, files={})
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, json.dumps(result), '')
    monkeypatch.setattr(subprocess, 'run', run)
    response = client.post('/v1/platform/components/preview', json=SPEC)
    assert response.status_code == 200, response.text
    assert response.json() == result
    assert calls[0][0][-2:] == ['-m', 'range42_stack.project']
    assert json.loads(calls[0][1]['input']) == {**SPEC, 'dns': '1.1.1.1'}
    assert calls[0][1]['env']['PYTHONPATH'] == str(tmp_path)
    for patch in [{'vmid_start': True}, {'id': '../escape'}, {'source_root': '/tmp/evil'}]:
        assert client.post('/v1/platform/components/preview', json={**SPEC, **patch}).status_code == 422
    assert len(calls) == 1
