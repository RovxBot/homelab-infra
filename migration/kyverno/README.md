# Kyverno migration with Flux-owned namespace defaults

Task 18 replaces unused namespace generation with explicit Flux reconciliation.
Kyverno remains responsible for validation, mutation, scoped exceptions and
reporting. The [activation sequence](activation/README.md),
[exception decision table](activation/exception-review.md) and
[qualification evidence](evidence/task18/qualification.json) define review gates.
No production entrypoint references the namespace example, new admission guards
or CEL policy fixtures. Production chart, exceptions and Cilium are unchanged.

The [reusable namespace base](../../infra/namespace-defaults/README.md) preserves
all three legacy resource specifications. A complete four-object bundle and a
separate Flux workload dependency are required. A label alone does not onboard a
namespace. CI checks completeness/dependencies; native admission restricts
identity, metadata, accessible shapes/network specification and destructive deletion;
CI verifies the complete Quantity-map specifications. The empty
legacy generator is retired only under proven native freeze, repeated complete
zero inventories, observed orphan settings and explicit policy/queue absence.

The [architectural decision](../../docs/decisions/0003-flux-namespace-defaults.md)
retains historical generator failures. Historical generation manifests/modules
remain diagnostic evidence, are excluded from every runnable qualification path,
and must never be added to a production Kustomization. No GeneratingPolicy or
additional generator write RBAC is needed by the selected architecture.

Run the [manual deployment workflow](../../.github/workflows/kyverno-cel-rehearsal.yml)
on the implementation branch. Blank scenarios runs **all four mandatory jobs**:
namespace-lifecycle, production-sequence, mutation and validation. A JSON subset
is diagnostic and cannot qualify deployment. Each disposable hosted kind v0.30.0 /
Kubernetes v1.34.0 environment uses enforcing Calico v3.31.0, Helm v3.19.0 and the
committed digest-pinned Flux v2.9.0 artifacts, with a 25-minute execution ceiling,
30-minute job ceiling and unconditional teardown. Production kubeconfig, sync and
credentials are never imported.

The namespace job exercises complete creation, partial/malformed rejection,
legacy equality, dependency blocking and workload readiness, drift prevention and
correction, source/kustomize-controller restart, resource recreation, protected
pruning, source rollback and consumer-first decommissioning. The integrated job
adds full legacy/CEL overlap, all six unchanged exceptions, continuous runner
security, Longhorn mutation, actual report inputs and RBAC, separate legacy prune,
Flux Helm upgrade with the original safety check, storage migration and compatible
chart/policy rollback while retaining CEL/native enforcement and Flux defaults.
Traffic probes sample ingress/egress denial with working positive controls through
restart/upgrade/rollback. Production Cilium retains policyEnforcementMode=never;
isolated Calico results do not establish production NetworkPolicy enforcement.

Validation reruns the 596-case per-engine equivalence corpus, both complete runner
suites and ephemeral-container probes, native exception accountability and real
Audit/background reporting. The mutation job compares 42 actual CREATE/UPDATE
admissions per engine (126 total) including full server-defaulted PodSpecs. Report
consumer regression fixtures preserve legacy/CEL identity, overlap/deduplication,
exceptions, persistence, recovery and collection failure semantics. Fresh explicit
same-finding pass evidence is required to close an incident.

The [version-aware workflow](../../.github/workflows/kyverno-compatibility.yml)
resolves and tests the actual proposed chart/application pair, authenticates exact
release/source/CLI identities, validates image signatures/provenance and vendor
Kubernetes support, and never silently substitutes an older version. Unchanged
evidence can be reused only with complete input **and implementation** hashes and
identical verified release artifacts; Task 18 reruns validation because shared
harness installation changed. The old incomplete reuse contract cannot pass.
Kyverno remains separate from Monday maintenance, manual review and no automerge;
temporary Renovate cap removal is prepared only in the final reviewed upgrade
patch, with no permanent single-release pin.

For local reproduction, use a disposable Linux Docker host with Python/PyYAML
6.0.3, run trust.sh successfully, then set KYVERNO_REHEARSAL_LOCAL=1,
CANDIDATE_TRUST=success and REHEARSAL_SCENARIO to one of the mandatory jobs before
running harness/run.sh under timeout 1500. The entrypoint refuses existing kind
clusters/kubeconfigs. GH_TOKEN may provide read-only GitHub metadata access;
credentials are not written into evidence. Artifact trust uses Sigstore's official
certificate/transparency/TUF checks. New hosted runs stay manual and bounded.

Policy regeneration remains harness/build.py followed by
harness/render-report-identities.py. activation/prepare.py only prepares reviewed
Git patches. Keep #2133 open, #2158 independent/unmerged and #2160 draft until
operator review. No production deployment, merge, bypass or exception change is
performed by this task.
