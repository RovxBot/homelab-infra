# Kubescape assessment

Tracking: [#2135](https://github.com/RovxBot/homelab-infra/issues/2135), which remains
open for broader coverage and monitoring. Flux owns this release and its dedicated
child Kustomization with health waiting. Chart **1.42.0** is unchanged; archive
SHA-256 `be45f83398f28db8f2ff395e5d8ab411328041e9e2165a42e5fb92a4ba369079`.

The completed NSA assessment and its limitations are preserved in the
[posture baseline](production-baseline.md). Image scanning qualification,
database provenance, acceptance evidence and coverage are recorded in the
[image assessment](image-assessment.md). Neither baseline represents cluster-wide
coverage. Both recurring scan CronJobs remain **suspended**.

## Manual image assessment

Kubevuln **v0.3.578** contains Grype **v0.121.0** and Syft **v1.55.0**. It generates
SBOMs in process; the optional SBOM sidecar and every node/host collector remain
disabled. Full SBOMs and vulnerability manifests persist through the existing
storage API and its 5Gi Longhorn PVC. The separate **10Gi RWO Longhorn** cache
holds the Grype database. Both PVCs carry `helm.sh/resource-policy: keep`.

| Component | CPU request / limit | Memory request / limit |
| --- | --- | --- |
| Posture scanner | 250m / 600m | 400Mi / 1Gi |
| Operator | 50m / 300m | 100Mi / 300Mi |
| Storage | 100m / 1500m | 400Mi / 1500Mi |
| Kubevuln | 300m / 1000m | 1Gi / 3Gi |
| Offline DB HTTP server | 150m / 150m | 200Mi / 200Mi |
| Each scheduler / certificate init | 10m / 100m | 32Mi / 128Mi |

Kubevuln requests/limits 1Gi/3Gi ephemeral storage, limits compressed images to
512MiB, SBOMs to 20MiB, and each scan to five minutes. It has one worker and one
accepted outstanding request. `/tmp` remains ephemeral. The cached database is
2.50GiB before hydration; allow old/new databases and the archive concurrently.
The 10Gi PVC has three replicas (30Gi provisioned capacity); report storage has
three 5Gi replicas. No dedicated monitoring infrastructure is introduced.

**Disabling `continuousScan` does not disable automatic image scans.** In pinned
operator v0.2.209, `HandleWatchers` starts Pod and ContainerProfile watchers when
its Kubevuln component is enabled, including an initial existing-Pod scan. This
release's exact ConfigMap post-render patch sets operator-visible
`components.kubevuln.enabled` and `components.kubevulnScheduler.enabled` false.
Only the operator mounts `ks-capabilities`. Helm still provisions the engine
and suspended scheduler; the engine uses `ks-cloud-config`. This deliberately
requires direct authenticated Kubevuln requests and also disables operator image
commands. Posture command processing remains enabled. CI compares the complete
raw/rendered objects and permits exactly those two routing changes.

`includeNamespaces: memos` remains a defense for posture and any future operator
routing. It is **not** an authorization boundary for direct Kubevuln callers.
For each manual scan, capture the running Pod/container imageID, Deployment UID,
namespace UID, node architecture and immutable registry manifest. Submit only
that image to `/v1/scanImage`, with its workload WLID, container name, unique job
ID and explicit platform. Use a short-lived operator ServiceAccount token with
chart audience `kubescape.io` through a localhost-only port-forward; existing
`operator-api-caller` RBAC grants the scan API. Never disable API authentication,
publish tokens, create broad scan schedules, or submit the scheduler's empty
all-workload designator. Check `/v1/scanStatus/<jobID>` and GET the full named
SBOM and VulnerabilityManifest: HTTP acceptance alone is not scan completion.

## Locality and permissions

No SaaS backend, account, access key, synchronizer, telemetry destination,
remediation, admission webhook, NetworkPolicy enforcement, DaemonSet, node
agent, eBPF, runtime or host scanning is enabled. Restricted PSA and Kyverno
remain enforced. Registry image retrieval still requires network access. Grype
loads only the pinned local HTTP mirror; `kubescapeOffline` alone does not
supply a vulnerability database or prevent database downloads.

The additional Kubevuln ClusterRole reads/writes vulnerability reports and SBOMs,
reads ContainerProfiles, and deletes only SBOM kinds to replace stale TooLarge
markers. Its namespace Role creates OperatorCommands and reads Jobs. Auth
delegation creates TokenReview/SubjectAccessReview requests. It cannot mutate
application Deployments, read application Secrets or alter admission policy.
The operator's newly rendered registry-credential Secret rule is removed: manual
public-image scans do not require it. The mirror has no token automount or RBAC grants. Existing posture/RBAC grants
and scoped certificate APIService permissions are preserved. Exact **61** chart
objects are in `rendered-inventory.json`.

## Qualification and GitOps

Use pinned Helm 3.20.2, Kustomize 5.5.0, Kyverno 1.20.0 and kubeconform 0.6.7:

```sh
python3 -m unittest discover -s scripts/ci/tests -p test_kubescape.py -v
python3 scripts/ci/check_kubescape.py --active-review \
  --base-ref origin/main --expected-kyverno-version 1.20.0
kustomize build infra/kubescape
kustomize build --load-restrictor LoadRestrictionsNone clusters/home
kubeconform -strict -ignore-missing-schemas -kubernetes-version 1.34.0 \
  -summary .work/kubescape-preflight/rendered.yaml
```

The preflight enforces exact chart/inventory/post-render deltas, pinned images,
manual routing, both suspended CronJobs, data retention, scan bounds, security
contexts and resource budgets. Kyverno must evaluate all **48** expected CEL
results without failures, errors, skips or warnings. Before merge, server-side
dry-run the exact render and restricted Pod templates (substitute the default
ServiceAccount only for accounts that do not yet exist). After Flux deploys,
verify actual accounts/Pods, imageIDs, PVCs, APIService, TLS, local DB metadata,
no automatic results, and healthy applications before the explicit request.

The posture manual Job remains available through `kubescape-scheduler`; command
completion means dispatch, so require scanner completion and a fresh, nonempty
named `workloadconfigurationscansummaries` GET. LIST zeros omit control details.
Preserve existing results; do not confuse CVEs with posture control failures.

## Freshness and updates

The local mirror is immutable and does not refresh itself. No mutable
`v6-latest` tag, DB rollout CronJob or automatic image update is enabled. Kubevuln
rechecks the same local mirror roughly every 24 hours; this **cannot** make a
frozen database current. Readiness can remain healthy with an aging loaded DB.

Before every separately authorized image scan, compare publisher and mirror
metadata, require archive checksum integrity, confirm the internal SQLite build
and schema, and reject an unknown/future build time or a database older than
**72 hours**. Record publisher lag explicitly. Never infer freshness from Pod
age, pull time, a successful download, cache presence or readiness. The current
one-off results naturally age; there is no recurring coverage promise.

Update through a reviewed Git PR: resolve the official Kubescape mirror index
digest, verify platform/layer hashes, inspect `databases/v6/latest.json`, verify
its archive SHA-256 and SQLite metadata/integrity, compare Anchore's current
index, and qualify storage/headroom. Change the pinned SHA, regenerate/review
rendered inventory, run preflight/admission checks, merge, then verify the live
mirror and cache before another explicit assessment. If the mirror exceeds the
age limit, obtain and verify a newer compatible official artifact; do not relax
freshness checks to force a successful assessment.

## Rollback

Keep the scan CronJobs suspended and operator image routing disabled. To stop
an in-flight image scan, merge a scoped change setting only
`kubevuln.replicaCount: 0`; allow its termination grace period and verify the
Pod exits, storage/APIService and posture remain available. Retain both PVCs.
Update the exact CI expectation for this deliberate containment change.

For feature rollback, revert the image activation values/patches through Git,
**retaining the `keep` annotations on both PVCs in the deployed release before
removing the cache object**. Restore the previous posture render, scanner images,
restricted security contexts and suspended posture scheduler. Let Flux reconcile;
verify HelmRelease/child Ready, discovery/TLS, posture named summary and existing
applications. The DB PVC can remain orphaned with its contents; inventory its
ownership before any later reuse. Findings stay on the retained storage PVC.
Export named results and verify a Longhorn recovery point before deliberate
storage deletion. Do not uninstall Helm, delete the namespace/CRDs, remove the
APIService manually, or delete findings as routine rollback. Suspending a
HelmRelease stops reconciliation but does not stop installed workloads.
