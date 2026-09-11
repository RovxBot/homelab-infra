import asyncio
from dataclasses import replace
from time import monotonic
from unittest.mock import AsyncMock, Mock

import pytest
from airtouch2.at2plus import At2PlusAircon, At2PlusGroup
from airtouch2.protocol.at2plus.enums import AcPower, AcMode, AcFanSpeed, GroupPower, GroupSetPower, GroupSetDamper
from airtouch2.protocol.at2plus.messages.AcStatus import AcStatus, AcStatusMessage
from homeassistant.components.climate import HVACMode, ClimateEntityFeature
from homeassistant.exceptions import HomeAssistantError, ConfigEntryNotReady


def status(power=AcPower.ON, mode=AcMode.HEAT):
    return AcStatus(0, power, mode, AcFanSpeed.LOW, 22, 21, False, False, False, False, 0)


@pytest.fixture
def client(integration):
    from custom_components.airtouch2plus.reliable_client import ReliableAirTouchClient
    c = ReliableAirTouchClient('test.invalid')
    c._client = Mock(connected=True, stop=AsyncMock())
    c.send = AsyncMock()
    return c


@pytest.fixture
def climate(client):
    from custom_components.airtouch2plus.Airtouch2PlusClimateEntity import Airtouch2PlusClimateEntity
    ac = At2PlusAircon(status(), client)
    client.aircons_by_id[0] = ac
    client._status_times[id(ac)] = monotonic()
    entity = Airtouch2PlusClimateEntity(ac)
    entity.async_write_ha_state = Mock()
    return entity


@pytest.mark.asyncio
async def test_late_ability_does_not_restore_stale_power(client):
    ability = asyncio.Future()
    client._request_ac_ability = AsyncMock(side_effect=lambda _: ability)
    # AsyncMock returns the Future itself, so explicitly await it.
    async def load(_):
        return await ability
    client._request_ac_ability.side_effect = load
    await client._handle_status_message(AcStatusMessage([status()]))
    await asyncio.sleep(0)
    await client._handle_status_message(AcStatusMessage([status(AcPower.OFF)]))
    ac = client.aircons_by_id[0]
    ability.set_result(Mock())
    await asyncio.sleep(0)
    assert ac.status.power == AcPower.OFF
    await client.stop()


@pytest.mark.parametrize('mode', list(AcMode))
def test_power_supported_in_every_mode(climate, mode):
    climate._ac.status = replace(climate._ac.status, mode=mode)
    assert climate.supported_features & ClimateEntityFeature.TURN_ON
    assert climate.supported_features & ClimateEntityFeature.TURN_OFF


@pytest.mark.parametrize('power,expected', [(AcPower.OFF, HVACMode.OFF), (AcPower.AWAY_OFF, HVACMode.OFF), (AcPower.ON, HVACMode.HEAT), (AcPower.SLEEP, HVACMode.HEAT), (AcPower.AWAY_ON, HVACMode.HEAT)])
def test_power_mapping(climate, power, expected):
    climate._ac.status = replace(climate._ac.status, power=power)
    assert climate.hvac_mode == expected


def test_missing_values_do_not_crash_or_report_zero(climate):
    climate._ac.status = replace(climate._ac.status, temperature=None, set_point=None, fan_speed=AcFanSpeed.UNCHANGED, mode=AcMode.NOT_AVAILABLE)
    assert climate.current_temperature is None
    assert climate.target_temperature is None
    assert climate.fan_mode is None
    assert climate.hvac_mode is None


def test_unavailable_and_recovery(climate, client):
    assert climate.available
    client._client.connected = False
    assert not climate.available
    client._client.connected = True
    client._status_times[id(climate._ac)] = monotonic() - 46
    assert not climate.available
    client._status_times[id(climate._ac)] = monotonic()
    assert climate.available
    climate._ac.status = replace(climate._ac.status, power=AcPower.NOT_AVAILABLE)
    assert not climate.available


@pytest.mark.asyncio
async def test_power_off_requires_fresh_confirmation_even_if_cached_off(climate, client):
    ac = climate._ac
    ac.status = status(AcPower.OFF)
    task = asyncio.create_task(climate.async_set_hvac_mode(HVACMode.OFF))
    await asyncio.sleep(0)
    assert not task.done()
    assert client.send.call_args_list[0].args[0].settings[0].power.name == 'OFF'
    await client._handle_status_message(AcStatusMessage([status(AcPower.OFF)]))
    await task
    assert climate.hvac_mode == HVACMode.OFF


@pytest.mark.asyncio
async def test_command_timeout_is_not_success(climate, client, monkeypatch):
    import custom_components.airtouch2plus.reliable_client as module
    monkeypatch.setattr(module, 'COMMAND_TIMEOUT', .01)
    with pytest.raises(HomeAssistantError, match='did not confirm'):
        await climate.async_turn_off()
    assert climate.hvac_mode == HVACMode.HEAT
    assert not climate._ac._callbacks


