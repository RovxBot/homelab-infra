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
vulnerability markers conservatively block all slots on their UTC cutover
day, including legacy manually started Jobs. The shared lock and uncertain-request fence remain unchanged.

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

### Immutable-image evidence boundary

The pinned engine applies workload-scoped security exceptions before storing CVE manifests and can refresh a workload summary from cached image data. Identical registry digests therefore do not alone qualify cross-workload findings. Require direct scanner-generated evidence per workload/container; retain previous shared records for audit but mark their coverage unknown until direct reassessment. Reintroducing reuse requires proven exception-independent image evidence or a native per-workload cached-summary contract. No new permissions, scanner requests beyond the existing budget, or deletion of evidence.

### Direct assessment contract for StatefulSets and DaemonSets

The runner recognizes the pinned engine's kind-derived WLID and `<kind>-<name>-<container>` summary name for StatefulSets and DaemonSets. Require observed controller generation, all desired replicas updated/ready/available, matching container template, full Pod membership and (StatefulSet) matching current/update and Pod revision. Reject scaled-zero, incomplete or ambiguous inventory, and summary names requiring sanitization. New kinds require direct scans; shared-image evidence remains Deployment-only until separately qualified. The configured nine targets and publication scope are unchanged; live canaries must be added through a separate reviewed scope change.

### First direct non-Deployment canaries

The reviewed vulnerability cohort now includes twelve container identities, adding WotLK MariaDB (StatefulSet), kube-proxy (DaemonSet) and Cloudflared (Deployment). Four requests per scheduled window, one worker, all existing storage and resource gates, and the 72-hour freshness requirement remain unchanged. Private publication and aggregate health require all twelve; the prior nine cannot satisfy expanded scope. The private publisher retains nine daily creates and ten hourly writes, so larger initial backlogs are deliberately deferred. No live acceptance is implied by configuration; confirm each new kind's independently attributed summary and resource usage.

### Multi-container private CVE projection

One stable private issue identity per reviewed workload. Require qualified evidence from every configured container; use the oldest assessment timestamp, union CVE IDs with highest severity and OR of fix flags, and sum non-CVE match counts. Missing, ambiguous, stale or overflowing evidence yields unknown, never a falsely complete or empty finding set. Existing twelve target identities, publication schema, issue keys and write budgets are unchanged. Sidecars can be added after live qualification. The projection remains bounded to 100 distinct CVEs per workload; an incomplete group is not published as a complete issue update.

### Bounded larger SBOM qualification

The 25 November 01:13 UTC batch rejected the first StatefulSet SBOM at 23,056,159 bytes against 20,971,520 bytes. Candidate scanner-only `MAXSBOMSIZE=33554432` leaves shared Helm config, node-agent resources, engine 3Gi limit, one worker, image limit, schedule, storage and history budgets unchanged. Pinned kubevuln config defaults the key and uses Viper AutomaticEnv; changed size limit invalidates prior TooLarge SBOM verdicts through its native lifecycle. Pinned storage v0.0.382 permits 1 GiB request bodies; runner HTTP reads stay bounded at 64 MiB. Retained report-pair reader rises from 16 to 64 MiB decompressed, with vulnerability and refresh Job memory requests/limits 256/768 MiB (posture unchanged). Release old SBOM/report payloads after archiving before new scan. These are candidate resource bounds, not proof of sufficient peak memory. Verify new engine readiness, scanner-only env, direct MariaDB summary, fresh DB/report identity, no OOM/restarts, storage headroom and retained evidence. Do not bypass gates or delete existing evidence manually. Stop on new storage, response size or resource failure.

### Private CVE eligibility diagnostics

The inventory refresh emits `privateCveReasons` and the general assessment status
stores `coverage.privateCveReasons` as aggregate fixed-code counts (one outcome
per reviewed workload, first blocking container in deterministic order). This
separates missing/ambiguous inventory, nonhealthy assessments, unqualified shared
evidence, missing/overflow/unqualified projections and workload aggregation bounds
from complete producer eligibility. It is not a remote publication receipt or
recovery proof. No workload identities, findings, package details, raw errors or
report references are included in these counts. Existing health gates, private
projection schema, scan limits and recovery gates remain unchanged.

### Bounded larger image qualification

A reviewed target returned `image_too_large` in the 25 November 13:13 UTC vulnerability window at the scanner-only 1 GiB image limit. Increase only `MAXIMAGESIZE` to 1.5 GiB (1610612736 bytes) for the next scheduled qualification. This is a bounded trial, not proof the image fits or can be scanned successfully. The shared chart configuration, 32 MiB SBOM bound, one worker, queue depth one, 3 GiB engine memory and ephemeral-storage limits, 2 GiB observed working-set gate, node-load and storage preflight, request deadlines and retained evidence remain unchanged. Do not increase the limits again without measured image size and resource evidence. Verify rollout, direct fresh workload report, peak sampled working set, engine restarts and terminal status; leave coverage unknown on any failure. If pressure or repeated size rejection persists, use a separately qualified image assessment path rather than silently excluding the target.

### Recover bounded advisory projection overflow

The 26 November 00:37 UTC refresh observed nine healthy assessments but seven complete private projections: two projection overflows, two failed assessments and one skipped assessment. Raise the producer and consumer advisory-ID bound from 100 to 200 per workload; leave the 240 KiB serialized ConfigMap and 256 KiB consumer read limits intact. The full twelve-workload worst-case advisory summary is tested against both bounds. Optional recovery proof and package details retain existing independent bounds and fallback; no advisory-ID truncation. Refresh may reconstruct a legacy overflow only from the exact qualified retained report, summary, successful terminal marker, running identity and current DB, and must reproduce the previous 100-ID overflow verdict before accepting a complete new projection. Preserve original assessment time; missing, inconsistent or still-overflowing archives remain unknown. Recovery proof and automatic state-transition gates are unchanged. Verify refresh and private publisher after rollout; two failed scans and one skipped scan require independent resolution.

### Stable running image with external-service readiness failure

The reviewed single-replica Last.fm bridge can have a running, started container and an observed fully updated Deployment while an upstream service failure makes its application readiness probe fail. Assess that exact running image independently of the application health result, only for the named reviewed bridge container. Require one nonterminating Running Pod, a started/running container, registry digest, exact current template, observed generation and one updated/total replica; duplicate or mixed identities remain ineligible. Recheck the same controller, generation, Pod UID and digest after scanning as before. The ordinary `ready` field, Gatus and Flux application health, and stricter `recoveryScopeReady` gate are unchanged. No readiness bypass for other workloads, no scan resource or schedule change, and no recovery proof from an unready workload. Verify a direct qualified report and preserve the independent application-health alert until its actual recovery.

### Explicit operator catch-up

Ordinary manual clones are subject to the same six-hour completion marker as
scheduled Jobs. A refused duplicate logs a skip without replacing the last
actual assessment. The operator may request **one extra vulnerability attempt
per UTC day** after the current slot's ordinary batch completed, using the
existing CronJob pod template and appending `--catchup` to the assessment
container command. The runner reserves its durable daily marker before
preflight and retains the lock, in-flight fence, four-image maximum and all
resource/identity checks. Only current unqualified assessment rows are
eligible; healthy images (including reporting-only overflow) are not rescanned
merely because a publication projection is incomplete. A failed extra attempt
consumes the allowance. Do not delete completion markers or bypass the fence.
The catch-up does not replace the ordinary slot marker or create a new schedule.
