# Kubescape posture assessment — suspended staging

Tracking: [#2135](https://github.com/RovxBot/homelab-infra/issues/2135).
Baseline: `b6a76abd2d75166abb39b32427dee1cca865568c` (merged `main`).
Prepared 20 November 2026. **Activation is blocked.** Neither this change nor
merging it installs Kubescape workloads: `HelmRelease/kubescape` has
`spec.suspend: true`. Flux adds only the namespace, chart source and suspended
release through a dedicated child Kustomization under
`clusters/home/infrastructure`. No cluster writes were made.
The child depends on infrastructure and Kyverno policies and has `wait: false`
while staged: a never-reconciled suspended HelmRelease has no Ready status and
must not stall the shared infrastructure health checks. The activation PR must
enable waiting on this child; existing infrastructure waiting is unchanged.

## Certificate init budget correction

The released chart's `templates/storage/deployment.yaml` renders
`certgen-create` and `certgen-patch` init containers without `resources`.
Both originally lacked all four CPU/memory request/limit fields, causing the
enforced `platform-pod-standards/require-requests-and-limits` failure. A
[Flux Helm Kustomize post-renderer](https://fluxcd.io/flux/components/helm/helmreleases/#post-renderers)
uses a strategic-merge patch targeting only
`apps/v1 Deployment/kubescape/storage` and adds resources to the **named**
`certgen-create` and `certgen-patch` init containers. Each requests **10m CPU /
32Mi memory**, with limits of **100m CPU / 128Mi memory**, matching the existing
ops-tool scheduler budget. These are conservative starting budgets for short
certificate/bootstrap API operations, not runtime sizing evidence.

The effective released-chart render now passes Kyverno CLI **1.19.2**, matching
chart 3.9.2's controller: **27 passes, 0 failures, 0 errors, 0 skips** across
the existing workload and namespace standards. No exception or policy relaxation
is used. The TLS strategy, commands/arguments, images, mounts, security contexts,
ServiceAccounts, RBAC and capabilities are preserved.

Preflight compares every raw/effective resource and field, allowing only these
two exact budgets and the existing scheduler suspension (plus Kustomize's
omission of null annotations/affinity/tolerations). Missing, renamed or extra
certificate init containers and unexpected chart-provided budgets fail closed.
Regression tests also exercise real Kustomize merging with reordered init
containers and same-name/namespace decoys. Both strict and suspended-staging
preflights now pass; **both still reject an unsuspended HelmRelease** and any
admission failure. This resolves only the certificate budget blocker in #2135.
Activation still requires separate review and the unresolved
[#2133](https://github.com/RovxBot/homelab-infra/issues/2133) admission/discovery gate.

## Verified artifacts and capabilities

The official [chart index](https://kubescape.github.io/helm-charts/index.yaml)
and [published release](https://github.com/kubescape/helm-charts/releases/tag/kubescape-operator-1.42.0)
were checked. Chart `kubescape-operator` **1.42.0**, published 6 November 2026,
declares `kubeVersion: >=1.28.0-0`; it renders for **1.34.0**. Archive SHA-256:
`be45f83398f28db8f2ff395e5d8ab411328041e9e2165a42e5fb92a4ba369079`.
The preflight verifies that checksum before Helm renders the archive.

All instantiated container images are pinned by version **and registry digest**
in the release. Anonymous Quay manifest responses verified the digests for:

| Component | Version | CPU request / limit | Memory request / limit |
| --- | --- | --- | --- |
| Posture scanner | `v4.0.20` | `250m / 600m` | `400Mi / 1Gi` |
| Operator | `v0.2.209` | `50m / 300m` | `100Mi / 300Mi` |
| Storage | `v0.0.382` | `100m / 1500m` | `400Mi / 1500Mi` |
| Scheduler (suspended) | ops-tool `0.2.0-r1` | `10m / 100m` | `32Mi / 128Mi` |
| Storage certificate init containers (each) | ops-tool `0.2.0-r1` | `10m / 100m` | `32Mi / 128Mi` |

Three single-replica Deployments request `400m` CPU and `900Mi` memory in
total, with limits of `2400m` CPU and `2824Mi` memory. The sequential certificate
init budgets are below storage's application budgets, so they do not increase
these effective Pod totals. These are initial budgets, not measured capacity
evidence. Certificate generation time/peak memory must be checked during the
later approved canary. No high scheduling priority or
preemption is requested. `WORKERCONCURRENCY=1` bounds operator command workers
(verified against the pinned operator's Viper configuration); submit only one
manual canary scan. It does not prove all internal scanner processing is serial.

Only operator, configuration scanning and offline mode are enabled. Every
other switchable capability is explicitly disabled, including continuous
scanning, node scans, node SBOMs, image vulnerabilities/relevancy/VEX, all
runtime features, admission, network policy generation, seccomp generation and
sandbox enforcement, workload management, remediation and automatic upgrades.
The seccomp backend is `storage` solely to avoid rendering its unused CRD;
the seccomp capability stays disabled.

Independent switches disable the host sensor, node-agent autoscaler, SBOM
sidecars, gVisor collection, multiple DaemonSets, service discovery scans,
Prometheus integrations, runtime priority class, alert CRD dependencies and
chart NetworkPolicies. No Prometheus, Grafana or Loki is required.

`server`, `account`, `accessKey` and credential references are empty.
The rendered configuration has `keepLocal: true`, `serviceDiscovery: false`,
and no synchronizer. `kubescapeOffline`, `skipUpdateCheck`,
`downloadArtifacts: false` and disabled operator rule updates suppress daily
reporting, version checks and per-scan downloads. Policies come from the
digest-pinned scanner image. This configuration requests no external result
export; network egress isolation is **not** claimed (Cilium enforcement is
unchanged and no NetworkPolicy is added). Registry pulls still need network
access. Confirm actual egress/reporting behaviour during the later canary.

The chart enables its daily posture CronJob whenever configuration scanning
is enabled, independently of `continuousScan`. A Flux Helm post-render patch
sets **that CronJob's `spec.suspend: true`**, so a later release unsuspension
alone still does not start periodic scans. There is no chart-native scheduler
disable setting. Operator startup scanning is gated by service discovery,
which renders false; `triggerSecurityFramework: false` also prevents adding
the extra security framework. See the pinned
[operator startup code](https://github.com/kubescape/operator/blob/v0.2.209/main.go)
and [chart component gates](https://github.com/kubescape/helm-charts/blob/kubescape-operator-1.42.0/charts/kubescape-operator/templates/_common.tpl).

## Actual rendered inventory

[rendered-inventory.json](rendered-inventory.json) records all **47** chart
resource identities, both exact certificate init budgets and an empty
`admissionBlockers` list. The
preflight regenerates and compares it after applying the exact Flux
post-render configuration. The namespace, HelmRepository, HelmRelease and Flux
child Kustomization are additional GitOps wrapper resources.

| Kind | Count | Names / purpose |
| --- | --- | --- |
| Deployment | 3 | `kubescape`, `operator`, `storage` |
| CronJob | 1 | `kubescape-scheduler`, **suspended** |
| Service / ServiceAccount | 3 / 3 | `kubescape`, `operator`, `storage`; services are ClusterIP |
| PersistentVolumeClaim | 1 | `kubescape-storage`, `5Gi`, RWO, `storageClassName: longhorn` |
| APIService | 1 | `v1beta1.spdx.softwarecomposition.kubescape.io` |
| CustomResourceDefinition | 5 | `operatorcommands`, `servicesscanresults`, `rules`, `securityexceptions`, `clustersecurityexceptions` in `kubescape.io` |
| ClusterRole / ClusterRoleBinding | 4 / 7 | Scanner, operator, storage, storage certgen; includes three auth-delegator bindings |
| Role / RoleBinding | 4 / 5 | Component roles, operator API caller, storage certgen; storage auth-reader binding is in `kube-system` |
| ConfigMap | 9 | Effective capabilities/cloud config, component config, matching rules, scheduler request, three inert CronJob templates |
| Secret | 1 | Empty `cloud-secret`; no commercial credentials |

No DaemonSet, host mount, host namespace, privileged container, admission
webhook, network policy, runtime rule binding, upgrade job, vulnerability
scanner or external reporting component is rendered. The chart still embeds
posture, vulnerability and registry CronJob **templates in ConfigMaps** even
with scanning disabled; those are inert data, not scheduled jobs. Do not invoke
schedule, registry or vulnerability commands. The preflight inventories them.

`operatorcommands.kubescape.io` supports manual scan requests and status.
The other four CRDs are unconditional chart assets retained by Helm's CRD
lifecycle even when their features are off; they do not enable those features.
Runtime alert and hostdata CRDs are absent. Posture findings themselves are
**aggregated API objects**, not ordinary etcd-backed CRDs. The required storage
service exposes workload configuration scans/summaries through the APIService,
persists them on Longhorn and uses Recreate updates for its RWO volume.
No vulnerability database volume is rendered. Storage capacity, Longhorn
replication overhead, fsGroup write access and restoration remain untested.

RBAC is not confined to the canary namespace. The scanner reads workload,
node, namespace, RBAC and policy metadata cluster-wide, and writes/deletes
assessment records. Namespace filters restrict namespaced scanning, not these
permissions or cluster-scoped checks. Secret collection is disabled. The
operator can manage its own namespace's Jobs/CronJobs/ConfigMaps, publish
events, handle OperatorCommands and remove stale assessment records, but has
no workload patch or NetworkPolicy modification grant. Auth delegation permits
TokenReview and SubjectAccessReview creation. Storage reads cluster metadata
and the standard `kube-system/extension-apiserver-authentication` ConfigMap.
Storage certgen can create namespace Secrets and get its named TLS Secret;
its ClusterRole grants **get/patch on all APIServices** without resourceNames.
This upstream privilege must be explicitly accepted or narrowed in a later review.

`certificates.strategy: initContainer` avoids random Helm-rendered certificates
and hooks. Init containers create/reuse `storage-tls-init` at runtime and patch
the APIService CA bundle. That Secret and CA bundle are absent from the static
render by design; TLS verification is not disabled. The init RBAC, certificate
bootstrap, API-server-to-service routing and APIService `Available=True` require
live verification. An unhealthy aggregated API could affect discovery, making
the #2133 recovery gate especially relevant.

## Local validation

Use Helm **3.20.2**, Kustomize **5.5.0**, Kyverno CLI **1.19.2**, kubeconform
**0.6.7** and Python with PyYAML. These commands perform no cluster operations:

```sh
python3 -m unittest discover -s scripts/ci/tests -v
python3 -m unittest discover -s scripts/tests -v
python3 scripts/ci/check_kubescape.py \
  --base-ref b6a76abd2d75166abb39b32427dee1cca865568c
python3 scripts/ci/check_kubescape.py --staged-review \
  --base-ref b6a76abd2d75166abb39b32427dee1cca865568c
kustomize build infra/kubescape
kustomize build --load-restrictor LoadRestrictionsNone clusters/home/infrastructure > /tmp/infrastructure.yaml
kustomize build --load-restrictor LoadRestrictionsNone clusters/home > /tmp/flux.yaml
kubeconform -strict -ignore-missing-schemas -kubernetes-version 1.34.0 \
  -summary .work/kubescape-preflight/rendered.yaml
```

Both modes succeed only while the release remains suspended, the actual
inventory and exact post-render edits match, every workload has complete
budgets and Kyverno reports 27 passes with zero failures/errors/skips/warnings.
Unexpected webhooks, workloads, privileges, host access, active scheduling or
capabilities fail either mode. The preflight verifies the CLI version is 1.19.2.
The dedicated CI workflow runs both modes and the real Kustomize regression.
It is not a deployment approval or an admission exception.

The work directory contains raw Helm output, effective post-rendered output,
extracted Pod templates, values, inventory and the actual Kyverno report.
Inspect both renders: `helm template` alone does not apply Flux post-renderers.
Current kubeconform result: **42 valid, 0 invalid/errors, 5 skipped** (CRD
definition schemas unavailable in the default catalog). Kustomize builds must
also cover every repository source root, as in the main Repo Checks workflow.

Talos compatibility is a **static inference**: these components use the
Kubernetes API, normal restricted Linux containers and a CSI PVC, with no
host filesystem, kernel/eBPF or container-runtime socket dependency. No live
Talos/node-architecture/certificate/storage test has run. Restricted PSA fields
and Kyverno hardening pass static inspection; **live PSA admission is not run**.
The local Kyverno CLI's attempted standalone `podSecurity` fixture evaluated
zero rules, so it is not counted as PSA validation. Do not infer a live pass
from successful rendering. No Kyverno, PSA, Cilium or Gatus files were changed.

Recorded local checks: 53 CI helper tests (22 Kubescape-focused), 174 script
tests, 38 Gitea guard tests and 123 deployed-version resource-budget cases
passed. All 52 Kustomize source roots, the repository Kyverno baseline gate,
Flux bootstrap image pins, Secret references, Immich version consistency,
workflow actionlint, YAML lint and offline zizmor checks passed. Repeated Helm
renders were identical. Both the strict and suspended-stage preflights passed:
Kyverno CLI 1.19.2 reported **27 passes and zero admission failures**. Gitleaks
found no secrets in the PR diff. The optional Python-image helper smoke check
was **unavailable** because the local Docker daemon socket does not exist;
the host Python unit suites passed. No live readiness, scan, PSA admission,
Talos runtime or egress test was run. CI is not awaited.

## Separately reviewed canary deployment

The platform owner must review and merge a **separate activation PR**. Before
that PR changes `spec.suspend` to false, it must deliberately update the
suspension guards, obtain a clean strict preflight and confirm #2133's
admission/discovery gate. The certificate budget correction here does not
satisfy those live acceptance gates. Retain the suspended
scheduler, offline configuration and disabled vulnerability/runtime features.
Verify that `default` contains only the intended noncritical canary workload;
otherwise choose a dedicated canary namespace in that PR. Empty scope is not
evidence of a successful assessment. No task here authorizes activation.

After that later activation, verify the release and all supporting components:

```sh
flux get helmreleases -n kubescape
kubectl -n kubescape rollout status deployment/operator --timeout=180s
kubectl -n kubescape rollout status deployment/kubescape --timeout=180s
kubectl -n kubescape rollout status deployment/storage --timeout=180s
kubectl -n kubescape get pods,pvc,cronjobs
kubectl wait apiservice/v1beta1.spdx.softwarecomposition.kubescape.io \
  --for=condition=Available --timeout=180s
kubectl api-resources --api-group=spdx.softwarecomposition.kubescape.io
```

Require Ready Pods, a Bound Longhorn PVC, no unexpected restarts/evictions or
admission warnings, a suspended scheduler, working authenticated scanner calls
and healthy API discovery before scanning. Bound resource usage and examine
events and certgen logs. Do not log TLS Secret contents or service-account tokens.

Run **one manual Job** from the still-suspended scheduler after canary approval:

```sh
kubectl -n kubescape create job --from=cronjob/kubescape-scheduler kubescape-canary-001
kubectl -n kubescape logs job/kubescape-canary-001
kubectl -n kubescape get operatorcommands.kubescape.io \
  --sort-by=.metadata.creationTimestamp -o yaml
```

The pinned request selects NSA posture, `hostScanner: false`, `keepLocal: true`;
the scanner and operator namespace filter is `default`. Cluster-scoped posture
checks still read shared RBAC/cluster metadata. The Job creates an OperatorCommand:
Job completion alone **does not mean the assessment completed**. Identify its
created command, require `status.completed: true`, inspect `status.error`, and
correlate its GUID/start/completion times with operator and scanner logs.
Do not create a recurring schedule or additional scan while it is running.

```sh
kubectl -n default get workloadconfigurationscans.spdx.softwarecomposition.kubescape.io -o json
kubectl -n default get workloadconfigurationscansummaries.spdx.softwarecomposition.kubescape.io -o json
kubectl -n kubescape get pods -o json
```

Keep the reviewed Git commit, Helm/chart version, pulled container `imageID`
digests, baked-in policy provenance, command UID/GUID, scan interval, exact
namespace/workload UID/resourceVersion and raw local reports together. Compare
returned report labels/payload/resource identities and timestamps against that
inventory and the selected framework. Reject stale/pre-existing reports, missing
workloads and inadequate provenance. Record unavailable host controls and
Secret-dependent controls as coverage gaps, not passes; this stage produces no
image-digest vulnerability acceptance. Findings must be interpreted as posture
results; empty output or component readiness alone is not scan acceptance.
Confirm no external reporting, automatic remediation or unexpected API/workload
mutation occurred. Leave #2135 open until later acceptance work is complete.

## Rollback

Before any deployment, verify the namespace/source are unused before reverting
the staging wrappers. Once activated, re-suspending a HelmRelease stops Flux
reconciliation **but does not stop or uninstall existing workloads**. Keep the
scheduler suspended. If reverting an activated chart, restore the last reviewed
compatible version/values through Git and verify the live state and stored data;
there is no prior deployed Kubescape version at this stage.

For a complete later uninstall, first inventory the exact Helm release and all
chart objects. **The chart's PVC has no keep annotation: Helm uninstall can
delete it.** Export findings and verify a Longhorn recovery point first. If the
PVC must remain, a reviewed preparatory PR must add
`helm.sh/resource-policy: keep` to that PVC through a release post-render patch
and verify it was reconciled before uninstall. Then a reviewed rollback PR can
remove only the Kubescape HelmRelease while retaining its namespace/source. Let Flux
uninstall that release and verify that its APIService and cluster RBAC are gone,
API discovery works and existing applications/admission remain healthy. Confirm
no shared ownership/name collisions before removal. Do not blanket-delete
cluster roles or namespaces, disable unrelated APIServices, or delete unrelated
CRDs. Helm retains CRDs; retain those and any explicitly preserved PVC until
consumers, data retention and Longhorn recovery have been reviewed. Remove the remaining Kubescape-only
namespace/source/storage in a separate deliberate cleanup after uninstall and
data export/backup verification. Never delete the namespace to force recovery.
