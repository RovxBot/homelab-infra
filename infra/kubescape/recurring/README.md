# Controlled recurring assessments

Tracking [#2135](https://github.com/RovxBot/homelab-infra/issues/2135) stays open.
Maintained operations and coverage are in the [Kubescape Wiki](https://github.com/RovxBot/homelab-infra/wiki/Kubescape).
The [scheduled checkpoint](scheduled-acceptance.json) records the first unattended
partial failure and its evidence limits; historical qualification below is not
acceptance of the current cohort or current workload digests.
The two new CronJobs wrap the existing verified client. Both chart CronJobs
remain suspended; chart, image watchers, preventive controls
and PVC sizes are unchanged.

The original scanner's same-image/database cache cannot satisfy fresh recurring
matching. The [small opt-in scanner extension](scanner/README.md) preserves
default callers and SBOM reuse while performing actual matching for this client.
Its tested immutable image replaces only the engine binary. The failed original
execution remains in [first-execution-evidence.json](first-execution-evidence.json).

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
| Idle CPU settling / poll interval | at most 120 seconds / 5 seconds, within the existing execution budget |
| Involved node CPU/memory utilization | below 85%, recent metrics, no pressure |
| DB / reports filesystem free | at least 3Gi / 1Gi |
| Each physical replica disk free | at least max(10Gi, 15%) |
| Additional compressed history | at most 512Mi logical, then stop |

The proposed bounded vulnerability rotation runs at **01:13, 10:13, 13:13 and
19:13 UTC daily**; posture remains **20:13 UTC daily**. Each vulnerability
window retains the four-image limit, single active scan and all existing
resource gates. These scheduled start times avoid the known 07:19 UTC DB
proposal workflow, 02:15 Adelaide Gitea cold backup, 04:15 UTC Longhorn backup
and 04:45 UTC periodic photo backup; confirm maintenance durations and live
resource/history overhead before widening the cohort. CI is unscheduled;
active CI Job Pods and scanner-node utilization remain additional gates.
Runner Deployments alone do not imply an active build. A four-window rotation
is not proof that 16 successful scans/day are sustainable.

Both CronJobs use `Forbid`, a five-minute starting deadline, zero retries and
`Never` restart. Required Pod affinity colocates runners with Kubevuln on its
RWO PVC. A shared filesystem lock serializes both modes, including manual
executions of these templates. Duplicate execution returns non-success without
queueing. Vulnerability completion markers prevent repeated execution within
each UTC six-hour slot; posture markers remain daily. Historical daily
vulnerability markers still block the original 18:00–23:59 UTC slot on
cutover. The shared lock and uncertain-request fence remain unchanged.

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

## Live inventory freshness

The inventory-only refresh runs at minutes 7 and 37 UTC each hour. It
uses the existing assessment identity, read/write lock file and history
subPath on the existing RWO PVC, without the scanner-audience token.
Its only persistent writes are the existing two named status ConfigMaps
and the lock file; it does not replace scan outcomes, launch scans, or
archive new reports. An active scan may cause an intentional skip; this
does not renew evidence. Unresolved request intents block refresh.

Each observation expires after 65 minutes independently of the 72-hour
image scan age. Changed running digest or workload UID, missing targets,
or incomplete Deployment rollout cannot inherit healthy coverage. The
CVE publisher refuses projections older than 65 minutes. A deployment
change can still precede the next inventory observation; this is a
bounded periodic check, not a synchronous admission or event watcher.
No finding is automatically closed on missing evidence.

## Trust, authentication and persisted results

Before submitting, require Helm observed generation/Ready, scanner/storage
readiness, expected immutable GitOps DB pin, mirror runtime imageID and served
metadata, live DB integrity/metadata and publisher-verified lock. The DB is fresh
through day 7, usable in grace through day 14, then blocked; image assessment
coverage expires after 72 hours independently of DB freshness.
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
deduplication prevents repeated execution in the same vulnerability slot and
concurrent image scans.

Posture requests NSA using the existing Memos scope and no host scanner. Fetch
the actual full result by scan ID with `keep=true`, then independently reread
fresh, evaluated named summaries for Memos **and shared RBAC**. Preserve Passed,
Failed, Action Required and Not Evaluated separately, including intentional
RBAC exceptions. Unsupported host/cloud checks cannot become passes. Completed
Jobs or OperatorCommands alone cannot establish a successful assessment.

`healthy` means a trustworthy recent assessment, including any findings, not
absence of vulnerabilities or full compliance. Exit codes: 0 healthy, 2 stale,
3 unknown, 4 failed. Publish unknown before long work, so a killed Job cannot
leave a new healthy status. Fsync every intent before POST. Timed-out, abandoned
or uncertain requests retain `inflight.json`; an observed terminal failed/completed
request from the same engine releases the fence while report checks can still
fail. The next run checks any retained existing ID
and refuses to resubmit. Lost posture responses without a server-generated ID
also fence future scans. GET transport failures yield unknown; POSTs never retry.

## Inventory and bounded expansion

[config.json](config.json) selects six reviewed public application images after
[PR #2190](https://github.com/RovxBot/homelab-infra/pull/2190), with at most four
sequential requests per scheduled execution. New/changed/old evidence takes priority;
failed attempts also rotate so peers are not starved. Successful peer records
survive a partial batch failure. Configured targets do not establish successful
coverage. Posture remains Memos plus shared RBAC. The Wiki and scheduled
checkpoint distinguish historical and currently running digests.

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

The existing pod-report `/kubescape-health.json` and Gatus check consume minimal
schedule status and age fields. The proposed correction makes a failed/unknown
latest attempt override a cached successful timestamp. Detailed inventory remains
in internal status and history. See the Wiki for production revalidation and
recovery; manual template execution proves mechanics only, and a genuine
unattended run is still required.

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
