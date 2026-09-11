"""Exercise the reviewed patch against the pinned upstream integration."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
import pytest_asyncio

APP = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='session')
def integration(tmp_path_factory):
    source = os.environ.get('AIRTOUCH_UPSTREAM')
    if not source:
        pytest.fail('Set AIRTOUCH_UPSTREAM to a clone of nathanvdh/homeassistant-airtouch2plus')
    dest = tmp_path_factory.mktemp('airtouch-component')
    # Archive the commit, not a potentially modified developer checkout.
    archive = subprocess.check_output(['git', '-C', source, 'archive', '6b0b5a90656dc13d68e6390ac6ba4989df6815fd'])
    subprocess.run(['tar', '-x', '-C', str(dest)], input=archive, check=True)
    subprocess.run(['git', 'apply', str(APP / 'patches/airtouch2plus-reliability.patch')], cwd=dest, check=True)
    sys.path.insert(0, str(dest))
    return dest


@pytest_asyncio.fixture
async def hass(tmp_path):
    from homeassistant.core import HomeAssistant
    from homeassistant import loader
    instance = HomeAssistant(str(tmp_path))
    instance.config.skip_pip = True
    loader.async_setup(instance)
    from homeassistant.config_entries import ConfigEntries
    from homeassistant.bootstrap import async_load_base_functionality
    instance.config_entries = ConfigEntries(instance, {})
    assert await async_load_base_functionality(instance)
    yield instance
    await instance.async_stop(force=True)
