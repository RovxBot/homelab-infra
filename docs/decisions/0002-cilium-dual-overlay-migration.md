# ADR 0002: Staged Cilium dual-overlay migration

## Status

Proposed on 2026-07-22. The first GitOps phase requires normal PR review; each
node migration and final CNI cutover requires an explicit maintenance-window
decision.

## Context

Talos currently manages Flannel. Its live configuration uses pod CIDR
`10.244.0.0/16` with VXLAN on UDP `4789`; kube-proxy remains active in nftables
mode. Flannel does not enforce the Kubernetes NetworkPolicies already tracked
in this repository.

The cluster has seven disk-bearing Longhorn nodes, public application paths,
accepted LAN NodePorts, and node-pinned WotLK workloads. A cluster-wide CNI
swap or an untested kube-proxy replacement would create unnecessary outage
risk. Prometheus, Grafana, Loki and Hubble are intentionally out of scope;
Gatus and Upptime are the selected health systems.

## Decision

Use Cilium `1.19.6` through a signed, digest-pinned OCI chart in Flux. The
first release is a secondary overlay only:

- Cilium uses `10.245.0.0/16` in cluster-pool IPAM and Geneve on UDP `6081`.
  Both are distinct from Flannel.
- Cilium does not write a default CNI configuration until a single node has the
  deliberate `io.cilium.migration/cilium-default=true` label.
- Kube-proxy remains enabled. Kube-proxy replacement, Gateway API, L2
  announcements, encryption and host firewall are later, separately reviewed
  changes.
- Hubble, Envoy and all Prometheus/ServiceMonitor integrations are disabled.
- Talos remains responsible for cgroupv2 and bpffs mounts; Cilium automatic
  mounts for both are disabled.
- Policy enforcement remains `never` through the network migration. Existing
  NetworkPolicies need a separate application-by-application allow-list audit
  before policy enforcement becomes `default`.
- Talos keeps `cluster.network.cni.name: flannel` during every dual-overlay
  node migration. `cni: none` is a final, separate Talos change after Cilium is
  primary on every node and Flannel can be explicitly removed.

The OCI chart is pinned by manifest digest and Flux verifies Cilium's keyless
Cosign signature from the Cilium GitHub Actions identity. This avoids adding a
new signing key or a cloud service.

## Consequences

The initial merge installs a Cilium agent on every node but does not move any
application Pod onto Cilium. A second Flux Kustomization creates the
`CiliumNodeConfig` selector only after the Cilium CRD is available; it matches
no node by default.

Node migration is one at a time: cordon, drain, label, restart its Cilium
agent, reboot, validate Gatus/Flux/Longhorn, then uncordon. Start with a worker
chosen from current Longhorn replica placement, never a control plane. Do not
mix this work with Talos, Kubernetes, Longhorn or policy-enforcement upgrades.

The final cutover removes the per-node selector, makes Cilium the default CNI,
then changes all Talos desired configurations to `cni: none` and explicitly
removes Flannel. That phase includes another rolling reboot to clear old
interfaces/routes. Cilium's eBPF host routing stays disabled because Talos host
DNS forwarding requires `bpf.hostLegacyRouting: true` unless the DNS design is
separately changed.

## References

- [Cilium live CNI migration](https://docs.cilium.io/en/stable/installation/k8s-install-migration/)
- [Cilium Helm/OCI installation](https://docs.cilium.io/en/stable/installation/k8s-install-helm/)
- [Talos Cilium guide](https://docs.siderolabs.com/kubernetes-guides/cni/deploying-cilium)
- [Flux OCI artifact verification](https://fluxcd.io/flux/components/source/ocirepositories/)
