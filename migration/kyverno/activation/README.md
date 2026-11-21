# Production activation sequence

Task 20 authorizes staged execution, routine merge decisions and live acceptance
without additional owner confirmation between passing stages. These sequential
Git patches still require CI and the acceptance gates below. Qualification and immutable
inputs/results are recorded in [Task 18 evidence](../evidence/task18/qualification.json).
Historical generator failures remain in [Task 17](../evidence/task17/findings.md);
there is no further generator debugging, adoption, or CEL generator activation.

RovxBot is the platform owner for every production acceptance gate. Export fresh
private backups of policies, exceptions, reports, CRDs, Helm values, Flux
inventories and relevant workloads to approved durable storage. Record checksums,
restore access and tested restore manifests without server-managed metadata.
Never replay queued UpdateRequests. Preserve original diagnostic metadata privately.

Exact merge/review order:

1. Review and merge **#2159** first (inventory/assessment, head
   `117ad3913928195a5365011d96f18fb241e639d9`). Its generator proposals are historical;
   Task 18's decision supersedes them. No production policy is activated by #2159.
2. Retarget **#2160** to main after #2159, verify the final diff and checks, and
   review the active report-automation and CI changes before merging. This draft
   changes incident identity/closure handling and CI behavior; it is **not a
   documentation-only merge**. Namespace bases and activation patches remain
   inert; active chart remains 3.9.2 and Renovate cap remains in force. Accept the
   new reporter's actual legacy/CEL fixture tests and least-privilege RBAC first.
3. Review one consolidated exception decision change based on
   [the five-row table](exception-review.md). Do not automatically alter any
   date/scope. If decisions change policy inputs, rerun the affected contracts.
4. Prepare one production PR per numbered patch below, in the exact table order.
   Require applied Flux revision and the listed live gate between PRs. **Do not
   merge all patches together.** These future PRs have no assigned numbers yet;
   the filenames are their exact change boundaries. Regenerate against the then
   current main and recheck each diff. Keep #2158 independent and unmerged.

`python3 migration/kyverno/activation/prepare.py` regenerates ten patches without
contacting Kubernetes. They apply/render sequentially in a disposable filesystem
copy. Rendering is separate from the real Flux/Helm lifecycle evidence.

| Production PR / patch | Change | Required acceptance before proceeding |
| --- | --- | --- |
| `00-protect-runner-namespaces-and-admission-drain` | Add prune protection before moving runner Namespaces; configure the chart-supported admission preStop sleep of 10 seconds for endpoint drain. | Observe app/Helm revisions, all three Namespace UIDs/prune annotations and two ready admission replicas with the drain hook. Accept the initial preparation rollout before continuous upgrade probes. |
| `01-overlap-cel-and-reporting` | Move runner Namespaces to infrastructure, preserving labels/UIDs; add CEL validations, all six exceptions, native exception accountability, 15 namespaced runner policies, Longhorn mutation and reporting/RBAC alongside legacy enforcement. | Infrastructure/policies/apps Ready at expected revisions and unchanged Namespace UIDs. Real positive/negative runner and ephemeral-container tests in both pools; exception metadata/accountability; Longhorn mutation; representative Enforce/Audit/report inputs. Approved exception decisions applied and retested. No new generator RBAC. |
| `02a-freeze` | Activate native fail-closed bootstrap enrollment freeze. | CREATE and UPDATE adding enrollment must be denied; unrelated Namespace operations allowed. Inspect type-check warnings/binding and prove cache propagation through **every serving API server**. Collect a fresh complete inventory under the proven freeze; stop on any enrolled Namespace, matching output candidate or UR, including completed URs. |
| `02b-orphan-legacy-before-retirement` | Set orphanDownstreamOnPolicyDelete=true on all three legacy rules. | Observe the live rule settings and applied Flux revision, then repeat complete zero inventory. Do not strip labels, adopt or delete outputs to force zero. |
| `02c-retire-empty-legacy-generator` | Flux prunes only the unused legacy bootstrap ClusterPolicy. | Confirm explicit policy absence, zero downstreams and zero UR/cleanup work; restart legacy background controller, verify readiness and repeat empty inventory. Keep freeze. Stop on any nonempty/unknown result. |
| `02d-qualify-flux-onboarding-contract` | Add reviewed native enrollment/default guards while freeze stays active. No replacement generator. | Native policies/bindings ready with no type warnings; CI complete-bundle/dependency checks accepted; Flux controller identity/inventory label assumptions reviewed; isolated lifecycle/restart/rollback evidence accepted. Prove the permanent binding independently with a denied contract-only malformed dry run that the freeze does not match, through every serving API server. Freeze still denies enrollment. |
| `02e-thaw` | Remove only the temporary freeze after the new contract is qualified. | Wait for a complete Flux-identity dry run to succeed after freeze removal, then confirm label-only CREATE/UPDATE still denied by permanent guards. First explicitly approved bundle reconciles four exact, Flux-owned objects; dependent workloads remain held until success. No existing namespace is enrolled automatically. |
| `03-retire-legacy-after-acceptance` | Prune old validation/mutation/governance/exceptions and both old runner policies only after every CEL replacement is live accepted. | CEL/native security and runner probes remain continuously valid. All legacy ClusterPolicy, Policy, CleanupPolicy, ClusterCleanupPolicy and PolicyException lists empty; URs empty. Observe separate policy and app Flux revisions, archive report history. |
| `04-qualified-upgrade` | Upgrade chart 3.10.0/app 1.20.0 with CreateReplace, original legacy-policy check enabled, acknowledgement=false, ghcr.io hook. | Legacy pruning already observed before Helm reconciliation. Exact artifact pair/signatures/provenance accepted; chart safety/migration hooks succeeded; storedVersions match the verified chart and served APIs are verified with retained object UIDs; two admission replicas, other controllers/webhooks and Flux healthy; admission/mutation/report/runner probes pass. |
| `05-release-renovate-cap` | Remove the temporary Renovate cap after successful live upgrade acceptance. | Separate high-risk updates, manual review, no automerge, artifact provenance and version-matched compatibility gates remain active. |

