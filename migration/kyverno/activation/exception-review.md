# Consolidated platform-owner exception decisions

**Review proposal only. No exception is renewed, revoked or widened.** All six
legacy and CEL scopes and dates remain byte-for-byte unchanged. RovxBot must
approve any remediation, scope reduction or renewal in one consolidated follow-up
change. Home Assistant remains unchanged, including its 2027-02-28 expiry.

The 21 November 2026 read-only audit evaluated the original legacy rules with
**no exceptions supplied**, using Kyverno CLI 1.19.2. Inputs were current rendered
Flux workload manifests, exact chart versions/committed values (Longhorn 1.13.0,
GPU Operator v26.7.2), and complete live Deployment/StatefulSet/DaemonSet/Job/
CronJob/Pod inventories in the six affected namespaces. Both live HelmRelease
versions, values and post-renderers match Git; the
[live comparison](../evidence/task18/live-chart-comparison.json) records this.
The [machine-readable audit](../evidence/task18/exception-audit.json) lists every
violating identity and rule, source counts and private-input hashes. Raw live
specs remain private. CREATE evaluation shows what a current spec would violate
on fresh admission; unchanged existing-violation UPDATE behavior is preserved.

| Expired exception | Current workloads still needing it / violated rules | Narrowing proposal for owner review | Remediation and proposed bounded renewal |
| --- | --- | --- | --- |
| `chart-managed-workloads` (2026-08-31) | Longhorn manager, CSI plugin, attacher/provisioner/resizer/snapshotter, driver deployer, global manager, UI, engine-image DaemonSet, weekly system-backup CronJob/Job. Missing owner/budgets; manager/UI/CSI also lack probes. All seven rendered chart controller/hook templates violate owner/budget rules; manager/UI also probes. The repository retention CronJob passes these rules. | Replace namespace-wide matching with rule-specific controller names, bounded engine-image/system-backup names, retaining all three chart hook Job names. Pods do not match these original controller rules. Require tests of operator-generated templates and future hooks before narrowing. | Add owner/budgets/probes using supported values or reviewed post-renderers, including operator-generated CSI/engine/backup settings. A namespace manifest fix cannot eliminate this exception. Propose renewal to **2026-12-21** while these templates are qualified. |
| `gpu-operator-workloads` (2026-10-31) | Eight live controllers: operator, NFD master/gc/worker, GPU feature discovery, device plugin, MPS control daemon, validator. Owner, budgets, digest pinning and/or hardening fail. Seven chart templates include cleanup, NFD-prune and CRD-upgrade hooks with the same violations. No sampled host-network/probe violations. | Propose removing host-network and probe exemptions after owner acceptance of rendered hooks and generated DaemonSets. Bound names to the listed controllers/hooks and generated hardware validation workloads. Current samples cannot guarantee future chart behavior. | NFD master/gc/worker need RuntimeDefault seccomp; add owner/budgets and pinned images. Privileged hardware validation requires separate qualification. Propose renewal to **2026-12-21**, reducing rules only after reviewed tests. |
| `mutable-image-tags` (2026-08-31) | Digest rule: Longhorn chart hooks/controllers and live manager/CSI/engine/system backup; WotLK authserver/worldserver and both extraction CronJobs; retained one-off realm-upsert Job. Current cloudflared, registration, homepage-assets, pod-report and wireguard controller specs pass this rule. | Remove passing named branches and wireguard scope after owner review. Replace broad WotLK matching with four controller names plus any supported one-off recreation path; bound dynamic Longhorn names/hooks. Completed retained Jobs are distinct from scheduled work. | Pin the four WotLK controller images and chart/manager settings to tested digests, retaining Renovate updates. Do not edit completed Jobs to manufacture a clean audit. Propose renewal to **2026-12-21** for required paths. |
| `privileged-namespaces` (2026-08-31) | Hardening: Longhorn controllers/hooks/CSI/engine/system backup, wireguard-client, WotLK registration/auth/world/MariaDB, six CronJobs and retained Jobs. 25 live identities fail. All inspected templates have hostNetwork=false; none violates that rule. | Propose dropping the host-network exemption after owner confirmation of maintenance/recovery templates. Bound hardening to named controllers/generated families. Missing standard securityContext fields in WotLK jobs do not prove a need for broad privilege. | Add non-root identity, no privilege escalation, drop ALL and RuntimeDefault to simple SQL/kubectl helpers first; test image UID/filesystem behavior. WireGuard/Longhorn host access requires runtime qualification. Propose renewal to **2026-12-21** for remaining hardening scope. |
| `wotlk-latest-tag` (2026-08-31) | Latest-tag rule: repository/live CronJob wotlk-weekly-restart and retained Job wotlk-weekly-restart-29915340 use latest. | Current CronJob name and bounded Job family are already narrow. Keep namespaces/kinds unchanged. Retained completed Jobs do not justify widening future scope. | Replace the kubectl helper reference with a tested version/digest, verify new CronJob/Job admission, then revoke after owner approval. Direct remediation is feasible. If not completed first, propose renewal to **2026-12-05**. |

Dates are proposed review deadlines, not applied renewals or automatic revocations.
Expiry metadata retains its original behavior. Record RovxBot's action,
justification, approved scope/date and tracking reference for each row in one
consolidated decision change. Keep #2133 open. Rerun the audit and admission checks
on final workloads. Neither zero reports nor disappearing completed Jobs establish
safe revocation. The Home Assistant exception needs no change in this review.
