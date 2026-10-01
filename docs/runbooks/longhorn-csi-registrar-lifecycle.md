# CSI registrar shutdown hook

The October 2026 audit found eight failed CSI preStop hooks. A read-only exec
confirmed the chart's `longhornio/csi-node-driver-registrar:v2.16.0` image has no
`/bin/sh`, although Longhorn supplies a shell-based cleanup hook.

The upstream registrar handles SIGTERM and removes its registration socket
before exiting. A narrowly scoped Kyverno admission mutation removes the
redundant registrar hook on `longhorn-system/longhorn-csi-plugin` DaemonSets.
It matches the known v2.16.0/v2.17.0/v2.18.0 images, expected container name and shell
command. The Longhorn CSI plugin's own socket-cleanup hook remains in place.
Longhorn's driver deployer recreates this DaemonSet at upgrades, so a manual
patch alone would not persist.

Longhorn 1.12.1 ships registrar v2.17.0; Longhorn 1.13.0 ships v2.18.0.
Both still configure the shell hook, and both registrar versions implement
native SIGTERM cleanup. Cover each image before the corresponding upgrade.

After the policy PR is merged and Flux has installed the policy, request a
server dry run of a metadata annotation on the existing DaemonSet. Inspect the
returned template: only the registrar preStop hook should disappear. Then apply
the same annotation to trigger one node at a time through the existing
`maxUnavailable: 1` strategy. Verify eight ready CSI pods, registration on all
eight CSINodes, all current claims Bound, and healthy volume replicas. Check
the next CSI rollout for new FailedPreStopHook events. Old pods still have the
old hook during this first replacement.

References: [Longhorn's hook definition](https://github.com/longhorn/longhorn-manager/blob/v1.11.1/csi/deployment.go)
and [registrar native SIGTERM cleanup](https://github.com/kubernetes-csi/node-driver-registrar/blob/v2.18.0/cmd/csi-node-driver-registrar/node_register.go).
