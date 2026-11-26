# Kubescape and security operations

This is the current operational entry point for the GitOps-managed security project
([#2132](https://github.com/RovxBot/homelab-infra/issues/2132)).
Historical manual qualification records are retained in the
[posture baseline](production-baseline.md), [image assessment](image-assessment.md),
[database maintenance](database-maintenance.md) and
[recurring assessment runbook](recurring/README.md). Historical limits and
activation states in those records are not current configuration.

## Architecture and ownership

| Layer | Owner | Scope |
| --- | --- | --- |
| Prevention | PSA and Kyverno CEL | Admission policy, reviewed exceptions and policy assurance |
| Posture | Kubescape NSA | 49 explicitly configured workload identities; host/cloud controls not fully evaluated |
| Vulnerability | Kubevuln and recurring assessment runner | 12 reviewed container identities; direct immutable image and workload attribution |
| Runtime | Native node-agent and authenticated HTTPS receiver | Eight Talos nodes; explicit namespaces and workload bindings, not every workload |
| Confidential incident lifecycle | Private Gitea publisher, policy publisher and admission observer | Reviewed runtime/CVE/policy findings only; durable write fences |
| Operational health | Gatus, pod-report and public health issue sync | Aggregate control health only; never raw findings or private incident links |
| Remediation | Reviewed Git PRs, CI and Flux | Human-reviewed changes; no automated workload mutation |

The chart remains pinned to 1.42.0. The exact Git revision, controller readiness,
direct report and loaded database identity are authoritative, not this document.
Node-agent readiness is not proof of lossless event delivery. Cilium policy
enforcement remains intentionally disabled; no new monitoring stack or SaaS
backend is required.

## Assessment limits and schedules

Chart scan schedulers and automatic operator image routing remain suspended.
The separate recurring Jobs own scans. UTC schedules: vulnerability 01:13,
10:13, 13:13, 19:13; posture 20:13; inventory refresh at :07 and :37.
One worker, one outstanding request and at most four sequential image
requests per window. The scanner-only image cap is 1.5 GiB, SBOM cap 32 MiB,
engine memory/ephemeral-storage limits 3 GiB each, sampled working-set gate
2 GiB. Shared chart image and SBOM defaults remain 512 MiB and 20 MiB.
The Grype database PVC is 10 GiB and report storage PVC 5 GiB; both retained.
Keep node pressure, fresh metrics, CI workload, storage, DB identity and
single-flight gates. Do not bypass a fence or delete archives to get a green check.

Vulnerability evidence is fresh for 72 hours. The immutable DB discovery
workflow proposes one reviewed update weekly at Sunday 18:19 UTC (Monday
04:49 ACDT / 03:49 ACST). The current recurring contract treats DB age up to
seven days as fresh, then grace until fourteen days, after which scans are
blocked. Independently verify loaded database metadata and checksum; a Ready
engine is not evidence of a fresh database.

The private advisory projection is bounded to 200 distinct High/Critical CVE
IDs per workload, 240 KiB producer / 256 KiB consumer read boundary.
Optional package details and recovery proof have separate bounds. Missing,
ambiguous, failed, stale or overflowing evidence is unknown, never clean.
The stable unready bridge exception is restricted to the reviewed running
image identity; application readiness and recovery proof remain strict.

## Operator checks

```sh
kubectl -n flux-system get kustomizations
kubectl -n kubescape get helmrelease kubescape
kubectl -n kubescape get daemonset node-agent
kubectl -n kubescape get deployments,pvc
kubectl -n kubescape get jobs --sort-by=.metadata.creationTimestamp
kubectl -n monitoring get jobs --sort-by=.metadata.creationTimestamp
```

Inspect only aggregate, fixed-code diagnostics from the assessment refresh
and publishers. `privateCveReasons` distinguishes assessment failures,
skips, stale/missing evidence and projection overflow from qualified
reporting. `qualifiedWorkloads` must equal `expectedWorkloads` for full
reviewed-cohort CVE publication health. A successful refresh is not proof
of full coverage. Native, CVE and receiver-capacity publisher stages are
independent; the overall Job can fail when only CVE coverage is incomplete.
Use the recurring runbook to investigate before considering changes to caps.

## Private findings and remediation

The publisher reads the authenticated private receiver projection and exact
named private CVE ConfigMap. The dedicated private Gitea writer is separate
from public health issue credentials. Do not expose findings, raw events,
package data, issue URLs or credentials in public GitHub issues or Actions.
Positive updates are deduplicated; ambiguous remote writes remain held
for manual reconciliation. Retain SQLite and all write-intent records.

The hourly GitHub remediation discovery workflow reads main and reviewed
open non-draft PRs without executing PR code. Its bounded artifact contains
only public source-derived candidate image metadata. Optional private
correlation requires the separate `monitoring/security-remediation-github-read`
Secret, key `token`, with read-only Actions permission on this repository.
It must not reuse the private Gitea writer or the public issue writer.
See [activation and completion gates](../../ops/security-receiver/OPERATIONAL-HANDOFF.md).
A matching PR is not proof its replacement image is clean.

Closure and reopening are hard disabled. The shadow recovery proof requires
newer independent exact running-image assessments and absence of the tracked
CVEs at all severities, with fresh, non-regressing DB identity and complete
scope. Gitea does not offer compare-and-swap issue state changes; preserve
manual disposition and human comments. Never mark an issue resolved simply
because a scan failed, a report disappeared or an alert stopped.

## Runtime retirement and rollback

Native transport has recorded a genuine R1000 and receiver restart
persistence; see [eight-node rollout](../../ops/security-receiver/EIGHT-NODE-ROLLOUT.md).
The disposable trigger has been removed from desired state by #2284; the
restricted namespace and reusable fixture remain. Verify its actual removal
and native transport before retiring the old collector.

The legacy collector and its PVC are removed from desired state by #2286.
After Flux reconciles and no Pod references the old prune-protected claim,
explicitly delete only the obsolete `security-collector-state` PVC as described
in the handoff checklist. This permanently removes its historical data; no
dedicated legacy backup is wanted. Preserve the active native receiver and
publisher claims. Active security state uses the existing weekly Longhorn
backup policy. Never delete active claims or infer delivery coverage from
agent readiness.