The preparation drain keeps the admission listener alive during endpoint removal.
It uses Kubernetes native sleep, with no shell/sidecar and unchanged webhook
timeouts/failurePolicy. Continuous positive and negative runner probes must pass
through upgrade and supported downgrade; a connection refusal is a failed gate.
See [Kubernetes lifecycle hooks](https://kubernetes.io/docs/concepts/containers/container-lifecycle-hooks/)
and the pinned charts' admissionController.container.lifecycle field.

Do not combine phase 03 and phase 04: infrastructure Helm reconciliation occurs
before the downstream policy prune and the original hook must refuse an unsafe
upgrade. The dependency graph remains infrastructure -> kyverno-policies ->
runner apps, with runner Namespace declarations moved ahead of policy readiness
to avoid a cycle. New opted-in workloads instead depend on their own dedicated
namespace bundle. See the [onboarding contract](../../../infra/namespace-defaults/README.md).
Production Cilium configuration remains unchanged; no production network-policy
enforcement claim follows from these resource declarations.

Rollback is conditional:

- Before legacy retirement: revert new validation/mutation additions while the
  originals still enforce. Keep native exception accountability until a verified
  replacement exists. Preserve runner Namespace UIDs during any inventory move.
- Under the bootstrap freeze, with repeated complete zero inventory: restore and
  verify the original 1.19.2 bootstrap policy before thawing only if reverting the
  architectural migration itself. The isolated empty retirement rollback is
  distinct from ongoing Flux namespace rollback.
- After any Flux namespace enrolls: keep its bundle, guards and workload
  dependency. Roll back workloads or restore complete Git bundle revisions.
  Namespace/output prune protection and Orphan deletion preserve defaults during
  accidental source/Kustomization removal; restore reconciliation promptly.
  Never reintroduce a generator over these Flux-owned objects or unenroll/delete
  production resources to make an empty-state predicate pass.
- After chart upgrade: retain CEL/native/runner enforcement while reconciling the
  qualified prior chart. Validate stored/served versions and read/write/admission
  compatibility before restoring legacy validators on 1.19.2. Do not restore the
  generator when Flux namespaces exist. A Git revert alone is not recovery.

The signed chart 3.10.0 artifact currently declares CEL storage v1beta1 while
serving v1; do not infer v1 storage from the application release announcement.
The rehearsal checks the actual chart schema, seeds real isolated historical v1
storage via CRD spec and an API write, and verifies the hook rewrites etcd objects
to the declared target version. This fixture never patches storedVersions status
manually and is never part of the production patch sequence.

CRD compatibility is a release gate. Capture CRD UIDs, all served/storage versions
and retained policy/exception UIDs before upgrade, after the migration hook and
after supported downgrade. Read objects through each served version. Never delete
CRDs or manually edit status.storedVersions. The 3.10 migration hook must actually
rewrite storage; a schema-only change is insufficient. A future proposed pair
must pass the same version-aware gate and vendor Kubernetes support check.

Rollback triggers include lost deny, unexpected exception, changed Namespace UID,
missing/default drift, failed network positive/control probes, failed hooks,
unhealthy controllers/Flux or unknown/stale report collection. Stop promotion;
retain enforcement and defaults, use reviewed rollback/forward-fix and observe
live recovery independently. No timeout increase, safety-check bypass, enforcement
reduction, automatic merge or promised maintenance-window RTO is authorized.

After qualification, the staged Renovate change removes the temporary <3.10.0
restriction without a permanent release pin. Kyverno remains a separate high-risk
manual-review update, outside Monday maintenance, with automerge disabled. The
actual proposed chart/application pair must pass all mandatory scenarios. #2133
stays open through operator acceptance; #2158 is not part of this merge order.
