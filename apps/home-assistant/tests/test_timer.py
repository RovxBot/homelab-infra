"""Run the real YAML scripts/automations with real HA timer and helper entities."""

import pytest
import pytest_asyncio
import yaml
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from conftest import APP

TIMER = 'timer.airtouch_auto_off'
ARMED = 'input_boolean.airtouch_auto_off_armed'
DEADLINE = 'input_datetime.airtouch_auto_off_at'
CLIMATE = 'climate.at2plus_ac_0'


@pytest_asyncio.fixture
async def timer_hass(hass):
    config = yaml.safe_load((APP / 'config/configuration.yaml').read_text())
    config['automation'] = [a for a in config['automation'] if a['id'].startswith('airtouch_')]
    await hass.config.async_set_time_zone('Australia/Adelaide')
    calls = []
    failure = {'on': False, 'off': False}

    async def power(call):
        desired = 'off' if call.service == 'turn_off' else 'heat'
        calls.append(desired)
        if failure['off' if desired == 'off' else 'on']:
            raise HomeAssistantError('Controller unavailable')
        hass.states.async_set(CLIMATE, desired)

    hass.services.async_register('climate', 'turn_on', power)
    hass.services.async_register('climate', 'turn_off', power)
    hass.states.async_set(CLIMATE, 'off')
    for domain in ('input_number', 'input_boolean', 'input_datetime', 'timer', 'script', 'automation'):
        assert await async_setup_component(hass, domain, config), domain
    # Start the real trigger lifecycle for the isolated helper-only instance.
    await hass.async_start()
    await hass.async_block_till_done()
    yield hass, calls, failure


async def service(hass, domain, service, data=None):
    await hass.services.async_call(domain, service, data or {}, blocking=True)
    await hass.async_block_till_done()


async def control(hass, operation, **data):
    await service(hass, 'script', 'airtouch_timer_control', {'operation': operation, **data})


async def set_deadline(hass, seconds):
    await service(hass, 'input_datetime', 'set_datetime', {'entity_id': DEADLINE, 'timestamp': dt_util.utcnow().timestamp() + seconds})


@pytest.mark.asyncio
async def test_start_uses_explicit_duration_not_delayed_helpers(timer_hass):
    hass, calls, _ = timer_hass
    await service(hass, 'script', 'airtouch_turn_on_with_timer', {'duration_minutes': 135})
    assert calls == ['heat']
    assert hass.states.get(TIMER).attributes['duration'] == '2:15:00'
    assert hass.states.is_state(ARMED, 'on')
    assert hass.states.get(DEADLINE).attributes['timestamp'] > dt_util.utcnow().timestamp()


@pytest.mark.asyncio
@pytest.mark.parametrize('duration', [0, -15, 1, 14, 16, 60.5, 540, 'unavailable', 'nan', 'inf'])
async def test_invalid_duration_does_not_turn_on_or_arm(timer_hass, duration):
    hass, calls, _ = timer_hass
    # HA's stop/error action records a trace error and ends the sequence.
    await service(hass, 'script', 'airtouch_turn_on_with_timer', {'duration_minutes': duration})
    assert calls == []
    assert hass.states.is_state(ARMED, 'off')
    assert hass.states.is_state(TIMER, 'idle')


@pytest.mark.asyncio
async def test_manual_off_cancels_timer_and_disarms(timer_hass):
    hass, calls, _ = timer_hass
    await control(hass, 'start', duration_minutes=60)
    await service(hass, 'climate', 'turn_off', {'entity_id': CLIMATE})
    assert hass.states.is_state(TIMER, 'idle')
    assert hass.states.is_state(ARMED, 'off')


@pytest.mark.asyncio
async def test_cancel_keeps_aircon_running(timer_hass):
    hass, calls, _ = timer_hass
    await control(hass, 'start', duration_minutes=60)
    await service(hass, 'script', 'airtouch_cancel_auto_off')
    assert calls == ['heat']
    assert hass.states.is_state(CLIMATE, 'heat')
    assert hass.states.is_state(ARMED, 'off')
    assert hass.states.is_state(TIMER, 'idle')


@pytest.mark.asyncio
async def test_old_off_notification_does_not_cancel_new_run(timer_hass):
    hass, _, _ = timer_hass
    await control(hass, 'start', duration_minutes=60)
    await control(hass, 'cancel_if_off')
    assert hass.states.is_state(TIMER, 'active')
    assert hass.states.is_state(ARMED, 'on')


@pytest.mark.asyncio
async def test_old_finished_event_does_not_stop_restarted_timer(timer_hass):
    hass, calls, _ = timer_hass
    await control(hass, 'start', duration_minutes=60)
    hass.bus.async_fire('timer.finished', {'entity_id': TIMER, 'finished_at': dt_util.utcnow().isoformat()})
    await hass.async_block_till_done()
    assert 'off' not in calls
    assert hass.states.is_state(TIMER, 'active')


@pytest.mark.asyncio
async def test_external_timer_cancellation_disarms(timer_hass):
    hass, calls, _ = timer_hass
    await control(hass, 'start', duration_minutes=60)
    await service(hass, 'timer', 'cancel', {'entity_id': TIMER})
    assert hass.states.is_state(ARMED, 'off')
    assert calls == ['heat']


