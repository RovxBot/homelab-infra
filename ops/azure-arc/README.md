# Azure Arc Runbook

This runbook onboards the `home` Talos cluster to Azure Arc as an **inventory-only** connected Kubernetes cluster.

## Day-1 guardrails

- Keep Flux as the only deployment authority.
- Do not add Arc agents or Arc extensions to `clusters/home`.
- Do not enable Arc Flux/GitOps in phase 1.
- Do not enable Azure Monitor Container Insights in phase 1.
- Do not enable Defender for Containers, Azure Policy, Cluster Connect, Azure RBAC, or Custom Locations in phase 1.

## Defaults

- Azure resource group: `rg-arc-homelab-au-east`
- Azure region: `australiaeast`
- Arc connected cluster name: `arc-home-talos`
- Distribution/infrastructure labels passed to Azure CLI: `generic`

These defaults are baked into [connect-home-cluster.sh](/home/Rov/Documents/Git/homelab-infra/ops/azure-arc/connect-home-cluster.sh).

## Prereqs

Operator machine:

- `az`
- `kubectl`
- Azure CLI login with permission to create `Microsoft.Kubernetes/connectedClusters`
- kubeconfig access to the `home` cluster

Required Azure resource providers:

- `Microsoft.Kubernetes`
- `Microsoft.KubernetesConfiguration`
- `Microsoft.ExtendedLocation`

Preferred Azure role for onboarding:

- `Kubernetes Cluster - Azure Arc Onboarding`

## Scripted workflow

Recommended usage:

```bash
export KUBE_CONTEXT=home
export SUBSCRIPTION_ID="<subscription-id>"

ops/azure-arc/connect-home-cluster.sh preflight
ops/azure-arc/connect-home-cluster.sh connect
ops/azure-arc/connect-home-cluster.sh status
```

The script does four things:

1. Verifies `az`/`kubectl`, Azure login, and target kube context.
2. Confirms the cluster exposes at least one `amd64` node and tries `kubectl top nodes`.
3. Registers the required Azure resource providers and creates the dedicated resource group.
4. Runs Azure Arc onboarding, then verifies the `azure-arc` namespace, Arc agents, Arc resource, and Flux Kustomizations.

## Exact connect command

The script wraps this Azure CLI flow:

```bash
az connectedk8s connect \
  --resource-group rg-arc-homelab-au-east \
  --name arc-home-talos \
  --location australiaeast \
  --distribution generic \
  --infrastructure generic \
  --kube-context home \
  --onboarding-timeout 600 \
  --tags environment=homelab cluster=home management=azure-arc gitops=flux phase=inventory-only \
  --yes
```

## Cluster egress check

The script validates Azure endpoints from the operator machine. If your node egress path differs from the machine running `az`, also verify cluster-side outbound access before onboarding:

```bash
kubectl run arc-egress-check \
  --rm -i --restart=Never \
  --image=curlimages/curl:8.7.1 \
  --command -- sh -ceu '
    for url in \
      https://management.azure.com \
      https://login.microsoftonline.com \
      https://mcr.microsoft.com \
      https://australiaeast.dp.kubernetesconfiguration.azure.com
    do
      echo "checking $url"
      curl -fsSIL --max-time 20 "$url" >/dev/null
    done
  '
```

`*.servicebus.windows.net` is mainly required once you decide to enable Cluster Connect later.

## Verification

After a successful connect:

```bash
az connectedk8s list -g rg-arc-homelab-au-east -o table
kubectl get ns azure-arc
kubectl get deployments,pods -n azure-arc
az k8s-extension list \
  --cluster-type connectedClusters \
  --resource-group rg-arc-homelab-au-east \
  --cluster-name arc-home-talos \
  -o table
kubectl get kustomizations.kustomize.toolkit.fluxcd.io -A
```

Expected day-1 result:

- The Arc resource exists in Azure.
- Arc agents are healthy in `azure-arc`.
- No Arc extensions are installed yet.
- Flux Kustomizations remain present and unchanged.

## Rollback / disconnect

Use the CLI, not the Azure portal, so the in-cluster Arc agents are removed too:

```bash
export KUBE_CONTEXT=home
export SUBSCRIPTION_ID="<subscription-id>"

ops/azure-arc/connect-home-cluster.sh delete
```

Equivalent raw command:

```bash
az connectedk8s delete \
  --resource-group rg-arc-homelab-au-east \
  --name arc-home-talos \
  --kube-context home \
  --yes
```

If removal gets stuck and you are intentionally tearing the deployment down, retry with `--force`.

## Deferred phase-2 options

Intentionally not part of this rollout:

- `microsoft.flux`
- `Microsoft.AzureMonitor.Containers`
- `microsoft.azuredefender.kubernetes`
- Azure Policy for Kubernetes
- Cluster Connect
- Azure RBAC
- Custom Locations

Revisit those only after the base Arc connection is stable and you decide which Azure management features add value over the existing Flux, Prometheus, Grafana, Loki, Cloudflare Tunnel, and Longhorn setup.
