# Kubescape posture assessment

Tracking: [#2135](https://github.com/RovxBot/homelab-infra/issues/2135).
Production activation qualification: 22 November 2026, from main `ac958cb5`.
The release is active through the dedicated Flux child Kustomization, with
health waiting enabled. **The posture CronJob stays suspended.** Only one
manual NSA canary is authorized; recurring scans require separate acceptance.
Actual deployment and canary evidence is recorded on the tracking issue and
in the production baseline once the assessment completes.

## Bounded configuration

Reuse the staged chart `kubescape-operator` **1.42.0**, SHA-256
`be45f83398f28db8f2ff395e5d8ab411328041e9e2165a42e5fb92a4ba369079`.
Kubernetes 1.34/Talos uses normal restricted containers and a CSI PVC; no host
mounts, host namespaces, node agents, DaemonSets or admission webhooks are added.
All instantiated images are pinned by version and digest in `helmrelease.yaml`.

| Component | Version | CPU request / limit | Memory request / limit |
| --- | --- | --- | --- |
| Scanner | v4.0.20 | 250m / 600m | 400Mi / 1Gi |
| Operator | v0.2.209 | 50m / 300m | 100Mi / 300Mi |
| Storage | v0.0.382 | 100m / 1500m | 400Mi / 1500Mi |
| Scheduler and each certificate init | ops-tool 0.2.0-r1 | 10m / 100m | 32Mi / 128Mi |

The three Deployments request **400m CPU / 900Mi**, with limits of **2400m /
2824Mi**. Sequential certificate init budgets do not increase storage's effective
Pod requests. `WORKERCONCURRENCY=1` bounds operator command workers; it does not
prove scanner internals are serial. Submit one scan at a time.

The existing **5Gi RWO Longhorn** PVC uses three replicas (15Gi provisioned
replica capacity before snapshots). Storage updates use Recreate. TLS uses the
existing initContainer certificate strategy, never insecureSkipTLSVerify.
Post-rendering adds only the two named init-container budgets, scheduler
suspension, and `resourceNames: [v1beta1.spdx.softwarecomposition.kubescape.io]`
on certificate-init get/patch access. It cannot patch unrelated APIServices.

RBAC review: scanner cluster-wide workload/RBAC/policy metadata reads support
cluster-scoped NSA checks; scan-record writes persist findings. Operator
permissions manage commands, namespace-local scheduling templates and stale
assessment cleanup. Storage reads support its aggregated API. Preserve those
upstream grants for compatibility. No component can patch application workloads
or modify NetworkPolicies. Secret collection is disabled; certgen can create
Secrets only in its namespace and get its named TLS Secret. Standard auth
delegation creates TokenReview/SubjectAccessReview requests. The exact inventory
contains **47** chart objects; `rendered-inventory.json` records the narrowed
certificate rules and budgets. Changes outside the qualified post-render edits
fail closed.

Namespaced scope is **memos**, selected because Deployment/memos has one running
Pod, an identifiable UID and digest, and is a noncritical notes application.
The zero-replica test Deployment in `default` is insufficient coverage evidence.
Cluster-scoped NSA checks still inspect shared metadata. Scanning reads Kubernetes
configuration; it does not connect to or mutate Memos or its database.

Only operator, configuration scanning and offline mode are enabled. Image
vulnerabilities, SBOMs, runtime, host/node scanning, admission, remediation,
automatic upgrades, continuous scans, service discovery, Prometheus, and external
synchronization remain disabled. The daily CronJob is independently suspended.
The chart embeds unused scheduler templates in ConfigMaps; those are inert.
Keep `server`, `account`, `accessKey` and cloud credential references empty,
`keepLocal: true`, `downloadArtifacts: false`, `skipUpdateCheck: true`, and rule
updates disabled. Policies are baked into the scanner image. This requests
local results; it does not create an egress-isolation policy. Registry pulls
still need network access. Inspect effective config and logs for reporting.

## Qualification and deployment

Preactivation verified: Kyverno **1.20.0 / chart 3.10.0**, two admission replicas
and all controllers Ready; live CEL ValidatingPolicy specifications match the
repository. API readiness/discovery and Cilium are healthy. Flux is reconciled
to main. All eight nodes are Ready without pressure; attached Longhorn volumes
are healthy and all storage nodes schedulable, with sufficient free capacity.
Existing detached volumes have unknown robustness; none is an activation fault.

The effective chart passes Kyverno CLI 1.20.0: **28 passes, zero failures,
errors, skips or warnings**. All three Deployments and the suspended CronJob
pass live server-side admission dry-runs. Restricted PSA Pod dry-runs preserve
all security fields but temporarily substitute the existing default
ServiceAccount because release ServiceAccounts do not exist before installation;
actual chart ServiceAccounts and Pods require verification after deployment.

The preflight defaults to suspended mode; active mode must be explicit and
requires `suspend: false` plus child `wait: true`. Both modes keep the same strict
inventory, privilege, optional-feature, PSA-field and Kyverno checks. CI selects
the declared mode and tests both acceptance and rejection paths. Use pinned Helm
3.20.2, Kustomize 5.5.0, Kyverno 1.20.0 and kubeconform 0.6.7:

```sh
python3 -m unittest discover -s scripts/ci/tests -p test_kubescape.py -v
python3 scripts/ci/check_kubescape.py --active-review \
  --base-ref origin/main --expected-kyverno-version 1.20.0
kustomize build infra/kubescape
kustomize build --load-restrictor LoadRestrictionsNone clusters/home
kubeconform -strict -ignore-missing-schemas -kubernetes-version 1.34.0 \
  -summary .work/kubescape-preflight/rendered.yaml
```

Merge only after required CI passes. Let Flux reconcile; verify HelmRelease and
child Kustomization Ready, all three Deployments Ready, PVC Bound, both certificate
init containers exit zero, TLS CA bundle present, APIService Available, and API
discovery healthy. Check events, imageIDs, restarts, resource usage, existing
applications and security controls before submitting a canary.

## Controlled scan and acceptance

```sh
kubectl -n kubescape create job --from=cronjob/kubescape-scheduler kubescape-canary-001
kubectl -n kubescape logs job/kubescape-canary-001
kubectl -n kubescape get operatorcommands.kubescape.io -o yaml
kubectl -n memos get workloadconfigurationscans.spdx.softwarecomposition.kubescape.io -o json
kubectl -n memos get workloadconfigurationscansummaries.spdx.softwarecomposition.kubescape.io -o json
```

Job completion proves command submission only. Require the created command's
completion without error, scanner evaluation, and nonempty persisted results
with Memos namespace, workload identity, scan time, NSA coverage and evaluated
passes/findings. Correlate command GUID and times with logs. Record unsupported
checks separately; empty, missing, stale and incomplete scans must never count
as clean. Keep raw reports locally; publish only summarized evidence.

Gatus follow-up should independently check component readiness/APIService and
scan completion/freshness through an authenticated collector with read-only
OperatorCommand and scan-summary access. Require a completed error-free command,
matching workload identity and scan timestamp, nonzero evaluated controls, and a
freshness threshold based on the accepted scan interval. A readiness endpoint
alone cannot prove a successful or current assessment. Do not implement that
integration or GitHub finding correlation here.

Start with at most one manual scan per day after canary acceptance; keep the
CronJob suspended in this task. Qualify broader coverage and peak consumption
before recommending more frequent scanning. Vulnerability scanning needs its
own image/digest inventory, scanner and database resources/storage, offline
artifact/update strategy, permissions and network review, attributable CVE/fix
results, and another bounded canary. SBOM/runtime/node agents remain separate.

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
