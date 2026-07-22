# Cilium dual-overlay migration runbook

This runbook migrates the existing Talos-managed Flannel CNI to Cilium without
changing the accepted LAN NodePorts or the existing Backblaze backup design.
It is intentionally split into reviewable phases. Do not combine it with a
Talos, Kubernetes, Longhorn, policy, or public-routing change.

## Design and stop conditions

| Item | Existing | Cilium migration value |
| --- | --- | --- |
| Pod CIDR | `10.244.0.0/16` | `10.245.0.0/16` |
| Overlay | Flannel VXLAN/UDP 4789 | Geneve/UDP 6081 |
| Service implementation | kube-proxy nftables | unchanged |
| Policy enforcement | not provided by Flannel | disabled during migration |
| Health systems | Gatus and Upptime | unchanged; no Hubble/Prometheus |

Stop immediately if a node is not Ready, Flux is not Ready, a Longhorn volume
is not `healthy`, a Cilium agent/operator is not Ready, a node has unexpected
routes for `10.245.0.0/16`, a Kubernetes Node `InternalIP` is not in
`192.168.1.0/24`, or Gatus reports a new failure. Do not drain a second node
while a prior node is rebuilding storage replicas.

The documented Cilium approach is a dual overlay: Flannel and Cilium use
separate CIDRs and encapsulation, so the Linux routing table can carry traffic
between old and new Pods while nodes are migrated. Cilium's official migration
guide requires cluster-pool IPAM and policy enforcement disabled during that
period.

## Phase 0: preflight

Run from the administrator workstation. It is read-only and prints no Talos
machine configuration or Secret data.

```bash
export KUBECONFIG="$HOME/.config/talos/cooked-k8s/kubeconfig-entra"
export TALOSCONFIG="$HOME/.config/talos/cooked-k8s/talosconfig-entra"

scripts/cilium-preflight.sh --node metal5
```

Before any node receives the Cilium migration label, apply the reviewed
non-secret `kubelet-node-ip-lan.json` patch from the separate Talos
configuration repository, one node at a time, using its
`kubelet-node-ip-stability` runbook. It constrains kubelet to advertise the
node LAN subnet (`192.168.1.0/24`) when Cilium's host interface makes Talos
multihomed. The no-reboot patch restarts kubelet, so verify each node returns
Ready with its LAN `InternalIP` before continuing. Do not manually edit the
Kubernetes Node object: kubelet re-registers its address itself.

`metal5` is only the current likely canary because it is a worker with one
running Longhorn replica at the time this runbook was written. Always run the
script again immediately before the maintenance window and choose the least
impactful worker from its fresh output. Keep the GPU/WotLK nodes later in the
worker sequence and migrate control planes last.

Before merging the initial Cilium PR, manually confirm the LAN/router permits
UDP `6081` directly between all seven node LAN addresses. No WAN rule is
required and Cilium does not create a new public listener.

## Phase 1: install the secondary overlay

Merge the reviewed GitOps PR containing `infra/cilium` and
`infra/cilium-migration`. Flux should install seven Cilium agent Pods and two
Cilium Operator Pods, then create a `CiliumNodeConfig` that selects no nodes.

```bash
kubectl -n flux-system get kustomizations cilium cilium-migration
kubectl -n kube-system get daemonset cilium
kubectl -n kube-system get deployment cilium-operator
kubectl -n kube-system get ciliumnodeconfigs.cilium.io cilium-default
scripts/cilium-preflight.sh --secondary
```

Expected outcome: all Cilium Pods are Ready, `cilium-default` exists, no node
has the `io.cilium.migration/cilium-default=true` label, and every Kubernetes
Node retains a `192.168.1.x` `InternalIP`. Existing Pods retain `10.244.x.x`
addresses and Flannel remains active.

If this phase fails before any node receives the migration label, revert the
GitOps PR. No workload CNI configuration has been selected, so Flannel remains
the active network.

If an initial Cilium agent log reports a Kyverno admission error for the newly
created `CiliumNode` CRD (for example, `resource ciliumnodes not found in group
cilium.io/v2`), first confirm the Cilium CRD exists, then perform one rolling
restart of the two-replica Kyverno admission controller. This refreshes its CRD
discovery cache without disabling policy enforcement; wait for both replicas to
be Ready before rechecking Cilium.

If a corrected Helm value is applied after a partially installed release, wait
for the HelmRelease to report Ready, then restart the Cilium DaemonSet once and
wait for its one-at-a-time rollout to finish before running the secondary
preflight. This makes every agent consume the corrected rendered configuration
while Cilium is still only the secondary overlay.

## Phase 2: migrate one worker

Use a user-approved maintenance window. Replace `metal5` only after the
preflight identifies it as the current least-impact worker.

```bash
NODE=metal5
NODE_IP="$(kubectl get node "$NODE" \
  -o go-template='{{index .metadata.annotations "flannel.alpha.coreos.com/public-ip"}}')"
test -n "$NODE_IP"

scripts/cilium-preflight.sh --secondary --node "$NODE"

kubectl cordon "$NODE"
kubectl drain "$NODE" --ignore-daemonsets --delete-emptydir-data --timeout=20m

kubectl label node "$NODE" --overwrite \
  io.cilium.migration/cilium-default=true
kubectl -n kube-system delete pod -l k8s-app=cilium \
  --field-selector "spec.nodeName=$NODE"
kubectl -n kube-system rollout status daemonset/cilium --timeout=10m
talosctl read --nodes "$NODE_IP" /etc/cni/net.d/05-cilium.conflist >/dev/null

talosctl reboot --nodes "$NODE_IP"
kubectl wait --for=condition=Ready "node/$NODE" --timeout=15m
```

