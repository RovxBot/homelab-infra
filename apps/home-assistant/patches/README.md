# AirTouch reliability patch

`airtouch2plus-reliability.patch` applies to
[`nathanvdh/homeassistant-airtouch2plus` at `6b0b5a90656dc13d68e6390ac6ba4989df6815fd`](https://github.com/nathanvdh/homeassistant-airtouch2plus/tree/6b0b5a90656dc13d68e6390ac6ba4989df6815fd),
using its existing `airtouch2==0.8.7` dependency. Entity IDs and config-entry data
remain compatible with the installed integration.

The patch fixes these observed code paths:

- The first AC status waited for capability discovery, then overwrote newer
  statuses. A late discovery response could restore an obsolete “on” state.
- The transport could reconnect after a failed send and report success without
  writing the command to the replacement connection. Commands now fail explicitly
  and wait up to 10 seconds for a fresh matching controller status.
- Push-only entities stayed available with old state after lost updates. The
  client requests AC/zone status every 15 seconds, marks data older than 45 seconds
  unavailable, and republishes it after recovery. It does not fabricate power state.
- Fan and dry modes omitted the power-control feature flags. Missing temperatures
  appeared as zero, and unknown mode/fan values could raise exceptions. Away/sleep
  power states also appeared off.
- Zone discovery only ran once. Late zones now register, setting zero closes a
  zone, and setting a nonzero percentage sends both power-on and the damper setting.
- Failed setup/config-flow attempts leaked clients, and background work did not
  consistently stop on unload or shutdown.

The init container fetches the exact commit, applies the reviewed patch before
replacing the installed code, and restores the old directory if replacement
fails. The installed marker includes the patch's SHA-256, so subsequent patch
changes install automatically. Only the original managed revision or a managed
patch of that revision can be replaced; unexpected installations are preserved.

The underlying library still logs unsupported controller messages such as subtype
`0x2b`; this patch does not guess their protocol meaning or add new sensor types.

## Validation

The sibling `tests` directory covers the component patch, actual Home Assistant
scripts and automations, wheel interactions, and the init-container install path.
The `Home Assistant Checks` workflow runs these tests using the Home Assistant
version in `deployment.yaml`.

Local reproduction (Python 3.14 and Node 22+):

```sh
python3 -m venv /tmp/airtouch-tests
/tmp/airtouch-tests/bin/pip install -r apps/home-assistant/tests/requirements.txt
# Use the version pinned in deployment.yaml.
/tmp/airtouch-tests/bin/pip install homeassistant==2026.9.1 airtouch2==0.8.7
git clone https://github.com/nathanvdh/homeassistant-airtouch2plus.git /tmp/airtouch-upstream
AIRTOUCH_UPSTREAM=/tmp/airtouch-upstream /tmp/airtouch-tests/bin/pytest -q apps/home-assistant/tests
node --test apps/home-assistant/tests/timer-picker.test.cjs
kubectl kustomize apps/home-assistant >/dev/null
```

Browser checks: open the dashboard with a saved nonzero duration, switch away and
back, hide/show or remount the card, scroll or use arrow keys, and press Start while
a wheel is settling. The centred value, selected highlight, and submitted duration
must agree. Test expiry and power switching with a simulated controller first;
physical operation needs a supervised check after deployment.

Home Assistant does not emit a finished event for timers that expired while it
was stopped ([timer documentation](https://www.home-assistant.io/integrations/timer/)).
The persisted deadline is therefore reconciled after startup, controller recovery,
and every 15 seconds. Failed shutdowns stay armed for retry; cancellation disarms
before cancelling the timer. A native timer pause suspends expiry enforcement,
and its new finish time is captured after resume.
