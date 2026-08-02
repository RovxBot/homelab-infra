# ADR 0002: Cilium primary CNI migration

## Status

Accepted and implemented. The staged dual-overlay rollout is complete; this
record now describes the resulting steady state and preserves the reason for
the migration design.

## Context

At the time of the decision, Talos managed Flannel with a `10.244.0.0/16` Pod
CIDR and VXLAN on UDP `4789`. Flannel did not enforce the Kubernetes
NetworkPolicies already tracked in this repository. A direct cluster-wide CNI
swap would have been too disruptive for disk-backed workloads, accepted LAN
NodePorts, public application routes and node-pinned WotLK services.

The migration therefore used Cilium as a temporary secondary Geneve overlay,
then moved nodes one at a time before retiring Flannel. Grafana, Prometheus,
Loki, Hubble and Envoy remained out of scope; Gatus and Upptime are the chosen
health systems.

## Decision

Use Cilium `1.19.6` from a signed, digest-pinned OCI chart in Flux as the
primary CNI.

- Cilium uses cluster-pool IPAM in `10.245.0.0/16` and Geneve on UDP `6081`.
- Cilium writes the primary CNI configuration and owns CNI state. Talos uses
  `cluster.network.cni.name: none`; Flannel and the temporary
  `CiliumNodeConfig` migration selector are removed.
- kube-proxy remains enabled. Kube-proxy replacement, Gateway API, L2
  announcements, encryption and host firewall remain separate decisions.
- Kube-proxy's `--cluster-cidr` is `10.245.0.0/16`, matching Cilium's
  workload range. Talos's distinct `10.244.0.0/16` Node-CIDR allocation
  setting remains unchanged until a separately designed migration.
- Policy enforcement remains `never` while application dependency policies are
  designed and tested. Existing NetworkPolicies do not yet provide meaningful
  application isolation.
- `bpf.hostLegacyRouting: true` remains required by the current Talos host-DNS
  design. Do not enable eBPF host routing until DNS is separately redesigned.
- Hubble, Envoy and Prometheus/ServiceMonitor integrations remain disabled.
- Talos owns cgroupv2 and bpffs mounts; Cilium automatic mounting remains
  disabled. Cilium's dedicated, PSA-labelled `cilium-secrets` namespace is
  retained for any future TLS-aware policy design.
- Kubelet node-IP selection must remain constrained to the management LAN. A
  Cilium host address must never become a Kubernetes Node `InternalIP`.

Flux verifies Cilium's keyless Cosign signature from the Cilium GitHub Actions
identity. This keeps deployment verification reproducible without adding a
cloud service or static signing key.

## Outcome and operational gates

All workload Pods use the Cilium Pod CIDR and Cilium peer health must report
every current node reachable before maintenance. The read-only
`scripts/cilium-primary-health.sh` gate validates the primary-CNI settings,
node/CiliumNode LAN addresses, CNI files, peer health, residual Flannel state,
Longhorn, Flux and workload Pod addresses.

If a node advertises a `10.245.x.x` Kubernetes `InternalIP`, repair the Talos
kubelet node-IP selector from the private Talos configuration source. Do not
manually edit Kubernetes Node status or CiliumNode resources.

## Consequences

NetworkPolicy enforcement is intentionally deferred, not complete. The next
network-security project must first build and test namespace-specific policies
for DNS, Cloudflared, Gatus, application-to-database/cache traffic and required
egress. Only then can enforcement move to `default` in a separate reviewed
change.

The retired dual-overlay procedure remains in Git history only. Operational
work must use the primary-CNI runbook rather than recreating migration labels,
Flannel resources or a second overlay.

## References

- [Cilium live CNI migration](https://docs.cilium.io/en/stable/installation/k8s-install-migration/)
- [Cilium Helm/OCI installation](https://docs.cilium.io/en/stable/installation/k8s-install-helm/)
- [Talos Cilium guide](https://docs.siderolabs.com/kubernetes-guides/cni/deploying-cilium)
- [Flux OCI artifact verification](https://fluxcd.io/flux/components/source/ocirepositories/)
