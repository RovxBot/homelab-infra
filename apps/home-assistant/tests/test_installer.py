"""Exercise the actual init-container script on temporary config volumes."""
import hashlib
import os
from pathlib import Path
import shlex
import subprocess

import pytest
import yaml

from conftest import APP

BASE = '6b0b5a90656dc13d68e6390ac6ba4989df6815fd'


@pytest.fixture
def installer(tmp_path):
    source = os.environ['AIRTOUCH_UPSTREAM']
    config = tmp_path / 'config'
    config.mkdir()
    assets = tmp_path / 'assets'
    assets.mkdir()
    patch = (APP / 'patches/airtouch2plus-reliability.patch').read_bytes()
    (assets / 'airtouch2plus-reliability.patch').write_bytes(patch)
    deployment = yaml.safe_load((APP / 'deployment.yaml').read_text())
    init = next(i for i in deployment['spec']['template']['spec']['initContainers'] if i['name'] == 'install-airtouch2plus')
    script = init['args'][0].replace('/config', str(config)).replace('/gitops-assets', str(assets)).replace('https://github.com/nathanvdh/homeassistant-airtouch2plus.git', shlex.quote(source))
    component = config / 'custom_components/airtouch2plus'
    expected = BASE + '-homelab.' + hashlib.sha256(patch).hexdigest()
    def run(code=script):
        return subprocess.run(['sh', '-ec', code], text=True, capture_output=True)
    return run, component, expected, script


def test_fresh_install_and_idempotent_restart(installer):
    run, component, expected, _ = installer
    result = run()
    assert result.returncode == 0, result.stderr
    assert (component / '.gitops-revision').read_text().strip() == expected
    assert (component / 'reliable_client.py').is_file()
    before = (component / 'reliable_client.py').stat().st_mtime_ns
    assert run().returncode == 0
    assert (component / 'reliable_client.py').stat().st_mtime_ns == before


@pytest.mark.parametrize('revision', [BASE, BASE + '-homelab.previous'])
def test_upgrades_known_managed_versions(installer, revision):
    run, component, expected, _ = installer
    component.mkdir(parents=True)
    (component / '.gitops-revision').write_text(revision)
    (component / 'old.py').write_text('old code')
    result = run()
    assert result.returncode == 0, result.stderr
    assert not (component / 'old.py').exists()
    assert (component / '.gitops-revision').read_text().strip() == expected


def test_unknown_installation_is_preserved(installer):
    run, component, _, _ = installer
    component.mkdir(parents=True)
    (component / 'custom.py').write_text('user code')
    result = run()
    assert result.returncode != 0
    assert 'unexpected AirTouch' in result.stderr
    assert (component / 'custom.py').read_text() == 'user code'


def test_failed_swap_restores_previous_component(installer):
    run, component, _, script = installer
    component.mkdir(parents=True)
    (component / '.gitops-revision').write_text(BASE)
    (component / 'previous.py').write_text('working version')
    script = script.replace('mv "$staging/repository/custom_components/airtouch2plus" "$component"', 'false')
    result = run(script)
    assert result.returncode != 0
    assert (component / 'previous.py').read_text() == 'working version'
    assert (component / '.gitops-revision').read_text() == BASE
