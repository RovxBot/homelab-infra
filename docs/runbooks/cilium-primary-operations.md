# Primary Cilium operations

Cilium is the primary CNI for `cooked-k8s`. It uses Geneve on UDP `6081` and
the `10.245.0.0/16` Pod CIDR. Talos has `cni: none`; Flannel, the temporary
`CiliumNodeConfig` selector and migration labels are intentionally absent.

This is a steady-state runbook. Do not recreate the historical dual-overlay
procedure, Flannel resources or migration labels.

## Health gate

Run this before CNI, Talos, Kubernetes, Longhorn or node-maintenance work. It
is read-only and requires the normal Entra Kubernetes credential plus the
local Talos recovery credential.

```bash
export KUBECONFIG="$HOME/.config/talos/cooked-k8s/kubeconfig-entra"
export TALOSCONFIG="$HOME/.config/talos/cooked-k8s/talosconfig-entra"

scripts/cilium-primary-health.sh
```

For a prospective maintenance node, include `--node <name>` to report its
current Longhorn replica and attachment load. The command never cordons,
drains, restarts or changes a node.

The check fails closed if any node is not Ready, its Kubernetes Node or
CiliumNode `InternalIP` is outside `192.168.1.0/24`, Cilium peer health is not
complete, the Cilium CNI file is missing, a workload remains on a non-Cilium
Pod CIDR, Flannel/migration resources remain, Longhorn is degraded, or Flux is
not Ready.

## Node address recovery

Cilium adds a host-side `10.245.x.x` address. Kubelet must keep advertising the
node's management LAN address to Kubernetes. If a node reports a Cilium address
as its Kubernetes `InternalIP`:

1. Stop node maintenance and run the health gate to establish the current
   cluster state.
2. Use the node's known LAN address from the non-secret Talos inventory; do
   not derive it from the incorrect Kubernetes Node status.
3. Apply that node's exact kubelet node-IP JSON patch from the private
   `TalosConfigs` repository with a Talos dry run, then reboot only that node.
   Keep a control plane cordoned during its repair and preserve etcd quorum.
4. Require the health gate to pass before uncordoning or performing the next
   maintenance step.

Never manually patch Kubernetes Node status or CiliumNode objects. The Talos
kubelet configuration is the source of the advertised address.

## Deliberate boundaries

- `policyEnforcementMode: never` is intentional. Do not enable `default` or
  add generic default-deny policies until namespace-specific dependency rules
  have been designed and tested.
- Keep `bpf.hostLegacyRouting: true` until the Talos host-DNS design has a
  separate review.
- Hubble, Envoy and Prometheus integrations remain disabled; use Gatus,
  Upptime, focused logs, events, Talos and Longhorn for health diagnosis.
- Cilium changes remain maintenance-window work. Validate Cilium peer health,
  Flux and Longhorn after every change.

## Follow-up: NetworkPolicy project

Build policies incrementally by namespace: DNS, Cloudflared and Gatus first,
then application-to-database/cache paths and each required egress route. Test
each namespace in audit-compatible stages and document exceptions. Change
global policy enforcement only after the complete policy set is proven.

The health script reports the kube-proxy cluster CIDR separately. Its desired
steady-state value needs a dedicated Talos/Kubernetes review; do not alter it
as an incidental Cilium cleanup.
