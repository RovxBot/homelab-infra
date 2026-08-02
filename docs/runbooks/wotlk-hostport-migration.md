# WotLK HostPort migration

The WotLK auth and world servers currently use `hostNetwork` only to expose
two game listeners on their pinned nodes. Move them to explicit `hostPort`
only after the portmap preflight below succeeds. This is a player-visible
maintenance change; it is intentionally split from enabling the CNI support.

| Workload | Node | LAN address | Listener |
| --- | --- | --- | --- |
| `wotlk-authserver` | `metal7` | `192.168.1.197` | TCP 3724 |
| `wotlk-worldserver` | `metal4` | `192.168.1.47` | TCP 8085 |

The OCI edge continues forwarding to those same LAN addresses and ports. SOAP
TCP 7878, MariaDB TCP 3306 and registration stay ClusterIP-only.

## Portmap preflight

Cilium keeps `kubeProxyReplacement: false`, so native Cilium HostPort support
is deliberately unavailable. Cilium's supported `cni.chainingMode: portmap`
adds the standard portmap plugin without changing kube-proxy service handling.
`rollOutCiliumPods: true` makes the resulting CNI ConfigMap change roll the
agent DaemonSet one node at a time; otherwise the old agents would not rewrite
the conflist. The plugin must exist on every node before enabling the Helm
value.

After the Cilium HelmRelease reconciles, require the normal health gate to
pass and verify the temporary probe on both pinned nodes:

```bash
export KUBECONFIG="$HOME/.config/talos/cooked-k8s/kubeconfig-entra"
export TALOSCONFIG="$HOME/.config/talos/cooked-k8s/talosconfig-entra"

scripts/cilium-primary-health.sh
scripts/cilium-hostport-probe.sh --node metal7
scripts/cilium-hostport-probe.sh --node metal4
```

The probe uses TCP 39080, outside the Kubernetes NodePort range. It binds only
the selected node's LAN address, confirms the path from the administrator
workstation, and removes itself even when a test fails.

Do not continue if Cilium, Flux, Longhorn, either probe, or the underlying
CNI conflist is unhealthy. The supported configuration is documented in the
[Cilium Portmap guide](https://docs.cilium.io/en/stable/installation/cni-chaining-portmap/).

## Separate workload migration

After a successful preflight, use a distinct PR to:

1. Remove `hostNetwork: true` and `ClusterFirstWithHostNet` from the two
   Deployments.
2. Add `hostPort` and `hostIP` only to auth TCP 3724 on `192.168.1.197` and
   world TCP 8085 on `192.168.1.47`. Do not expose SOAP.
3. Roll auth first, test LAN and public `grim.cooked.beer:3724`, then roll
   world and test its existing OCI public `grim.cooked.beer:8443` path with a
   real game client.
4. Confirm the Services now resolve to Cilium Pod IPs, Gatus remains healthy,
   and Cilium/Longhorn/Flux health gates pass.

The rollback is the previous manifest with `hostNetwork: true`, applied as a
one-pod Deployment rollback while the node selectors and OCI forwarding remain
unchanged. Do not enable global NetworkPolicy enforcement as part of either
stage.
