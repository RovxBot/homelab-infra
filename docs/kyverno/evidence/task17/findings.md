# Task 17 findings

**Historical receipt — 21 November 2026.** The findings and decisions below
record the failed generator qualification at that time. They are not current
production acceptance evidence. The later accepted Flux-owned design is recorded
in [ADR 0003](../../../../docs/decisions/0003-flux-namespace-defaults.md).

The 109 raw captures (5,522,522 bytes) have been retired from the checkout under
cleanup [#2293](https://github.com/RovxBot/homelab-infra/issues/2293), following the
[accepted dependency audit](https://github.com/RovxBot/homelab-infra/issues/2292#issuecomment-7182975729).
The seven direct Task 17 receipts remain here. [files.json](files.json) preserves
the original run/implementation references and all 115 original file hashes.
Its `historical_files_sha256` keys resolve through `archive.file_url_template`
to exact files at immutable commit
[`4468df762841f1341d1e1061f25f0e0b29da67e0`](https://github.com/RovxBot/homelab-infra/tree/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/evidence/task17).
Those hashes describe that snapshot, including the original version of this
receipt; they do not validate today's checkout or authorize evidence reuse.

| Retired captures at immutable source | Files | Bytes |
| --- | ---: | ---: |
| [adopted](https://github.com/RovxBot/homelab-infra/tree/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/evidence/task17/adopted) | 26 | 1,361,963 |
| [empty-upgraded](https://github.com/RovxBot/homelab-infra/tree/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/evidence/task17/empty-upgraded) | 24 | 970,312 |
| [fresh-target](https://github.com/RovxBot/homelab-infra/tree/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/evidence/task17/fresh-target) | 24 | 919,632 |
| [mutation](https://github.com/RovxBot/homelab-infra/tree/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/evidence/task17/mutation) | 5 | 219,263 |
| [upgraded](https://github.com/RovxBot/homelab-infra/tree/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/evidence/task17/upgraded) | 30 | 2,051,352 |

## Recorded findings and decisions

**Decision: stop before production activation.** The preferred empty-state
mechanics work, but fresh CEL generator restart recovery fails on both releases.
This is a general synchronization reliability blocker, not an adoption-only
migration requirement. No timeout increase or production workaround is qualified.

The main isolated run is
[44882022959](https://github.com/RovxBot/homelab-infra/actions/runs/44882022959),
tested implementation `8dc63d5b12ebeb06a045cec61cda90a37887fe8f`.
The historical index in [files.json](files.json) identifies archived snapshots,
watches, logs, traffic samples, admission objects and teardown records at the
immutable source above. Full run artifacts also contain trust
verification and remaining controller logs. Production raw inventories remain
private; only counts, timestamps and hashes are committed.

| Case | Observed result | Qualification |
| --- | --- | --- |
| Fresh CEL on 1.19.2 after empty cutover | Complete outputs generated; after restart quota stayed at injected `pods: "21"` for the unchanged 75-second bound. | failed |
| Fresh CEL installed directly on 1.20.0 | Same failure, without any legacy resources or adoption history. | failed |
| CEL adopting legacy resources on 1.19.2, upgraded to 1.20.0 | Handoff retained three UIDs and repaired LimitRange drift. Quota restart recovery failed after upgrade. | failed |
| CEL created fresh on 1.19.2, then upgraded | Targeted run 44883383400 retained the failed baseline, repaired drift through an explicit Namespace event, then upgraded. Restart recovery again left quota at 21 on 1.20.0. | failed |

The empty cutover used real Flux commits, a native fail-closed admission freeze
(probed for both Namespace CREATE and UPDATE), a separately observed legacy
orphan configuration, legacy prune/absence, controller restart, repeated complete
zero inventories and then CEL activation/thaw. A new eligible Namespace received
the exact LimitRange, ResourceQuota and default-deny NetworkPolicy. **This is a
passed cutover step, not full migration qualification.** Trigger/deletion
lifecycle and rollback were not reached because the restart gate failed first.
Any nonempty production inventory stops this path; discovery is not permission
for automatic adoption or deletion.

The targeted [run 44883383400](https://github.com/RovxBot/homelab-infra/actions/runs/44883383400), at `bde3230fd9619bf08727231cd71bcad9b931beae`, completed the fresh-upgraded distinction. The explicit trigger repaired baseline quota in under two seconds; it did not erase the preceding restart failure. Report RBAC was verified on both releases: the service account can list both report kinds and is forbidden to list Secrets or either exception API. Its continuous traffic probe also spans the upgrade. No further controller reruns are planned.

All three outputs were **present** in every post-failure snapshot, with their
original UIDs. The quota's only intended drift remained `20 → 21`. Policy status
was Ready, background Deployment was Ready, and the final UpdateRequest list was
empty. Continuous watches ran across restart (and across upgrade in the adopted
case) without early exits. Cleanup messages about other nonmatching namespaces
are not evidence of deletion; the direct object/watch evidence establishes the
actual outcome here.

The target startup logs and API events identify the missed event boundary:

- The fresh 1.20 quota was correct at resourceVersion `1585` before drift.
- The independent API watch captured modifications `1661` and `1662`, both with
  `pods: "21"`.
- The restarted controller logged `start watcher ... resourceVersion=1662` at
  07:54:19 UTC, after the drifted object was already current.
- At exact target source `f666af06e9bd310c7e730f1523c0430e90371a89`,
  [`context.go`](https://github.com/kyverno/kyverno/blob/f666af06e9bd310c7e730f1523c0430e90371a89/pkg/cel/libs/context.go#L359)
  returns the **existing** object during cache restoration instead of writing
  the rendered desired object. The
  [watch manager](https://github.com/kyverno/kyverno/blob/f666af06e9bd310c7e730f1523c0430e90371a89/pkg/background/gpol/dynamic_watcher.go#L533)
  records that object's hash/data and starts watching at its resourceVersion.

This source-supported explanation accounts for the failure: drift admitted
between Pod readiness and watcher hydration becomes the cache baseline, so
waiting longer for the same watch event does not correct it. Pod rollout
readiness and GeneratingPolicy Ready do not establish completed watcher recovery.
This does not prove every later drift is unrecoverable or that an arbitrary
longer resync will never occur. It does establish an unqualified restart window;
waiting past 75 seconds or injecting a Namespace event would change the test,
not qualify automatic recovery. No upstream issue or PR was opened or tagged.

Calico v3.31.0 was installed with kind's default CNI disabled. Before enrollment,
protected/control endpoints were reachable. After default-deny generation,
sampled TCP ingress and egress were denied while the independent control flow
worked. Each fresh case retained 20 successful negative/control samples across
its observed restart window. These are actual enforcing-CNI observations, not an
object-existence inference; they do not prove unobserved intervals or the
unreached deletion/rollback lifecycle.

Longhorn mutation is **verified**: 21 fixtures × CREATE/UPDATE = 42 admission cases
for legacy 1.19.2, CEL 1.19.2 and CEL 1.20.0. All three returned identical allowed
outcomes and complete server-defaulted PodSpecs. Six supported image references,
namespace/name, registrar identity and index zero, shell condition, untouched
postStart/sidecar hooks, negative boundaries, missing/null fields and malformed
fields were exercised. All ten malformed admissions per engine failed native
schema validation; they were not webhook failures. The only intended mutation
removes `/spec/template/spec/containers/0/lifecycle/preStop`.

Report automation now canonicalizes the actual recorded legacy/CEL validation
formats (including absent rule on 1.19 and `validation` on 1.20), namespaced
runner identities and the Longhorn mutation. The combined generator uses a
correlation family to find existing legacy incidents without claiming a CEL pass
proves recovery of every old rule. Duplicate reports remain one incident;
exception skips, young failures, missing/stale/future passes and API collection
failures cannot close it. Closure requires a fresh explicit pass for the same
canonical finding, after issue creation, with no overlapping failure/unknown.
Disappearing reports or deleted resources require review rather than automatic
incident closure. No live issue automation was executed during this task.

The new reusable CI runs the exact proposed chart/application pair, verified
release artifacts and dynamically matched source. Compatible updates have no
fixed release ceiling; changed artifacts or a new application major require
review. Vendor Kubernetes support is checked against the upstream release table;
unknown/unsupported combinations fail. Old validation evidence can be reused
only for the exact pinned contract/target. Mandatory generator, mutation and
validation subsets cannot silently skip to a passing full qualification.

The version-matched 1.19.2 baseline gate exposed a duplicate-Namespace panic
in the CLI fake client when multiple Flux renders were combined. CI now deduplicates
identical documents and evaluates differing definitions in separate batches,
preserving findings from every variant. The actual baseline passes with zero
current findings and zero accepted exceptions; the baseline file was not refreshed.
The suspended Kubescape preflight was also tested against both CLIs: 27 legacy
passes and 28 CEL passes, with zero failures/errors/skips/warnings. The additional
CEL pass is the explicit non-service probe guard on the Job; no admission decision
changed. This is offline compatibility validation, not Kubescape activation.

Three practical operator decisions remain:

1. Keep the generator migration blocked pending a supported recovery correction,
   or authorize review of the Flux-owned alternative below. Adoption is optional
   and does not solve this general failure.
2. Decide each of the five expired exceptions individually using the
   [scope/consequence table](../../activation/exception-review.md). All six dates
   and scopes remain intact in this draft.
3. After engineering gates pass, review each activation patch, durable private
   backups and the actually rehearsed rollback/storage procedure. Neither this
   draft nor a rendered patch authorizes production reconciliation.

A Flux-owned alternative is credible because the refreshed production inventory
has no enrolled namespaces or outputs. A reusable Git bundle could declare the
Namespace plus the exact three defaults; workload Kustomizations would depend on
that bundle. Flux would own drift correction and prune, removing an additional
in-memory generator cache/restart dependency. However this changes the bootstrap
interface: a label alone would no longer provision defaults. It also changes
reconciliation latency and label-removal/policy-deletion semantics. A review must
choose the Git onboarding contract, reject partial bundles, preserve all default
values, specify safe pruning/namespace deletion and qualify drift/restart/rollback
and CNI behavior. No replacement is activated, and existing bootstrap
functionality has not been removed.
