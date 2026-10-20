"""Check the alert boundary against real Home Assistant templates."""

from datetime import timedelta

import pytest
import yaml
from homeassistant.components.mqtt.sensor import PLATFORM_SCHEMA_MODERN
from homeassistant.helpers.template import Template
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
