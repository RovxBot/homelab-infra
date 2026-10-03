# Preserve Longhorn networking during the minor upgrades

This Talos cluster uses Cilium and V1 iSCSI volumes. Longhorn 1.12.1 introduces
internal ingress NetworkPolicies by default. Its instance-manager policy permits
selected Longhorn pods but omits the node-hosted iSCSI initiator. The upstream
advisory documents attachment and detachment failures on CNIs that enforce these
policies. Kubernetes server-side validation cannot detect this traffic failure.

Set `networkPolicies.restrictInternalTraffic: false` before moving from 1.11.1
to 1.12.1, and retain it for 1.13.0. This preserves the cluster's existing working
network behavior through the upgrade. It is separate from `networkPolicies.enabled`,
which controls the UI frontend policy. The preview renders no internal policies
for either target chart, keeps manager `maxUnavailable: 1`, and preserves the
current StorageClass parameters and control-plane tolerations.

Enabling these policies later requires testing the actual Talos/Cilium traffic
paths. Do not copy the advisory's example link-local CIDR without observed CNI
flows. Any later restriction should be a separate reviewed change, with volume
attachment and RWX recovery checks on a disposable test volume first.

References: [the upstream V1 iSCSI advisory](https://longhorn.io/kb/troubleshooting-volume-attachment-stuck-cni-networkpolicies/)
and [the Helm opt-out guidance](https://longhorn.io/docs/1.13.0/important-notes/#internal-network-policies).
