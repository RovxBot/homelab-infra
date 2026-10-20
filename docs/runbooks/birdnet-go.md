# BirdNET-Go, UniFi camera audio and Home Assistant

BirdNET-Go runs local BirdNET v2.4 inference against the existing **Shed front**
and **Shed** UniFi Protect RTSPS feeds. Both cameras already had their High
channel and microphones enabled. No changes to camera recording or microphone
settings are required. FFmpeg decodes audio inside the unprivileged pod; no
sound device, GPU, host networking or inbound camera connection is needed.

```text
UniFi Protect ──RTSPS/TCP──► BirdNET-Go ──MQTT──► Mosquitto ──► Home Assistant
                                ▲                              Birds dashboard
                      Cloudflare Tunnel                        phone alerts
                                ▲
                  birdnet.cooked.beer + Entra OIDC
```

## Access

- BirdNET: <https://birdnet.cooked.beer>
- Home Assistant: <https://home.cooked.beer/homelab-birds/birds>
- Entra registration: **BirdNET-Go OIDC**, application ID
  `7638d202-18e2-4d73-9b39-a10b3b5d8678`.
- Tenant: `345cd18b-5bee-4ac4-b729-a3f7059a28f3`.
- Web callback: `https://birdnet.cooked.beer/auth/openid-connect/callback`.
- Single tenant, enterprise-app assignment required, assigned directly to
  Sam and Rachael. The native OIDC email allowlist also limits both accounts.
- Only `openid`, `profile` and `email` delegated sign-in scopes are consented.
  Existing tenant sign-in/MFA policies apply; this app adds no MFA exemption.
- Private mode protects detection data. Public live audio, subnet bypass and
  password login are disabled. BirdNET-Go currently gives allowed users control
  of settings; the Entra assignments are not separate viewer/admin roles.

The DNS record is a proxied CNAME to the existing `homelab-k8s` Cloudflare
tunnel. The tunnel routes HTTP to the internal BirdNET service. MQTT has only a
ClusterIP service; it has no Cloudflare route, NodePort or LAN listener.

## Home Assistant

Connect the MQTT integration to `mosquitto.mqtt.svc.cluster.local`, port `1883`,
using username `homeassistant`. Its password is the
`homeassistant-password` key of Secret `mqtt/mosquitto` and is also encrypted
in `secrets/mosquitto.enc.yaml`. Do not paste the password in issues or Git.
Keep discovery enabled with the default `homeassistant` prefix and default
birth/will topic `homeassistant/status`.

Native MQTT discovery supplies each camera's latest species, confidence,
scientific name and sound level. The declarative Birds dashboard also has an
aggregate latest detection and 24-hour history. That history shows species
state changes; the complete detection history, audio clips and statistics live
in BirdNET-Go. Recorded bird clips are FLAC; speech filtering and Privacy Guard
are enabled. BirdWeather uploads and Sentry telemetry are disabled.

The Birds dashboard controls phone alerts to `notify.pixel_6a` and
`notify.pixel_8a`. The alert toggle restores its last state; it starts off on a
fresh installation. Turn it on when ready. Defaults are at least **90%**
confidence and one alert every **30 minutes across both cameras**. Threshold
and cooldown helpers can be adjusted on the dashboard. Results are still
collected while alerts are off. The detection threshold is 80%; the alert
threshold is independent.

Both discovery and the aggregate sensor receive results over MQTT. Automation
subscribes only to `birdnet`, never `birdnet/#`, so the per-camera discovery
topics do not produce duplicate alerts. Detection messages are not retained.
Broker ACLs separate the BirdNET publisher and Home Assistant subscriber.

## Configuration and storage

`apps/birdnet-go/config.example.yaml` documents the configured structure without
credentials or camera tokens. The actual `config.yaml`, including the precise
station location copied from Home Assistant, lives in the SOPS-encrypted
`secrets/birdnet-go.enc.yaml`. The Flux age identity is cluster-only.

