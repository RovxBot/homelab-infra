# Platform-owner exception decisions

All six scopes and dates remain unchanged in both APIs. Five historical expiries
require an explicit decision from the platform owner (RovxBot). This draft neither
renews nor revokes them. Expiry is metadata; the migration must not silently turn
it into a new automatic revocation rule.

| Exception / date | Exact affected scope | Existing justification | Expected consequence of removal |
| --- | --- | --- | --- |
| `chart-managed-workloads` — **expired 2026-08-31** | Pod, Deployment, StatefulSet, DaemonSet, Job and CronJob in `longhorn-system`; owner, resource budgets and probes only | Chart templates do not consistently expose those fields. | Recreated or newly violating Longhorn workloads can be denied until their templates satisfy labels, budgets and probes. |
| `gpu-operator-workloads` — **expired 2026-10-31** | The same six workload kinds in `gpu-operator`; six original owner/budget/hardening/host-network/image/probe rules only | NVIDIA GPU Operator/NFD require host hardware access; Talos supplies the driver/toolkit. | Hardware discovery/operator pods may fail admission for host access or other outstanding template violations. |
| `mutable-image-tags` — **expired 2026-08-31** | Digest rule only: all resources in `longhorn-system`, `wireguard`, `wotlk`; plus Deployment/CronJob/StatefulSet named `cloudflared`, `grimguzzler-registration`, `homepage-assets` or `pod-report` within the original `cloudflared`, `homepage`, `wotlk` namespace union | Tag-based updates and upstream-controlled tags remain in use. | New/recreated matching workloads require digest references; tag-only updates may be denied. |
| `privileged-namespaces` — **expired 2026-08-31** | Hardening and host-network rules only, original resources in `longhorn-system`, `wireguard`, `wotlk` | Storage, networking and game workloads need privileged runtime behavior. | Matching new/recreated pods or templates may be denied until their runtime/network requirements change. |
| `wotlk-latest-tag` — **expired 2026-08-31** | Latest-tag rule only: CronJob `wotlk-weekly-restart` and Job `wotlk-weekly-restart*` in `wotlk` | Weekly restart intentionally follows the latest built image. | The weekly CronJob or newly created matching Jobs may be denied while using `latest`. |
| `home-assistant` — 2027-02-28 | Hardening and host-network rules only: Deployment `home-assistant` in namespace `home-assistant` | LAN multicast needs host networking; upstream S6 starts as UID 0. Capabilities remain dropped, privilege escalation forbidden and seccomp RuntimeDefault. | Recreating/updating the Deployment with those violations can be denied; multicast discovery requires an alternative networking design. |

Removal does not terminate existing running pods. Existing-violation UPDATE
behavior is preserved; precise effects depend on the operation and whether a new
violation is introduced. Do not treat this table as approval to remove an exception.

For **each expired row**, record owner, chosen action (remediate and revoke, or
explicitly approved time-bounded renewal), justification, tracking issue and
reviewed date/scope change in a separate commit. The future Home Assistant date
requires no automatic adjustment. Native accountability preserves metadata/issue
tracking requirements across both APIs; it does not create tracking issues itself.