Never add `--force` to the drain command. If it is blocked, inspect the named
Pod/PDB and stop rather than deleting an unmanaged workload. If the node owns
Longhorn replicas, wait for every affected volume to return to `healthy` before
the reboot and again before selecting another node.

Validate the migrated node and the cluster before uncordoning it:

```bash
CILIUM_POD="$(kubectl -n kube-system get pod -l k8s-app=cilium \
  --field-selector "spec.nodeName=$NODE" -o jsonpath='{.items[0].metadata.name}')"
kubectl -n kube-system exec "$CILIUM_POD" -c cilium-agent -- \
  cilium-dbg status --brief --timeout=60s
talosctl read --nodes "$NODE_IP" /etc/cni/net.d/05-cilium.conflist

kubectl -n kube-system run --attach --rm --restart=Never verify-network \
  --overrides='{"spec":{"nodeName":"'"$NODE"'","tolerations":[{"operator":"Exists"}]}}' \
  --image=ghcr.io/nicolaka/netshoot:v0.8 -- \
  /bin/bash -ec 'ip -br addr; api_status="$(curl -sSk -o /dev/null -w "%{http_code}" https://$KUBERNETES_SERVICE_HOST/healthz)"; test "$api_status" = 200 || test "$api_status" = 401; echo "Kubernetes API reachable ($api_status)"'

kubectl get node "$NODE" -o wide
kubectl -n longhorn-system get volumes.longhorn.io
kubectl -n flux-system get kustomizations,helmreleases
kubectl -n monitoring get deployment gatus

kubectl uncordon "$NODE"
```

The Cilium configuration file must be `05-cilium.conflist`; the temporary
`verify-network` Pod must receive an address in `10.245.0.0/16` and reach the
Kubernetes API. An unauthenticated probe commonly receives `401`, which proves
service routing and API reachability without exposing a credential; `200` is
also acceptable. That validates the selected CNI and API path while the node
remains cordoned. Check Gatus after the node returns. Keep the node cordoned and
stop if any validation fails; do not begin a second node. The Cilium and Talos
recovery procedure must be reviewed against the exact failed state rather than
trying a broad CNI deletion from the workstation.

`flannel.alpha.coreos.com/public-ip` is a transition-only source of the Talos
LAN address while Flannel remains active. It avoids trusting a stale Kubernetes
Node `InternalIP`; retain an explicit non-secret node-to-LAN inventory before
Flannel is removed in Phase 3.

Repeat Phase 2 for workers first, then the three control planes one at a time.
Recalculate replica/attachment placement before every node. Migrate `metal4`
and `metal7` only in a window that permits their WotLK/Jellyfin disruption.

## Phase 3: make Cilium primary and retire Flannel

This is a separate reviewed PR after every ordinary Pod uses `10.245.0.0/16`.
It must not be performed merely because Cilium agents are Ready.

1. Keep `bpf.hostLegacyRouting: true`; Talos host DNS forwarding makes the
   upstream eBPF host-routing optimisation unsafe until DNS is redesigned.
2. Change the Helm values to make Cilium write its normal CNI configuration
   everywhere (`cni.customConf: false`) and restore normal primary-CNI operator
   behaviour: `unmanagedPodWatcher.restart`, `removeNodeTaints`,
   `setNodeTaints`, and `setNodeNetworkStatus` become `true`. Keep policy
   enforcement `never` and `bpf.hostLegacyRouting: true` until their separate
   reviews are complete. The chart deliberately does not roll Pods for a
   ConfigMap-only change, so after Flux reports the Helm release Ready, perform
   a controlled restart of both components (the agent release permits only one
   unavailable Pod):

   ```bash
   kubectl -n kube-system wait --for=condition=Ready helmrelease/cilium --timeout=20m
   kubectl -n kube-system rollout restart daemonset/cilium
   kubectl -n kube-system rollout status daemonset/cilium --timeout=30m
   kubectl -n kube-system rollout restart deployment/cilium-operator
   kubectl -n kube-system rollout status deployment/cilium-operator --timeout=15m
   ```

   Validate Cilium health and new CNI configuration on every node before
   proceeding.
3. Remove the `cilium-migration` Flux Kustomization so Flux prunes
   `CiliumNodeConfig`, then confirm every node still has Cilium networking.
4. In the separate private Talos configuration repository, set
   `cluster.network.cni.name: none` for all seven nodes. Apply that small patch
   in `--mode=no-reboot` only after Cilium is already primary everywhere.
   Do not run `talosctl upgrade-k8s` during this transition: Talos 1.12 does
   not prune obsolete bootstrap manifests on a normal config apply.
5. Explicitly delete Talos-managed Flannel resources after confirmation that
   Cilium is primary: the `kube-flannel` DaemonSet, `kube-flannel-cfg`
   ConfigMap, `flannel` ServiceAccount, ClusterRole and ClusterRoleBinding.
6. Perform a final rolling drain/reboot, one node at a time, to clear Flannel
   routes/interfaces. Keep the Longhorn health gate between every node.

Only after a separate policy review should the Helm release change
`policyEnforcementMode` to `default`. That review starts with DNS, Cloudflared,
Gatus, database/cache dependencies and explicit egress per namespace; it does
not introduce generic default-deny policies blindly.

## References

- [Cilium live migration guide](https://docs.cilium.io/en/stable/installation/k8s-install-migration/)
- [Cilium per-node configuration](https://docs.cilium.io/en/stable/configuration/per-node-config/)
- [Cilium on Talos](https://docs.cilium.io/en/stable/installation/k8s-install-helm/)
- [Talos CNI configuration](https://docs.siderolabs.com/talos/v1.12/reference/configuration/v1alpha1/config)
