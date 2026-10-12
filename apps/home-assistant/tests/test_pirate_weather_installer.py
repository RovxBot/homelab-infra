"""Exercise installation when the reviewed commit has no upstream release tag."""
import shlex
import subprocess

import pytest
import yaml

from conftest import APP


@pytest.fixture
def pirate_installer(tmp_path):
    source = tmp_path / 'upstream'
    source.mkdir()
    subprocess.run(['git', 'init', str(source)], check=True, capture_output=True)
    component_source = source / 'custom_components/pirateweather'
    component_source.mkdir(parents=True)
    (component_source / 'manifest.json').write_text('{"domain": "pirateweather"}')
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
    subprocess.run([
        'git', '-C', str(source), '-c', 'user.name=Installer Test',
        '-c', 'user.email=installer@example.invalid', 'commit', '-m', 'Reviewed component',
    ], check=True, capture_output=True)
    revision = subprocess.check_output(
        ['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True,
    ).strip()
    config = tmp_path / 'config'
    config.mkdir()
    deployment = yaml.safe_load((APP / 'deployment.yaml').read_text())
    init = next(i for i in deployment['spec']['template']['spec']['initContainers']
                if i['name'] == 'install-pirate-weather')
    script = (init['args'][0]
              .replace('/config', str(config))
              .replace('e3515a23afdddaf89274782d176b3fb80db83bd8', revision)
              .replace('https://github.com/Pirate-Weather/pirate-weather-ha.git', shlex.quote(str(source))))
    component = config / 'custom_components/pirateweather'

    def run(code=script):
        return subprocess.run(['sh', '-ec', code], text=True, capture_output=True)

    return run, component, config, revision, script


def test_install_without_release_tag_and_idempotent_restart(pirate_installer):
    run, component, config, revision, _ = pirate_installer
    result = run()
    assert result.returncode == 0, result.stderr
    assert (component / '.gitops-revision').read_text().strip() == revision
    before = (component / 'manifest.json').stat().st_mtime_ns
    assert run().returncode == 0
    assert (component / 'manifest.json').stat().st_mtime_ns == before
    assert not list(config.glob('.pirateweather.*'))


def test_unknown_installation_is_preserved(pirate_installer):
    run, component, _, _, _ = pirate_installer
    component.mkdir(parents=True)
    (component / 'custom.py').write_text('user code')
    result = run()
    assert result.returncode != 0
    assert 'unexpected Pirate Weather' in result.stderr
    assert (component / 'custom.py').read_text() == 'user code'


def test_failed_fetch_cleans_staging_without_installing(pirate_installer):
    run, component, config, revision, script = pirate_installer
    result = run(script.replace(revision, '0' * 40))
    assert result.returncode != 0
    assert not component.exists()
    assert not list(config.glob('.pirateweather.*'))
