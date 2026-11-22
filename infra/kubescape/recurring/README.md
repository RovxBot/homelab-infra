# Controlled recurring assessments

Tracking [#2135](https://github.com/RovxBot/homelab-infra/issues/2135) stays open.
The two new CronJobs wrap the existing verified client. Both chart CronJobs
remain suspended; chart, scanner images, image watchers, preventive controls
and PVC sizes are unchanged.

## Qualified budget

[Resource evidence](resource-qualification.json) combines fresh kubelet/cAdvisor
measurements with the previous 356-second hydration. Kubevuln currently uses
about 915Mi working memory; 1.35GB cgroup usage includes about 1.31GB filesystem
cache. Previous hydration peaked at 1,041Mi working memory and 3.22GB cgroup
usage including reclaimable cache, without OOM/restart. Keep the **3Gi** limit.
Measured samples are not instantaneous peak guarantees.

| Gate | Budget |
| --- | --- |
| Active scans across both modes | 1, shared nonblocking PVC flock |
| Client queued requests | 0; engine retains maximum 1 worker + 1 queued |
| Engine / client timeout | 300 / 360 seconds |
| Job / cache audit deadline | 1800 / 600 seconds |
| POST retries / Job retries | 0 / 0 |
| Working memory / idle CPU before scanning | below 2Gi / 250m |
| Involved node CPU/memory utilization | below 85%, recent metrics, no pressure |
| DB / reports filesystem free | at least 3Gi / 1Gi |
| Each physical replica disk free | at least max(10Gi, 15%) |
| Additional compressed history | at most 512Mi logical, then stop |

Vulnerability runs **19:13 UTC daily**, posture **20:13 UTC daily** (05:43 /
06:43 Adelaide the following day during daylight saving). This avoids the
07:19 UTC DB proposal workflow, 02:15 Adelaide Gitea cold backup, 04:15 UTC
Longhorn backup and 04:45 UTC periodic photo backup. CI is unscheduled; active
CI Job Pods and scanner-node utilization are additional gates. Runner
Deployments alone do not imply an active build.

Both CronJobs use `Forbid`, a five-minute starting deadline, zero retries and
`Never` restart. Required Pod affinity colocates runners with Kubevuln on its
RWO PVC. A shared filesystem lock serializes both modes, including manual
executions of these templates. Duplicate execution returns non-success without
queueing. Same-day identical mode/cohort completion markers prevent resubmission.

Compressed previous/new reports, SBOMs, request IDs and actual posture results
use `assessments/` on the existing DB PVC. The runner's writable subPath is
limited to this directory; its small init only creates that directory. Audit
Jobs mount DB/reports **read-only**, without API tokens. No extra PVC is added.
History adds at most 1.5Gi replicated data before the 512Mi gate, excluding
retained snapshots; no automatic deletion occurs. Export and review retention
before that limit is reached.

Existing recovery snapshots remain charged. DB allocation is about **7.31GB
per replica**, 21.94GB across three; reports about 155MB per replica. Every
volume requires three running healthy replicas and sufficient physical free
space. Existing unrelated Longhorn scheduling pressure is not remediated here.
Database replacement separately retains its larger budget:
`2*new expanded DB + 2*archive + 512Mi`, with existing hydrated DB and history
charged as used. Passing the scan threshold does not approve replacement.
Database growth and additional cohorts require requalification.

## Trust, authentication and persisted results

Before submitting, require Helm observed generation/Ready, scanner/storage
readiness, expected immutable GitOps DB pin, mirror runtime imageID and served
metadata, live DB integrity/metadata, publisher-verified lock and **72-hour age**.
Jobs use the byte-exact existing `kubescape_db.evaluate`. The transient pinned
Grype Job checks the hydrated xxh64; Python checks SQLite metadata and SHA256.
Audit evidence expires after 15 minutes and is bound to engine Pod UID.
Replacement, unavailable storage, restart, disk pressure or exhausted resources
prevent submission. No API readiness signal can substitute for freshness.

The dedicated service account has rotating projected `kubescape.io` tokens for
scanner APIs and normal audience tokens for Kubernetes. Credentials never enter
Git, command arguments, evidence or logs. Inventory/report permissions are
read-only. Writes are namespace-scoped audit Jobs and one named status
ConfigMap. No Secret, node-proxy, exec, policy or application writes are granted.
Underlying Kubevuln API freshness enforcement is still not claimed.

Vulnerability success requires terminal `succeeded/completed`, a **new** report
timestamp, expected DB, nonempty SBOM artifacts, verified manifest/config hashes
and independent named rereads. The namespaced summary must reference that full
report with the requested digest, WLID and container. Workload UID/generation,
Pod UIDs and running digest must stay unchanged. Each request uses the current
running immutable digest. Archive previous reports before submission; a stable
image-derived scan ID never proves a new assessment.

The initial cohort has no duplicate images. Cross-workload reuse is deferred
until its namespaced summary-attribution semantics are qualified; current
deduplication prevents repeated same-day execution and concurrent image scans.

Posture requests NSA using the existing Memos scope and no host scanner. Fetch
the actual full result by scan ID with `keep=true`, then independently reread
fresh, evaluated named summaries for Memos **and shared RBAC**. Preserve Passed,
Failed, Action Required and Not Evaluated separately, including intentional
RBAC exceptions. Unsupported host/cloud checks cannot become passes. Completed
Jobs or OperatorCommands alone cannot establish a successful assessment.

`healthy` means a trustworthy recent assessment, including any findings, not
absence of vulnerabilities or full compliance. Exit codes: 0 healthy, 2 stale,
3 unknown, 4 failed. Publish unknown before long work, so a killed Job cannot
leave a new healthy status. Fsync every intent before POST. Failed, timed-out or
uncertain requests retain `inflight.json`; the next run checks the existing ID
and refuses to resubmit. Lost posture responses without a server-generated ID
also fence future scans. GET transport failures yield unknown; POSTs never retry.

## Inventory and bounded expansion

[config.json](config.json) starts with Memos. After **both production Memos paths
succeed**, add only `homepage/Deployment/homepage-assets/nginx` through a reviewed
PR. This public unprivileged nginx static-asset service has one replica,
20m/64Mi requests and 200m/256Mi limits. It is noncritical, has an accessible
immutable Docker Hub digest and needs no privileged scanning. This expands
image coverage; posture remains Memos plus shared RBAC. Remove only that entry
through GitOps if it exceeds budget, preserving Memos and historical evidence.

`kubescape-assessment-status` contains `vulnerability.json`, `posture.json` and
`coverage.json`; full gzip evidence and `records.json` remain on the PVC.
Inventory records namespace, controller kind/name/UID, container, running
digest, last success, DB identity, age, severity counts, fixes, attempt state and
exclusions. States distinguish healthy, never_scanned, stale, failed, unknown,
deliberately_excluded and unsupported. Scaled-zero/unscheduled controllers have
no claimed running digest. Operator readiness never implies coverage. Consumers
must recalculate freshness against their own clock and honor generatedAt /
validUntil. Rebuild read-only using an exported history directory:

```sh
PYTHONPATH=scripts python3 infra/kubescape/recurring/runner.py inventory --local \
  --config infra/kubescape/recurring/config.json \
  --expected infra/kubescape/grype-database.json --history .work/exported-assessments
kubectl -n kubescape get configmap kubescape-assessment-status -o json
```

No monitoring platform, Gatus check or incident bridge is added. Operational
monitoring stays under #2134/#2138 and #2135 remains open. Qualify the **deployed**
production templates without waiting for tomorrow:

```sh
kubectl -n kubescape create job --from=cronjob/kubescape-assessment-vulnerability assessment-vulnerability-acceptance
# Verify actual results and JSON before proceeding.
kubectl -n kubescape create job --from=cronjob/kubescape-assessment-posture assessment-posture-acceptance
```

Record manual versus unattended execution explicitly. A manually triggered Job
qualifies mechanics only. Leave the next unattended observation as a follow-up.

## Recovery

1. Inspect JSON, Job conditions, saved intent and actual job/scan ID. Old DB
   => stale; replacement/unavailability/low-space => failure or unknown without
   submission. Missing/incomplete/old reports cannot pass.
2. For uncertainty, timeout, restart or changed image, investigate the existing
   ID and persisted results before retrying. A restarted engine may have lost
   in-memory status; that is unknown.
3. Suspend both **new** CronJobs through a reviewed PR when investigation is
   needed. Wait for active Jobs and actual scanner work to finish. Preserve
   the chart's suspended schedules, all PVCs, exports and recovery snapshots.
4. Correct freshness with the existing reviewed immutable update or restore
   storage/resources. Never clear DB caches, delete reports or restart unrelated
   applications. Recheck loaded identity and fresh audits after recovery.
5. Once the previous request is demonstrably terminal or its process is gone,
   export history and archive `inflight.json` under a unique recovery filename
   using a reviewed bounded maintenance Job. This recovery acknowledgement is
   intentionally never automated. Preserve `.done` markers. Requalification of
   a new day/cohort permits a new run.
6. Unsuspend via GitOps and run the template once; verify actual reports,
   inventory, Flux, node/application health and preventive controls. A code
   rollback alone cannot make old databases or assessments fresh.

Failure tests use isolated fault injection; they do not claim destructive
production outages were induced. Committed acceptance records actual production
checks, resource/storage growth and remaining unattended observations.