async def elapsed(hass):
    # Simulate a timer restored idle after its deadline expired during an outage.
    hass.states.async_set(TIMER, 'idle')
    await set_deadline(hass, -30)
    await service(hass, 'input_boolean', 'turn_on', {'entity_id': ARMED})
    hass.states.async_set(CLIMATE, 'heat')


@pytest.mark.asyncio
async def test_restart_elapsed_timer_turns_off(timer_hass):
    hass, calls, _ = timer_hass
    await elapsed(hass)
    await control(hass, 'reconcile')
    assert calls == ['off']
    assert hass.states.is_state(ARMED, 'off')


@pytest.mark.asyncio
async def test_unavailable_at_expiry_retries_after_recovery(timer_hass):
    hass, calls, _ = timer_hass
    await elapsed(hass)
    hass.states.async_set(CLIMATE, 'unavailable')
    await control(hass, 'reconcile')
    assert calls == []
    assert hass.states.is_state(ARMED, 'on')
    hass.states.async_set(CLIMATE, 'heat')
    await hass.async_block_till_done()
    assert calls == ['off']
    assert hass.states.is_state(ARMED, 'off')


@pytest.mark.asyncio
async def test_failed_shutdown_keeps_deadline_armed(timer_hass):
    hass, calls, failure = timer_hass
    await elapsed(hass)
    failure['off'] = True
    with pytest.raises(HomeAssistantError):
        await control(hass, 'reconcile')
    assert hass.states.is_state(ARMED, 'on')
    failure['off'] = False
    await control(hass, 'reconcile')
    assert calls == ['off', 'off']
    assert hass.states.is_state(ARMED, 'off')


@pytest.mark.asyncio
async def test_failed_start_does_not_arm(timer_hass):
    hass, _, failure = timer_hass
    failure['on'] = True
    with pytest.raises(HomeAssistantError):
        await control(hass, 'start', duration_minutes=60)
    assert hass.states.is_state(TIMER, 'idle')
    assert hass.states.is_state(ARMED, 'off')


@pytest.mark.asyncio
async def test_paused_timer_is_not_treated_as_expired(timer_hass):
    hass, calls, _ = timer_hass
    await control(hass, 'start', duration_minutes=60)
    await service(hass, 'timer', 'pause', {'entity_id': TIMER})
    await set_deadline(hass, -1)
    await control(hass, 'reconcile')
    assert calls == ['heat']
    assert hass.states.is_state(TIMER, 'paused')
    await service(hass, 'timer', 'start', {'entity_id': TIMER})
    await control(hass, 'reconcile')
    assert hass.states.get(DEADLINE).attributes['timestamp'] > dt_util.utcnow().timestamp()


@pytest.mark.asyncio
async def test_disarmed_deadline_cannot_turn_off(timer_hass):
    hass, calls, _ = timer_hass
    await elapsed(hass)
    await control(hass, 'cancel')
    await control(hass, 'reconcile')
    assert calls == []


@pytest.mark.asyncio
async def test_real_finished_event_shuts_down(timer_hass):
    hass, calls, _ = timer_hass
    await control(hass, 'start', duration_minutes=15)
    # Shorten the real timer to exercise its scheduled finished event.
    await service(hass, 'timer', 'start', {'entity_id': TIMER, 'duration': 1})
    await control(hass, 'reconcile')
    import asyncio
    await asyncio.sleep(1.2)
    await hass.async_block_till_done()
    assert calls == ['heat', 'off']
    assert hass.states.is_state(TIMER, 'idle')
    assert hass.states.is_state(ARMED, 'off')


@pytest.mark.asyncio
async def test_future_deadline_restores_missing_countdown(timer_hass):
    hass, calls, _ = timer_hass
    hass.states.async_set(CLIMATE, 'heat')
    await set_deadline(hass, 3600)
    await service(hass, 'input_boolean', 'turn_on', {'entity_id': ARMED})
    await control(hass, 'reconcile')
    assert hass.states.is_state(TIMER, 'active')
    assert calls == []


@pytest.mark.asyncio
async def test_timer_cancel_queued_during_start_wins(timer_hass):
    hass, calls, _ = timer_hass
    import asyncio
    started = asyncio.Event()
    release = asyncio.Event()
    async def slow_start(call):
        started.set()
        await release.wait()
        hass.states.async_set(CLIMATE, 'heat')
    hass.services.async_register('climate', 'turn_on', slow_start)
    start = asyncio.create_task(hass.services.async_call('script', 'airtouch_timer_control', {'operation': 'start', 'duration_minutes': 60}, blocking=True))
    await started.wait()
    cancel = asyncio.create_task(hass.services.async_call('script', 'airtouch_timer_control', {'operation': 'cancel'}, blocking=True))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(start, cancel)
    await hass.async_block_till_done()
    assert hass.states.is_state(TIMER, 'idle')
    assert hass.states.is_state(ARMED, 'off')
    assert hass.states.is_state(CLIMATE, 'heat')


@pytest.mark.asyncio
async def test_native_finish_action_shuts_down_immediately(timer_hass):
    hass, calls, _ = timer_hass
    await control(hass, 'start', duration_minutes=60)
    await service(hass, 'timer', 'finish', {'entity_id': TIMER})
    assert calls == ['heat', 'off']
    assert hass.states.is_state(TIMER, 'idle')
    assert hass.states.is_state(ARMED, 'off')
