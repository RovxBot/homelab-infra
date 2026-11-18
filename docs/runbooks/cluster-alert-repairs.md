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
   and only closes each exact title after the newest two Gatus samples succeed
   recently. Cluster closure additionally requires a fresh direct health payload
   with all six checks OK. Unavailable/stale health leaves that incident open.
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
