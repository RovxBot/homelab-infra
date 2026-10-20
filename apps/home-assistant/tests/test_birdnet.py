"""Check the alert boundary against real Home Assistant templates."""

from datetime import timedelta

import pytest
import yaml
from homeassistant.components.mqtt.sensor import PLATFORM_SCHEMA_MODERN
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.template import Template
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from conftest import APP


@pytest.mark.asyncio
async def test_aggregate_sensor_uses_supported_mqtt_schema(hass):
    package = yaml.safe_load((APP / 'config/packages/birdnet.yaml').read_text())
    for sensor in package['mqtt']['sensor']:
        PLATFORM_SCHEMA_MODERN(sensor)


@pytest.fixture
def bird_alert():
    package = yaml.safe_load((APP / 'config/packages/birdnet.yaml').read_text())
    return package['automation'][0]


@pytest.mark.asyncio
@pytest.mark.parametrize('payload,expected', [
    ({'sourceName': 'Shed', 'CommonName': 'Australian Magpie', 'Confidence': 0.95}, True),
    ({'sourceName': 'Shed front', 'CommonName': 'Australian Magpie', 'Confidence': 0.90}, True),
    ({'sourceName': 'Shed', 'CommonName': 'Australian Magpie', 'Confidence': 0.89}, False),
    ({'sourceName': 'Other camera', 'CommonName': 'Australian Magpie', 'Confidence': 0.99}, False),
    ({'sourceName': 'Shed', 'CommonName': 'Australian Magpie', 'Confidence': 'invalid'}, False),
    ({'sourceName': 'Shed', 'Confidence': 0.99}, False),
    ({}, False),
    ([], False),
])
async def test_only_named_camera_and_confident_bird_alerts(hass, bird_alert, payload, expected):
    hass.states.async_set('input_number.birdnet_alert_confidence', '90')
    template = Template(bird_alert['conditions'][1]['value_template'], hass)
    assert template.async_render({'trigger': {'payload_json': payload}}) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize('minutes_ago,expected', [(None, True), (5, False), (31, True)])
async def test_shared_alert_cooldown(hass, bird_alert, minutes_ago, expected):
    hass.states.async_set('input_number.birdnet_alert_cooldown', '30')
    last = None if minutes_ago is None else dt_util.utcnow() - timedelta(minutes=minutes_ago)
    template = Template(bird_alert['conditions'][2]['value_template'], hass)
    assert template.async_render({'this': {'attributes': {'last_triggered': last}}}) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize('failed_phone', ['sam', 'rachael'])
async def test_phone_failure_preserves_ha_alert_and_other_phone(hass, bird_alert, failed_phone):
    calls = []

    async def persist(call):
        calls.append(('ha', call.data))

    async def sam(call):
        calls.append(('sam', call.data))
        if failed_phone == 'sam':
            raise HomeAssistantError('Phone unavailable')

    async def rachael(call):
        calls.append(('rachael', call.data))
        if failed_phone == 'rachael':
            raise HomeAssistantError('Local push disconnected')

    hass.services.async_register('persistent_notification', 'create', persist)
    hass.services.async_register('notify', 'mobile_app_pixel_6a', sam)
    hass.services.async_register('notify', 'send_message', rachael)
    config = {'script': {'birdnet_alert_delivery': {'sequence': bird_alert['actions']}}}
    assert await async_setup_component(hass, 'script', config)
    await hass.services.async_call('script', 'birdnet_alert_delivery', {
        'trigger': {'payload_json': {
            'sourceName': 'Shed front', 'CommonName': 'Australian Magpie',
            'Confidence': 0.95, 'Time': '11:00:00',
        }},
    }, blocking=True)
    await hass.async_block_till_done()
    assert {name for name, _ in calls} == {'ha', 'sam', 'rachael'}
    assert all('Australian Magpie' in data['message'] for _, data in calls)