@pytest.mark.asyncio
async def test_command_connection_error_surfaces(climate, client):
    client.send.side_effect = ConnectionError('lost connection')
    with pytest.raises(HomeAssistantError):
        await climate.async_turn_off()
    assert not climate._ac._callbacks


@pytest.mark.asyncio
async def test_mode_is_sent_atomically_with_power_on(climate, client):
    task = asyncio.create_task(climate.async_set_hvac_mode(HVACMode.COOL))
    await asyncio.sleep(0)
    settings = client.send.call_args_list[0].args[0].settings[0]
    assert settings.power.name == 'ON'
    assert settings.mode.name == 'COOL'
    await client._handle_status_message(AcStatusMessage([status(mode=AcMode.COOL)]))
    await task


@pytest.mark.asyncio
async def test_transport_does_not_silently_drop_command(integration):
    from custom_components.airtouch2plus.reliable_client import ReliableTransport
    transport = ReliableTransport('test.invalid', 9200, AsyncMock(), AsyncMock())
    transport._reader = Mock(at_eof=Mock(return_value=False))
    transport._writer = Mock(is_closing=Mock(return_value=False), drain=AsyncMock(side_effect=ConnectionResetError))
    transport._try_reconnect = AsyncMock()
    with pytest.raises(ConnectionResetError):
        await transport.send(Mock(to_bytes=Mock(return_value=b'command')))
    transport._writer.write.assert_called_once_with(b'command')
    transport._try_reconnect.assert_not_called()


@pytest.mark.asyncio
async def test_transport_stop_before_run_closes_socket(integration):
    from custom_components.airtouch2plus.reliable_client import ReliableTransport
    transport = ReliableTransport('test.invalid', 9200, AsyncMock(), AsyncMock())
    transport._writer = Mock(wait_closed=AsyncMock())
    await transport.stop()
    transport._writer.close.assert_called_once()


@pytest.mark.asyncio
async def test_setup_timeout_cleans_up_and_retries(integration, hass, monkeypatch):
    import custom_components.airtouch2plus as module
    client = Mock(connect=AsyncMock(return_value=True), wait_for_ac=AsyncMock(side_effect=TimeoutError), stop=AsyncMock())
    monkeypatch.setattr(module, 'At2PlusClient', Mock(return_value=client))
    with pytest.raises(ConfigEntryNotReady):
        await module.async_setup_entry(hass, Mock(data={'host': 'test.invalid'}))
    client.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_config_flow_failure_cleans_up(integration, hass, monkeypatch):
    import custom_components.airtouch2plus.config_flow as module
    client = Mock(connect=AsyncMock(return_value=True), wait_for_ac=AsyncMock(side_effect=TimeoutError), stop=AsyncMock())
    monkeypatch.setattr(module, 'At2PlusClient', Mock(return_value=client))
    with pytest.raises(module.CannotConnect):
        await module.validate_input(hass, {'host': 'test.invalid'})
    client.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_zones_discovered_after_setup_are_added(client, hass):
    from custom_components.airtouch2plus.fan import async_setup_entry
    entry = Mock(entry_id='test')
    hass.data['airtouch2plus'] = {'test': client}
    add = Mock()
    await async_setup_entry(hass, entry, add)
    assert not add.called
    group = Mock(status=Mock(id=1), _client=client)
    client.groups_by_id[1] = group
    for cb in client._new_group_callbacks:
        cb()
    assert len(add.call_args.args[0]) == 1
    for cb in client._new_group_callbacks:
        cb()
    assert add.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('percentage', [0, 35, 100])
async def test_zone_speed_controls_power(client, percentage):
    from custom_components.airtouch2plus.Airtouch2PlusGroupEntity import AirTouch2PlusGroupEntity
    # Only fields used by At2PlusGroup are needed here.
    group = At2PlusGroup(Mock(id=0, power=GroupPower.OFF, damp=20), client)
    entity = AirTouch2PlusGroupEntity(group)
    entity.async_write_ha_state = Mock()
    task = asyncio.create_task(entity.async_set_percentage(percentage))
    await asyncio.sleep(0)
    settings = client.send.call_args_list[0].args[0].settings[0]
    assert settings.power == (GroupSetPower.OFF if percentage == 0 else GroupSetPower.ON)
    if percentage:
        assert settings.damp_mode == GroupSetDamper.SET
        assert settings.damp == percentage
    client._status_times[id(group)] = monotonic()
    group._update_status(Mock(id=0, power=GroupPower.OFF if percentage == 0 else GroupPower.ON, damp=percentage))
    await task
