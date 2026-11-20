# Cluster alert repairs — 19 November 2026

Related incidents: [Gitea backup #2085](https://github.com/RovxBot/homelab-infra/issues/2085),
[Koito #2099](https://github.com/RovxBot/homelab-infra/issues/2099),
[cluster health #2100](https://github.com/RovxBot/homelab-infra/issues/2100).
The PR prepares GitOps changes only. No rollout, production backup, restart,
scale operation, merge or incident closure was performed during preparation.
Server dry runs did not persist changes. Do not use PR auto-close keywords for
these incidents: Gatus must resolve them from endpoint evidence.

## Evidence and scope

Investigation began from `02905ab4`; concurrent `main` commit `00e8c3b2` changes
only WotLK guild configuration and does not overlap the repair. Observations
below were taken on 19 November Adelaide time (18 November UTC). Raw cluster
logs, credentials and object dumps are deliberately excluded from this record.

- **Koito startup confirmed:** live container status reports StartError for
  `/usr/bin/node` and CrashLoopBackOff. The exact pinned image's amd64 registry
  config specifies ENTRYPOINT `/nodejs/bin/node`, CMD
  `/app/src/backend/index.ts`, working directory `/app` and default UID 65532.
  The [0.21.0 Dockerfile](https://github.com/FoxxMD/multi-scrobbler/blob/0.21.0/Dockerfile)
  confirms distroless, root-owned readable application files and configurable
  data directories. Remove only the obsolete command override; retain image,
  UID/GID/fsGroup 1000, dedicated PVC, projected configuration, credentials and
  writable `/tmp`. No shell/exec probe or root initialization is required by
  this manifest. `/config`, data and logs remain on the existing PVC, whose
  ownership must be checked after rollout. Upstream
  [health tests](https://github.com/FoxxMD/multi-scrobbler/blob/0.21.0/src/backend/tests/server/health.test.ts)
  still exercise `/api/health`; the server explicitly exempts health routes
  from UI authentication. HTTP health does not prove a successful scrobble.
- **Backup authentication confirmed in configuration:** the supplied failed-Job
  logs show localhost:8080; the script had no kubeconfig and the Job disables
  service links. Current retained pod logs are empty, so that historical log
  finding comes from the task's inspection, not a fresh log capture. The Job
  still reports exit 1. Real kubectl/TLS regression tests establish token and
  CA use and preflight ordering; the original script fails that regression.
  See [backup behavior](../gitea/backup-restore.md).
- **Policy comparison confirmed:** both admission replicas log errors in
  `require-requests-and-limits`. In
  [Kyverno v1.19.2 Equals](https://github.com/kyverno/kyverno/blob/v1.19.2/pkg/engine/variables/operator/equal.go),
  quantity keys cause quantity parsing of the empty-string comparison value.
  Boolean JMESPath equality avoids that conversion. Deployed-version CLI tests
  reproduce the original errors and verify their absence after repair while
  denying missing requests/limits. Existing policy exceptions remain intact.
- **Webhook timeout unresolved:** events record policyvalidate failures at the
  existing ten-second deadline, across multiple policies. Both replicas are
  Ready with zero restarts, approximately 38–50 millicores and 101–107 MiB
  working memory at sampling; modest usage does not rule out earlier stalls.
  Service 443 selects both ready EndpointSlice targets on port 9443, on two
  different nodes. All eight Cilium agents are Ready. Live inspection finds no
  Kyverno NetworkPolicies or Cilium policy CRs. Existing Cilium enable-policy
  is `never` and host firewall is false, matching the documented platform
  configuration; this PR does not change them. The policy-validation webhook
  remains `Fail`, timeout 10 seconds. One sampled API server exposes 1,163
  historical calling_webhook_error rejections for policy validation and 3,389
  rejected UPDATE latency samples averaging about 9.97 seconds. Those counters
  are cumulative and do not locate the failing boundary. Three server-side
  dry runs of the reported Longhorn policy succeeded in 0.090, 0.077 and 0.078
  seconds end to end. Flux alternates between successful reconciliations,
  admission timeouts and dependency failures. This proves intermittent reachability,
  not sustained recovery or causation by the quantity logs. No speculative
  timeout, replica, resource, network, failurePolicy or chart changes are made.
- **Cluster-health incident is real:** the failing bridge, recent backup Job
  and blocked Flux resources meet the existing health criteria. Detached
  Longhorn volumes with unknown robustness are already treated normally; keep
  that criterion. A completed Job with historical failed attempts is a confirmed
  false-positive case, now exempted only when its Complete condition is True.
  Active/failed Jobs with recent failed attempts remain failures. Failure
  identities are limited to workload/PVC/Flux names, replica counts, Ready/phase
  states and recent Job names. No events, env, Secret or ConfigMap contents enter
  incident diagnostics. Every Kubernetes status query now has a 15-second bound.

## Remaining webhook diagnostic

Do not change admission security based on the successful samples above. At the
next timeout, capture the request time, API-server node, webhook name and
AdmissionReview UID from that server's audit/trace logs. Correlate the UID with
both Kyverno replicas' request start/end logs and API discovery errors. Inspect
Cilium flow observations or packet captures for that control-plane node to
**both** current EndpointSlice IPs on TCP 9443, and compare Service 443 traffic
with direct endpoint traffic using the same TLS server name/CA. Read counters
per API-server node to avoid attributing a load-balanced sample to every node.

- No SYN/ACK, retransmissions or a drop before TLS identifies a network path.
- TLS succeeds and the UID enters Kyverno but completes late: capture bounded
  controller goroutine profiles, discovery timings and request latency while
  reproducing that policy dry run; inspect CPU throttling/OOM history too.
- Kyverno never sees the UID despite successful TLS: correlate API-server
  connection queues and webhook handler logs before proposing a change.
- A different failing webhook/resource in the same Flux interval is independent
  evidence, not proof that policyvalidate failed for the same reason.

This trace/flow correlation was **NOT RUN**; the available adapter does not
provide control-plane audit traces or packet capture. Read-only CLI fallback
provided live webhook/network configuration, metrics and safe dry runs. Do not
claim the timeout is permanently fixed. Keep chart **3.9.2**; CEL migration and
chart 3.10+ remain out of scope.

### On-demand timeout evidence snapshot

Use the workstation utility from a checkout with Python 3.10+ and `kubectl`.
Select an existing context explicitly; it uses only its current permissions.
It creates no Kubernetes resources and sends no admission probes, including
server-side dry runs. It does not execute the older diagnostic suggestions
above or change Kyverno configuration. Chart **3.9.2** stays pinned.

```bash
python3 scripts/kyverno-timeout-evidence.py \
  --context <existing-context> --since-minutes 30 > /tmp/kyverno-evidence.json
```

The report is sanitized JSON with `confirmed_observations`,
`possible_correlations`, `evidence_unavailable_or_limited`, per-source status
and collection times. Review it before attaching it to an incident. The script
never saves raw source data or emits raw errors/logs, credentials, environment,
AdmissionReview bodies or complete objects. Pod/resource identities are kept;
node/context identities, endpoint addresses and explicit request identifiers
are represented by stable hashes. The report is capped at **48 KiB**.

Defaults are the `kyverno` namespace, `kyverno-svc`, and Kustomizations/events
in `flux-system`. Override with `--namespace`, `--service`, and
`--flux-namespace` if needed; this is deliberately not a cluster-wide search.
Pods use the chart's `app.kubernetes.io/component=admission-controller` label
and logs use its `kyverno` container. Missing containers are reported. Current
metrics are selected for those pods and include their own timestamps; usage
does not establish earlier resource pressure. Pod UID hashes help distinguish
instances and correlate current EndpointSlice targets. Recreated/deleted pods
and historical endpoint state may be unavailable.

Collection uses direct read-only API GETs to avoid relying on unrelated API
discovery. Each request has an 8-second server timeout and a 10-second process
bound, within a default **90-second total I/O budget** (`--budget-seconds`,
10–180). Each list follows at most three 200-item pages, with explicit
truncation status; a partial list is not guaranteed to contain the newest
events. Each response is capped at 2 MiB. At most eight admission replicas are
sampled, with current and (if restarted) previous logs capped at 1000 lines /
128 KiB each. No follow/watch is used. Messages are examined for at most 200
lines. The evidence window is configurable from 1–120 minutes; the default
report retains at most 20 rows per evidence section (`--max-items`, 1–40).
Counts before report limits and any final byte-limit trimming are reported.
Exit **0** means the reads completed, **2** means a source was unavailable or a
list was truncated (the report is still usable), and **1** means the snapshot
could not be produced. Success does not mean the timeout is resolved or that
retained logs/events cover every request.

Flux timeout rows retain the webhook name, affected Kustomization, and resource
identity when present in that same message line. An unavailable resource name
is not inferred from nearby messages. Lines with multiple webhook names are
excluded as ambiguous. Aggregated events use their last-observed time; report
rows are evidence records, not independent request counts. Condition timestamps
are explicitly last-transition times and are not
used for time-only request correlation. A `timeout=10s` URL parameter is a
configured deadline, **not measured latency**. Controller evidence retains
classified errors, explicit request/audit UUID hashes and durations with
explicit units. Generic `uid` fields, nested request bodies and bare numeric
durations are excluded. Audit IDs, request IDs and admission UIDs are different
identifier types and are never treated as interchangeable.

Time overlap within 15 seconds is only a possible correlation, even if it
involves the same webhook. Conflicting explicit identifiers do not correlate.
An explicit shared identifier is stronger evidence of overlap but still does
not locate the failing boundary. The absence of a corresponding request from
bounded/rotated logs does **not** prove Kyverno never received it. Endpoints
are sampled before and after collection; changes during collection are shown
only when both lists are available and complete. Neither ready endpoints nor
an unchanged endpoint sample proves API-server-to-webhook connectivity or
endpoint health at an earlier timeout.

The utility has **no configured control-plane audit/trace or Cilium flow
source**. It reports those as unavailable, without requesting new RBAC, host
access or substitute probes. To establish root cause, an operator with existing
access would need a failing request's API-server identity, precise start/end
times, webhook and AdmissionReview UID; matching request start/end and operation
timings from both controller replicas; the endpoint state at that time; and
existing flow observations showing TCP/TLS progress or drops/retransmissions
from that API server to the chosen endpoint on TCP 9443. Retain only safe fields,
never request bodies. Those data distinguish a path/TLS failure from a request
that entered Kyverno and stalled, and can then identify a specific processing
operation. Until that boundary is observed, root cause remains unresolved and
[#2133](https://github.com/RovxBot/homelab-infra/issues/2133) stays open.

For optional API-server admission telemetry, explicitly add `--apiserver-metrics`.
Without it, `/metrics` is never requested. To request a bounded pair:

```bash
python3 scripts/kyverno-timeout-evidence.py \
  --context <existing-context> --budget-seconds 60 \
  --apiserver-metrics --metrics-interval-seconds 5 > /tmp/kyverno-evidence.json
```

This performs at most two read-only `kubectl get --raw /metrics` calls using the
same explicit context, current credentials and existing request/response limits.
The interval accepts 1–30 seconds and consumes the overall budget; insufficient
remaining time skips the second observation. No additional RBAC is requested.
Forbidden access, request failures, response overflow and missing metrics remain
explicitly unavailable; they are never converted into zero counters.

Only exact `name` labels `validate-policy.kyverno.svc` and
`validate.kyverno.svc-fail` are retained, from these Kubernetes 1.34 families:

| Family | Retained fields |
| --- | --- |
| `apiserver_admission_webhook_admission_duration_seconds` | `_bucket` and `_count`; `name`, `type`, `operation`, `rejected`, `le` on buckets |
| `apiserver_admission_webhook_rejection_count` | Counter; `name`, `type`, `operation`, `error_type`, `rejection_code` |
| `apiserver_admission_webhook_request_total` | Counter; `name`, `type`, `operation`, `rejected`, `code` |

Buckets are allowlisted to 0.005, 0.025, 0.1, 0.5, 1, 2.5, 10, 25 seconds and
`+Inf`. Histogram sums, arbitrary labels, unrelated webhooks, raw endpoint data,
exemplars and request bodies are omitted. Label values must match fixed enums or
bounded numeric codes. Malformed/duplicate samples and incomplete/inconsistent
histograms cannot support changes. Counter parsing preserves integer precision;
values above 2^53 are rejected. Family status distinguishes no exposed samples
from exposed families without matching webhook samples.

`apiserver_metrics.observations` contains **cumulative** values, client GET
start/finish times, and explicit Prometheus sample timestamps only when provided
(`sample_at` otherwise stays null). Client collection time is not an admission
request timestamp. `counter_changes` is separate and emits **no rates**. It
requires a single consistent `apiserver_id_hash` from local authentication,
authorization or encryption configuration-controller metrics in each response,
plus an unchanged `process_start_time_seconds`. Only a hash alias and the names
of identity-bearing metrics are retained; configuration hashes, other labels
and configuration-controller metric values are excluded. The recognized identity
families are `apiserver_{authentication,authorization,encryption}_config_controller_`
with `automatic_reloads_total`, `automatic_reload_last_timestamp_seconds`, or
`last_config_info` suffixes.

A context, endpoint URL, generic `instance` label, build version or process
start time alone cannot establish API-server identity. Missing/ambiguous identity
or process start in the first observation stops metrics sampling immediately.
Different server hashes, process restarts, negative deltas, changing/missing
series, invalid samples, inconsistent histogram deltas or stale/non-increasing
explicit sample timestamps invalidate the pair. Absent series are never assumed
zero. Two observations cannot exclude a counter reset that catches up between
them. Even a verified pair describes only the responding instance, not every
replica behind a load balancer; the script does not retry to obtain a matching
server.

Telemetry rows share `--max-items` and the existing 48 KiB report bound.
Rejection/request counters are retained before histogram detail when rows are
limited. Per-observation and change counts before row limits, all family/source
statuses and final byte trimming remain visible. The existing v1 report fields
are preserved; `apiserver_metrics` and `evidence_classification` are additive.
An unavailable requested pair or metric family yields exit 2 with a partial
report. The original pod, EndpointSlice, Flux event and controller-log evidence
continues within the same overall budget.

API-server histogram observations measure aggregate webhook invocation time;
they are not the duration of a particular failed AdmissionReview.
`calling_webhook_error` includes timeout and other invocation errors. These
metrics cannot separate connection/TLS/network transport failure, API-server
admission processing delay, and Kyverno handler processing delay. The report
keeps API-server observations, safely measured counter changes, historical Flux
records, possible correlations, and missing per-request evidence distinct.
Unchanged counters during a short window do not explain or refute an earlier
event. Use existing per-request API-server traces, matching controller timings
and transport observations described above to locate the failure boundary before
proposing a corrective configuration PR.

Metric definitions and identity semantics were checked against the
[Kubernetes 1.34 metrics reference](https://v1-34.docs.kubernetes.io/docs/reference/instrumentation/metrics/),
[v1.34.1 admission implementation](https://github.com/kubernetes/kubernetes/blob/v1.34.1/staging/src/k8s.io/apiserver/pkg/admission/metrics/metrics.go),
and [local configuration-controller identity implementation](https://github.com/kubernetes/kubernetes/blob/v1.34.1/staging/src/k8s.io/apiserver/pkg/server/options/authorizationconfig/metrics/metrics.go).

Fixture checks (also included in the existing script test discovery):

```bash
python3 -m unittest discover -s scripts/tests -p test_kyverno_timeout_evidence.py -v
```

One authorized read-only snapshot ran on **20 November 2026, 20:30:19–20:30:21
Adelaide time** (10:00:19–10:00:21 UTC), covering the preceding 30 minutes.
All configured reads succeeded without list/report truncation; the sanitized
report was 7,151 bytes. One retained event last observed at **20:19:40 Adelaide**
(09:49:40 UTC) recorded `validate-policy.kyverno.svc` timing out with the existing
10-second configured deadline while `kyverno-policies` reconciled
`ClusterPolicy/platform-namespace-bootstrap`. Both admission replicas were Ready
with zero restarts on distinct nodes, using approximately 28/50 millicores and
100 MiB each when sampled. Service 443 selected both; its two ready, serving,
non-terminating TCP 9443 endpoints were unchanged across the two samples.
The bounded controller logs yielded no classified errors, explicit request
identifiers or measured operation latency, so no controller correlation could
be established. This does not prove the timed-out request was absent. Audit
traces and Cilium flows were unavailable to the utility. The snapshot does not
locate the failing boundary, prove connectivity or establish a distinct defect.

The API-server extension received one explicitly bounded live attempt on
**20 November 2026, 22:46:49.753690–22:46:50.287904 Adelaide time**
(12:16:49.753690–12:16:50.287904 UTC), using the workstation's existing selected
context (reported as `context-f7cfc78e4428`), a 60-second budget, a 120-minute
historical window, `--max-items 40`, and a requested five-second pair interval.
The first `/metrics` GET ran from **12:16:49.753865 to 12:16:49.944907 UTC** and
returned the sanitized transport reason **`request-failed`**. This is not a
verified Forbidden/RBAC error: stderr was intentionally suppressed and the
failure boundary was not identified. The second metrics GET and interval wait
were skipped. The existing pod, Service, Flux event/Kustomization,
EndpointSlice and pod-metrics GETs also returned `request-failed`; no pod logs
could be selected. Exit status was **2**, with a **6,585-byte** sanitized report,
no list/report truncation, no API-server metric samples, no established serving
instance, no deltas, and no new per-request or Flux evidence. No requests were
retried, no admission probes were sent, and no permissions or resources changed.
The previously supplied **12:03:03 UTC** Flux timeout is historical evidence,
not an observation reverified by this attempt. The API-server/transport/handler
boundary remains unresolved; a corrective cluster PR is not justified yet.
Keep #2133 and #2150 open, and keep Kubescape suspended.

Flag and endpoint semantics follow the Kubernetes references for
[bounded pod logs](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_logs/),
[read-only raw GETs](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_get/),
and [EndpointSlice conditions](https://kubernetes.io/docs/concepts/services-networking/endpoint-slices/).

## Validation before rollout

Run all local fast checks before relying on PR CI:

```bash
python3 -m unittest discover -s ops/gitea/tests -v
python3 -m unittest discover -s scripts/tests -v
python3 -m unittest discover -s scripts/ci/tests -v
python3 scripts/ci/test_platform_resources.py --kyverno /path/to/kyverno-1.19.2
python3 scripts/ci/kyverno_gate.py check --repo-root . --cluster-root clusters/home \
  --baseline .github/kyverno-baseline.yaml --kyverno-bin /path/to/kyverno-1.15.2 \
  --work-dir /tmp/kyverno-gate
```

The new TLS tests need kubectl and OpenSSL; a missing binary is reported as a
skip, not runtime success. CI's manifest job runs the deployed-version CLI
resource cases as well as its existing 1.15.2 baseline CLI. A trial replacing
that baseline CLI with 1.19.2 panicked on duplicate Namespace inputs in the
existing gate; keeping the baseline pin avoids unrelated gate refactoring.
The dedicated 1.19.2 tests cover all five workload template kinds, normal/init
containers, valid integral/decimal/milli quantities, all missing resource fields,
empty quantities, the Longhorn exception scope and Home Assistant's unrelated
exception. No real credentials are used. Local Restic 0.19.1 was also checked
against a temporary local repository: `list locks --json` returns **empty
output** when no locks exist (not an empty JSON array).

Kustomize must render every source root. Validate standard resource schemas,
Flux paths/dependencies, Secret references and unchanged SOPS ciphertext. The
schema tool's missing-CRD-schema skips must be reported explicitly. The Python
helper container smoke test needs a Docker daemon; no daemon is available in
the preparation environment, so that test is **UNAVAILABLE** locally and left
to required CI. No application images or digests change and the toolchain-image
workflow's path filter excludes these script/manifest edits.

Local results: **PASS** — 38 Gitea tests (including TLS fixtures), 123 script
tests, 31 CI-helper tests, 37 existing helper smoke cases on the host Python,
123 Kyverno 1.19.2 resource cases, the existing policy baseline and both runner
policy pools. All 51 Kustomize roots render. Standard Kubernetes 1.34 schemas
validate after omitting only SOPS envelope metadata in temporary validation
copies (ciphertext is never decrypted): 491 valid resources, zero invalid or
errors, and 128 CRD-schema skips across 619 rendered objects. Secret
references resolve for the Flux entrypoint and affected source roots with the
parent-provided Gatus Secret included. Images, PVC references and Secret
references in changed manifests are structurally unchanged. Bash syntax,
ShellCheck, actionlint, workflow yamllint and whitespace checks pass.

## Authorized production validation

The operator must authorize the rollout and maintenance window separately.
The Koito Recreate rollout briefly interrupts scrobbling. Updating pod-report
and the issue-sync CronJob does not take Gitea down. Gitea's script is delivered
by ConfigMap; its next scheduled backup uses the repaired version after merge.
Coordinate that schedule with the approved outage window. If needed, use a
separately reviewed suspend change before merging; do not silently suspend it.

1. After approved GitOps rollout, verify `apps-koito`, `apps-gitea`, infrastructure
   and kyverno-policies Ready against the merged revision. Check all dependent
   Kustomizations, including Home Assistant and GPU Operator. Record Ready
   conditions and new webhook errors across several reconciliation intervals.
2. Verify bridge 1/1 Ready, no new restarts, `/api/health` HTTP 200, writable
   existing config/data/log directories, retained Last.fm authorization and a
   new scrobble arriving at Koito. Preserve the PVC and existing OAuth flow;
   reauthorization is not part of this repair.
3. Before the **explicitly authorized cold backup**, verify no active backup or
   maintenance operation, all PVCs/Longhorn attachments healthy, PostgreSQL
   healthy and expected replica counts 1/1/0. Check that preflight has not
   changed replicas. The capture may take Gitea down for up to twenty minutes,
   plus bounded shutdown/cleanup and scheduling time; the overall Job deadline
   is one hour. Do not run a second Job if one is already scheduled/running.
   Only after authorization, a single controlled Job can be created with:

   ```bash
   kubectl -n gitea create job --from=cronjob/gitea-cold-backup \
     gitea-backup-verify-$(date -u +%Y%m%d%H%M%S)
   ```

4. After Job completion and Pod exit, confirm Gitea and protection have one
   **available** replica each, mirror remains at zero, PostgreSQL and attached
   volumes are healthy, and repository/recovery/runner checks pass. Record the
   new tagged Restic snapshot and successful `restic check` output through the
   existing secured toolchain. Check success/failure markers without exposing
   archive credentials. `/backupz` must return 200; do not edit markers manually.
5. Verify fresh `/health.json` categories and diagnostic identities. An existing
   cluster incident receives one managed body block; unchanged detail creates
   no additional update, and no duplicate issues are created. The sync never
   creates issues (Gatus owns creation). It preserves existing creator filtering
   and only closes each exact title using that endpoint's Gatus evidence.
   Non-cluster endpoints still require two recent successes. Cluster closure
   requires 20 consecutive successful samples spanning at least 19 minutes,
   continuous one-minute sampling and a fresh direct health payload with all six
   checks OK. Unavailable, stale, unknown or contradictory evidence leaves that
   incident open. See [Gatus recovery policy](gatus-recovery.md) for exact gates,
   configuration and the pinned-version validation procedure.
6. Allow Gatus to close the original three incidents normally. Do not delete
   failed Jobs or close issues to manufacture acceptance. A failed backup
   marker survives until a successful verified capture even if a Job ages out.

## Failure handling and rollback

Revert the affected logical commit through a reviewed GitOps PR and verify the
reconciled revision. Reverting Koito's repair restores the known broken command,
so prefer correcting any separately evidenced runtime problem while retaining
the upstream entrypoint. Never delete or replace application PVCs.

If a backup is active, stop competing changes. On TERM, capture must terminate
before service restoration. If API recovery fails, do not start another backup:
first prove no capture/Pod remains, inspect storage attachments, then restore
`gitea` and `gitea-protection` to one and leave `gitea-mirror` at zero using the
existing separately authorized recovery procedure. Preserve the failure marker,
inspect stale shared/Restic locks, and verify application readiness after RWO
release. Persistent API loss, node loss and SIGKILL require operator recovery;
shell traps cannot guarantee availability under those conditions.

Rollback of the diagnostic changes restores the former incident bodies/behavior
for future sync runs; it does not prove recovery or justify closing an incident.
Kyverno chart, failure policies, exceptions and security rules remain unchanged
apart from the typed comparison correction. Reverting that correction reintroduces
quantity errors; no CEL migration is needed for this patch.
