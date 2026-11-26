# 0003: Explicit Flux-owned namespace defaults

Status: accepted by the platform owner in Task 20, 22 November 2026. Implementation:
#2160, superseding the generator transition portions of #2159's assessment.

Repeated isolated Kyverno generator restart recovery failed on both releases,
with fresh generators as well as adopted/upgraded resources. The quota remained
at injected pods=21 within the unchanged 75-second recovery bound. Preserve
[Task 17 evidence](../../migration/kyverno/evidence/task17/findings.md); do not
resume that investigation or treat a namespace event as automatic recovery.

Production inventory remains empty: no opted-in namespaces, matching bootstrap
outputs or UpdateRequests. This supports a simpler empty-state retirement with
no ownership transfer. Fresh production zero inventory must still be repeated
under an admission freeze before actual retirement; today's read-only inventory
alone does not authorize cutover.

Use a reusable Kustomize Namespace/LimitRange/ResourceQuota/NetworkPolicy bundle,
owned and reconciled directly by Flux, only for explicit reviewed opt-in. Preserve
legacy specifications and owner/PSA requirements. Hold workload reconciliation
on its separate bundle Kustomization's successful readiness. Keep Kyverno for
validation, Longhorn mutation, exceptions and reporting. Add no custom controller,
operator, generator-specific write RBAC or CEL GeneratingPolicy dependency.

The [onboarding contract](../../infra/namespace-defaults/README.md) combines
rendered-bundle CI with reviewed native admission and prune protection. Kubernetes
provides no multi-object atomic admission transaction; do not claim otherwise.
Label-only enrollment is denied. A complete reviewed Git change plus readiness
dependency is required. Accidental removal retains the protected defaults until
source rollback restores reconciliation. Decommission only after consumers/data
are reviewed and removed, through separately observed approval and prune phases.

Retire the legacy generator under a native freeze, repeated complete zero
inventories, observed orphan settings, explicit absence and no queued cleanup.
Any nonempty/unknown inventory stops the process. Never delete outputs, change
labels or transfer ownership to manufacture an empty state. Replace the freeze
only after the permanent GitOps contract and isolated lifecycle are qualified.

The [completed activation history](../../migration/kyverno/activation/README.md) keeps legacy
validation and exceptions until replacements pass live acceptance; prunes them
in a separately observed phase before Helm's original legacy safety hook.
Storage migration and supported chart/policy rollback retain enforcing CEL/native
policies. Keep Flux defaults during rollback; reintroducing a generator over them
is unsupported. Review the five expired exceptions together, with no unapproved
renewal, revocation or widening. Home Assistant stays unchanged.

Calico proves isolated sampled traffic denial with positive controls. Production
Cilium keeps policyEnforcementMode=never; this decision does not enable production
NetworkPolicy enforcement or change Cilium configuration. Operator acceptance,
HA admission propagation, durable backups and per-phase production verification
remain distinct from isolated qualification.
