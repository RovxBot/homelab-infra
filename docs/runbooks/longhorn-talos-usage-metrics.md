# Optional Longhorn usage reporting on Talos

The manager's optional anonymous usage collector tries to classify each disk's
device type from host mount tables. On this Talos cluster it cannot resolve the
bind-mounted `/var/lib/longhorn/` path and reports a warning while disk readiness
and application I/O remain healthy. The same collection code exists in 1.13.0.

Set `allow-collecting-longhorn-usage-metrics` to `false` to stop this optional
device classification and usage reporting. Longhorn continues version checking
and exposes its operational metrics. This setting requires no workload restart,
disk changes, snapshot operations or backup-store requests.

Validate the Setting's value and `status.applied`, disk Ready conditions and
manager logs after reconciliation. Actual disk, replica and backup errors still
require investigation.

References: [the usage setting definition](https://github.com/longhorn/longhorn-manager/blob/v1.11.1/types/setting.go)
and [the collector's opt-out](https://github.com/longhorn/longhorn-manager/blob/v1.11.1/controller/setting_controller.go).