The init container copies that encrypted Git-managed configuration to the
writable config PVC on **every pod start**. UI settings changes work immediately
but must be copied back to the encrypted Git configuration to survive a restart.
This includes model selections made in the model gallery. A Secret update alone
does not restart the pod; reconcile Git, then roll the Deployment when changing
credentials or startup settings. Keep the example in sync with nonsecret
configuration changes.

The 1 GiB config PVC and 20 GiB data PVC use Longhorn. SQLite and exported clips
live under `/data`. The built-in BirdNET model is supplied by the image; the
model gallery resolves its model directory under `/config`. Grow that PVC
before installing large additional models. Clip cleanup starts at 75% usage of
the data filesystem and can remove every clip; database and model growth still
need monitoring. This is a capacity policy, not a fixed retention period. The
namespace and PVCs disable Flux pruning. There is one replica with a Recreate
strategy to avoid concurrent SQLite writers.

Mosquitto has a separate retained 1 GiB Longhorn PVC for discovery and retained
availability messages. Its salted PBKDF2-SHA512 password hashes are encrypted in
Git. Neither passwords nor ACLs rely on NetworkPolicy: Cilium currently has
policy enforcement disabled, and the manifests document intended ingress.

## Verification and recovery

```bash
kubectl -n flux-system get kustomization infrastructure-mosquitto apps-birdnet-go apps-home-assistant
kubectl -n mqtt get deployment,pvc
kubectl -n birdnet-go get deployment,pvc
```

Confirm audio activity on both sources in BirdNET, detections in the Birds
dashboard, both discovered source devices in Home Assistant's MQTT integration,
and an Entra sign-in from a fresh session. Signed-out requests to detection APIs
must return 401 and live audio must require sign-in. A healthy TCP probe proves
only that the web process is listening; source audio and inference health must
also be checked in BirdNET's System Health view.

Initial live validation confirmed both camera streams receiving audio, active
BirdNET inference, MQTT discovery of both source devices and their changing
sound levels, HTTPS health returning 200, and anonymous detection requests
returning 401 both internally and through Cloudflare. The Entra authorization
redirect uses the tenant-specific endpoint, the registered callback and PKCE
S256. A complete browser sign-in still needs verification after local DNS
caches expire. Public Google and Cloudflare DNS already resolve the hostname.

Protect reports an audio sample rate of 11,025 Hz for these G3 streams. BirdNET
resamples it to 48 kHz, but this cannot restore frequencies above about 5.5 kHz;
detection of higher-pitched calls is limited by the camera microphones.

If streams stop, first confirm the recorder address and enabled camera stream in
UniFi Protect. Obtain a replacement URL through Home Assistant's existing
Protect API connection or Protect's camera settings, update the encrypted
configuration, then restart BirdNET. Stream URLs contain access tokens and must
remain secret. High-quality video can consume network bandwidth even though
only audio is analyzed. If needed, enable a lower-bandwidth channel in Protect
after confirming that it includes the same audio track.

The initial Entra client secret expires **20 October 2027 UTC**. Create its
replacement before expiry, update the encrypted BirdNET config, restart and
verify fresh sign-in before retiring the old credential. Rotate the two MQTT
passwords together with their broker hashes and clients when required.

For rollout problems, revert the PR and reconcile Flux. The new namespaces and
PVCs remain available for recovery. Remove the BirdNET DNS record and disable
its enterprise application if abandoning the service. Avoid downgrading the
BirdNET binary against a newer database/configuration: restore a matching
backup first. PVC retention is not a backup; arrange a consistent SQLite/data
backup before future version or schema upgrades.

## Upstream references

- [BirdNET-Go 20261014 release](https://github.com/tphakala/birdnet-go/releases/tag/20261014)
- [Release configuration types, including native OIDC and private mode](https://github.com/tphakala/birdnet-go/blob/20261014/internal/conf/config.go)
- [Per-source MQTT topics and HA discovery](https://github.com/tphakala/birdnet-go/blob/20261014/internal/mqtt/topics.go)
- [Home Assistant MQTT integration](https://www.home-assistant.io/integrations/mqtt/)
- [Entra application assignment](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/assign-user-or-group-access-portal)
