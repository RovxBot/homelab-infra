# Production NSA posture baseline — 22 November 2026

**Verified:** deployed and scanned through activation [PR #2177](https://github.com/RovxBot/homelab-infra/pull/2177),
merged as `f9b91ce1680e74f5e7932c67b7fb7766ad5b3014`.
Tracking [#2135](https://github.com/RovxBot/homelab-infra/issues/2135) stays open
for image vulnerability scanning and broader coverage. The recurring CronJob
remains suspended, with no automatic scan scheduled.

## Deployed release and readiness

Chart **kubescape-operator 1.42.0**, scanner **v4.0.20**, operator **v0.2.209**,
storage **v0.0.382**, certificate/scheduler ops-tool **0.2.0-r1**. Exact image
digests are in `helmrelease.yaml`; running imageIDs match those pinned digests.
Scanner build identifies source `ec65d3184f6bab7d1ad6e5db9d970d452316d253`.
The NSA policy bundle is baked into that immutable scanner image; per-scan
downloads and rule updates are disabled. No standalone policy-bundle hash was
extracted.

HelmRelease and Flux child Kustomization Ready; all three Deployments **1/1**,
all Pods Ready with **zero restarts**. Both certificate init containers exited
zero in approximately one second each. The public leaf certificate verifies
against the APIService CA for `storage.kubescape.svc`; TLS verification remains
enabled. APIService Available and full Kubernetes API discovery work.

The **5Gi RWO Longhorn PVC is Bound**, mounted at `/data`, with fsGroup 65532;
its volume is attached and healthy with three replicas. Certificate-init RBAC
permits get/patch only on the Kubescape APIService: live authorization confirms
its own APIService is allowed and the metrics APIService is denied.

Startup events were bounded: initial PVC scheduling/attachment, one storage
readiness HTTP 500 before initialization completed, and the expected CronJob
`UnexpectedJob` event for the manually created Job. No ongoing readiness,
admission, eviction, OOM or restart fault was observed. The operator's bare-metal
cloud-provider lookup logs an error; this did not prevent assessment.

## Execution and attributable results

Times below are UTC; the scan ran **22 November 2026, 09:37:18–09:37:23 Adelaide**.

| Evidence | Value |
| --- | --- |
| Manual Job | `kubescape/kubescape-canary-001` |
| Job UID | `a15f0a6f-0567-4415-ad7a-ba6d25ffdf1a` |
| OperatorCommand | `kubescape/kubescape-scheduler-zbpqv` |
| Command UID | `4e0f4573-7038-41b6-9254-aba605126534` |
| Command GUID | `a7722563-3e52-4351-91de-b961845e3c17` |
| Scanner ID | `cd902c09-6e81-413a-99a0-dac8bfbbd117` |
| Job creates command | `2026-11-21T23:07:13Z` |
| Command processed/completed | `2026-11-21T23:07:18Z`; completed true, no status error |
| Scanner start / done | `2026-11-21T23:07:18Z` / `2026-11-21T23:07:23Z` |
| Persisted report time | `2026-11-21T23:07:21.097616523Z` |
| Namespaced target | `memos`, UID `3eb9baf6-0f47-46ab-9d4e-c089f59d0953` |
| Workload | `apps/v1 Deployment/memos`, one running Pod |
| Workload UID / resourceVersion | `1e1e25ca-4b09-4d6e-98f5-afd3540fc866` / `188602150` |
| Workload image | `neosmemo/memos:26.11.1@sha256:5dcd6e6b973a55b964f2fe2ccdaa33319463ab448a23404ebb5d2acbd19299ba` |
| Framework | NSA, 26 controls; host scanner false, keepLocal true |

The Job owned the command, operator/scanner logs correlated the scanner ID,
and the scanner completed after command dispatch. Before the canary there were
**zero commands and zero stored summaries**. After it there were **24 persisted
summaries**: four Memos namespaced objects (Deployment, ConfigMap, ServiceAccount,
NetworkPolicy), the Memos Namespace, and 19 synthetic shared RBAC identities.
The scan is namespace-filtered; it is not evidence of cluster-wide workload
coverage. Shared RBAC checks still read cluster metadata.

Scanner framework results: **18 passed controls, 3 failed, 5 Action Required**;
rounded compliance **76** (resource-summary display 75.91%). Across individual
persisted summaries there are **51 passed and 6 failed resource/control results**:
five Medium failures and one Low failure, affecting four distinct resources.
These counts describe different aggregation levels and must not be conflated.

| Representative finding | Persisted evidence |
| --- | --- |
| Memos immutable filesystem, `C-0017`, Low | `memos/deployment-memos`: failed; 14 other controls explicitly passed |
| Command execution permissions, `C-0002`, Medium | Three shared RBAC identities failed, including the existing administrator and operator grants |
| Administrative roles, `C-0035`, Medium | Two administrator identities failed |

These are posture observations, not CVEs. Existing administrator privileges can
be intentional; no suppression, remediation or policy/workload modification was
performed. The existing built-in system administrator exception is represented
as `w/exceptions`; it does not turn that result into an unconditional pass.

**Storage verification:** named summary GETs expose nonempty controls, severities,
namespace/kind/name labels, WLID and `kubescape.io/report-created-at`. They were
read again minutes later with unchanged findings. The scanner wrote results
through the aggregated API, and Longhorn allocated size increased by 400KiB.
The source Deployment UID/resourceVersion and image were captured alongside the
reports and stayed unchanged; the summary's own UID is not the Deployment UID.
Framework and scan ID attribution comes from the pinned request plus correlated
logs; those fields are not included in the workload summary schema.

## Coverage and interpretation limits

- The scanner reports **83% coverage** and **24/26 controls evaluated**. Preserve
  both values as reported: 83% is not the arithmetic fraction 24/26.
- `C-0069` and `C-0070` cannot evaluate kubelet anonymous access/client TLS without
  hostdata/KubeletInfo. Host collection and node agents remain disabled.
- Cloud metadata for EKS/GKE/AKS is unavailable on bare-metal Talos. Secret/etcd
  encryption, audit logging and legacy PSP checks report Action Required; this
  is not a claim that those protections are absent. PodSecurityPolicy is no
  longer served; existing PSA enforcement was independently verified.
- Scanner lacks read access to optional `securityexceptions` CRDs and falls back
  to primary/baked-in exceptions. Both custom exception resources are empty in
  this baseline. Qualify their read permissions before using custom exceptions.
- **LIST responses omit controls and show zero severities even for failures.**
  Read each summary by name. The cluster summary/list zeros are not clean-scan
  evidence. Full WorkloadConfigurationScan objects were not emitted; persisted
  named summaries are the verified findings interface for this configuration.
- OperatorCommand completion means dispatch succeeded; it preceded actual scan
  completion by five seconds. Require results, not just a completed Job/command.
- This is a short canary. Crash/restart durability and backup/restore were not
  exercised; persistent-volume writes and later API rereads were verified.

## Measured consumption and frequency

Sampled approximately every ten seconds using metrics-server during deployment
and assessment. Values are sampled maxima, not instantaneous peak guarantees;
the scan was shorter than a metrics collection window.

| Component | Sampled max CPU | Sampled max working memory | Post-scan idle CPU / memory |
| --- | --- | --- | --- |
| Scanner | 60.3m | 65.2Mi | 5m / 65Mi |
| Operator | 33.3m | 20Mi | 3m / 18Mi |
| Storage | 104.1m | 51.4Mi | 5m / 50Mi |

Memory remains below configured requests and CPU below limits; storage briefly
used slightly more than its 100m CPU request during initialization. Short certificate init CPU/memory
peaks were not captured by metrics-server; exit status, duration and absence of
OOM/restarts were verified. PVC filesystem use was **507,904 bytes (496KiB)**;
Longhorn allocated **154,296,320 bytes (~147.2MiB) per replica**, including filesystem
overhead. The three 5Gi replicas provision 15Gi of logical capacity before snapshots.

API-server sampled maxima were **343m / 502m / 153m CPU** across the three control
planes, with working memory below 1.71Gi each. `/readyz` succeeded throughout
sampling, with low client request times recorded in the local samples. No claim of a request-rate or
latency benchmark is made. All nodes remained Ready without pressure. Existing
Pod restart counts were unchanged; no new unhealthy application Pods appeared.

Recommend **one scan daily at most for this scope**, single command at a time,
after explicit acceptance of recurring scans. Keep current budgets and the
CronJob suspended. Re-measure broader namespace and peak load before expansion
or shorter intervals. Do not infer whole-cluster sizing from this five-second
Memos scan.

## Locality, security and future monitoring

Effective configuration remains offline/local, with empty account/accessKey and
no backend/server or synchronizer. Scanner startup explicitly logs no backend
configured and skips SaaS wiring. No report export appears in scan logs; results
are served by local storage. Operator telemetry has no configured base,
per-signal, legacy or discovery endpoint. Its no-access-key startup warning alone
does not prove an exporter exists; the pinned implementation leaves unconfigured
signals off. Egress is not network-isolated and no packet capture was performed.

Kyverno Deployment and CEL policy specs, admission webhook definitions, namespace
PSA labels, Cilium config and Memos Deployment spec match preactivation snapshots.
Live negative dry-runs still reject a missing CPU limit and PSA privilege
escalation. Scanner cannot read Memos Secrets; operator cannot patch Memos
Deployments. No new admission webhook, privileged workload or node agent exists.

For future Gatus integration, use an authenticated collector to expose separate
availability, completion, freshness and coverage outcomes:

1. Component readiness: scanner `:8080/readyz`, operator `:4002/v1/readiness`,
   storage `:8443/readyz` over HTTPS; also require APIService Available.
2. Submission: read OperatorCommands, require started/completed and no error.
3. Assessment: GET each expected workload summary by name, require nonempty
   controls and matching namespace/kind/name/WLID; compare report-created-at with
   command start and captured workload UID/image inventory. Require the expected
   NSA request/coverage evidence. Any pending/missing/error/unsupported result
   must remain distinguishable from an evaluated pass.
4. Freshness: after daily scheduling is separately accepted, alert when the last
   accepted report is older than **26 hours**. The current one-off baseline will
   naturally age; no daily freshness promise is enabled here.

Scanner `/v1/status?ID=...` is an optional corroborating completion interface; it
requires authenticated scan-read access and the correct service-token audience.
An unauthenticated API proxy request was rejected. Do not expose it anonymously
or treat readiness as scan freshness. No Gatus integration or finding correlation
was implemented.

Next vulnerability phase needs a scoped running-image/digest inventory, scanner
and database CPU/memory/PVC sizing, offline database/update provenance and network
review, qualified permissions, local CVE/severity/fix attribution, and its own
controlled canary. Vulnerabilities, SBOM collection, runtime detection and node
agents remain disabled.

Raw reports, logs, resource snapshots, runtime samples and evidence checksums are
retained locally in `.work/task22/`; public tracking contains summarized evidence.
Use the [runbook rollback](README.md#rollback) if required; suspending a release
alone does not stop installed workloads, and uninstall can delete its PVC.
