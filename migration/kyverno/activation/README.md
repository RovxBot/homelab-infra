# Production activation proposal — blocked

No patch in this directory is active. These are sequential review artifacts, not
permission to deploy. **Do not begin production cutover:** fresh CEL generators
on both 1.19.2 and 1.20.0 failed restart drift recovery. See
[deployment qualification](../evidence/task17/qualification.json). Generator
lifecycle, selected-path rollback and full upgrade acceptance remain mandatory.
No acknowledgement bypass, timeout increase or enforcement reduction is proposed.

`python3 migration/kyverno/activation/prepare.py` deterministically regenerates the
patches against the current draft. They apply sequentially in a disposable copy;
Kustomize renders each phase. Rendering does not qualify live reconciliation,
CRD migration, exception validity or rollback. Preserve the established production
Kustomization inventories rather than creating competing owners of policies.

Platform owner RovxBot approves each separately reviewable Git phase. Export fresh
private backups of policy definitions, reports, CRDs, Helm values, triggers,
outputs and UpdateRequests to durable approved storage first. Retain original
metadata for diagnostics; restore manifests must omit server-managed fields.
Never replay old UpdateRequests. Keep #2133 open and #2158 independent.

| Patch | Intended behavior | Gate before the next phase |
| --- | --- | --- |
| `00` | Set prune protection on runner namespaces while their original security policies remain active. | Observe the app Flux revision and prune annotations; capture all three namespace UIDs. |
| `01` | Move namespace declarations ahead of policy reconciliation, keeping labels unchanged. Add CEL validations, all six CEL exceptions, native exception accountability, both runner suites, Longhorn mutation, reporting RBAC/automation and required generator RBAC alongside legacy policies. | Infrastructure Ready and unchanged namespace UIDs, then every CEL policy Ready at the applied revision. Real positive/negative runner and ephemeral-container probes in **both** namespaces, native exception metadata probes, Longhorn mutation and representative Enforce/Audit reports. Owner decisions on expired exceptions are recorded. The old generator remains sole owner. |
| `02a` | Activate the native fail-closed Namespace bootstrap freeze. | Deny both CREATE of an eligible Namespace and UPDATE adding the label; allow unrelated Namespace operations. Inspect native policy type-check warnings and binding. Prove the freeze through every serving API server before relying on zero inventory; HA cache propagation is outside the single-node rehearsal. Refresh **complete** inventory under the demonstrated freeze. Any eligible Namespace, output candidate or UpdateRequest stops cutover. |
| `02b` | Set legacy `orphanDownstreamOnPolicyDelete=true` through a Kustomize patch. | Observe all three live rules before pruning. Refresh the zero inventory again. This protects cleanup ordering; it does not authorize adopting any discovered outputs. |
| `02c` | Retire the empty legacy generator through Flux prune. | Confirm policy absence, no downstreams and no queued work. Restart the background controller to clear legacy caches; establish readiness and repeat zero inventory. Keep the freeze. |
| `02d` | Activate the CEL generator only after the legacy generator is absent. | GeneratingPolicy Ready, exact manifest revision, empty inventory still verified. **Currently blocked by the restart failures.** Never substitute adoption evidence for this gate. |
| `02e` | Remove the freeze only after the qualified CEL generator is accepted. | New eligible Namespace receives all exact defaults. Hold workload onboarding until outputs are observed; generation is asynchronous. Verify drift repair, all deletion/trigger semantics, restart recovery and enforcing-CNI traffic behavior. |
| `03` | Retire legacy validation/mutation/governance/exception resources and both original runner policies after CEL live acceptance. | CEL policies and native guard remain active; both runner suites still deny prohibited pods. All namespaces show zero ClusterPolicy, Policy, CleanupPolicy, ClusterCleanupPolicy and legacy PolicyException objects. No unfinished URs. Observe both app and policy Flux revisions and archive report history. |
| `04` | Upgrade to chart 3.10.0/app 1.20.0, retain CreateReplace CRD handling, default-on legacy check and `acknowledgeLegacyPoliciesNotEnforced=false`; use ghcr.io for the hook. Remove the temporary Renovate cap **only here**. | Verified exact chart/application artifacts, legacy inventory empty before Helm reconciliation, migration hook completed, CEL CRDs store `v1`, all served/stored versions compatible, two admission replicas Ready, other controllers/webhooks Ready, Flux healthy and live behavioral checks accepted. |

The patch sequence retains the infrastructure → kyverno-policies dependency and
adds kyverno-policies → runner app readiness. Runner Namespace declarations move
to infrastructure first, so this does not create a bootstrap dependency cycle.
No PSA label is relaxed. The 15 explicit namespaced CEL runner policies remain
necessary while both job namespaces use privileged PSA for their narrow supported
capability exceptions. Preserve namespace UIDs during the inventory move.

Do not combine phase 03 pruning with phase 04. The Helm hook runs in the upstream
infrastructure reconciliation before downstream policy pruning. A reverse Flux
dependency would create a cycle. Require applied revision and observed readiness,
not merely a successful Kustomize build. Custom Flux health expressions inspect
Kyverno `status.conditionStatus.ready` and conditions; actual admission/report
probes are separate gates.

The chart migration hook must rewrite stored objects, not just change CRD schema.
Retain CRDs and old served versions until storage and rollback compatibility are
proved. Inspect the hook Job's completion/logs and each CRD's storedVersions; do
not manually patch status.storedVersions or delete CRDs. A failed migration stops
promotion. Before accepting any future release, verify its current storage and
API contracts rather than assuming another chart has identical hooks.

Rollback is conditional on the phase and inventory:

- Before legacy retirement, revert new CEL validation/mutation/exception additions
  through reviewed Git while the original enforcing policies remain. Keep native
  exception accountability until a verified replacement exists. Moving Namespace
  ownership back requires the same prune protection and UID checks.
- During the generator freeze, **empty-state rollback** requires a fresh zero
  inventory, no pending work and no generator competition. Remove the empty CEL
  generator through Flux; on 1.19.2 restore and verify the original generator
  before thawing. This path has a harness but is **not yet qualified** because the
  mandatory restart gate failed first.
- Once a Namespace has enrolled, empty-state rollback no longer applies. Freeze
  new triggers and stop for review; never delete, unenrol or strip provenance
  from production resources to force the zero predicate. Prefer a reviewed
  forward fix. Legacy adoption is a separate optional path and has no production
  authorization or mandatory role in the selected migration.
- After 1.20, restoring legacy YAML alone cannot restore enforcement. Keep CEL
  validations, native accountability and both runner suites active. A chart
  downgrade requires a successful isolated storage/API/controller rehearsal
  before restoring legacy objects. Reverting a Git commit alone is insufficient.

Rollback triggers include any lost deny, unexpected exception, changed output UID,
missing or drifting bootstrap output, failed CNI probe, webhook/controller failure,
unknown report collection, failed storage migration or unhealthy dependent Flux
reconciliation. Do not uninstall Kyverno or prune generated defaults as recovery.

The original 75-second reconciliation bound remains. RTO, automatic generator
rollback and production activation are unqualified; no maintenance window is
promised. Keep the Renovate cap and separate high-risk/no-automerge rule until the
qualified upgrade phase is reviewed.
