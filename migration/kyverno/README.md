# Kyverno migration with Flux-owned namespace defaults

The accepted migration uses explicit Flux reconciliation for namespace defaults.
Kyverno remains responsible for validation, mutation, scoped exceptions and
reporting. Production consumes the CEL manifests through infra/kyverno-policies.
The [completed activation history](activation/README.md),
[exception decisions](activation/exception-review.md) and
[Task 21 acceptance](evidence/task21/deployment.json) retain the cutover record.
Current namespace operations belong in the
[Wiki](https://github.com/RovxBot/homelab-infra/wiki/Namespace-Onboarding).

The [reusable namespace base](../../infra/namespace-defaults/README.md) preserves
all three legacy resource specifications. A complete four-object bundle and a
separate Flux workload dependency are required. A label alone does not onboard a
namespace. CI checks completeness/dependencies; native admission restricts
identity, metadata, accessible shapes/network specification and destructive deletion;
CI verifies the complete Quantity-map specifications. The empty
legacy generator retirement was qualified under proven native freeze, repeated
complete zero inventories, observed orphan settings and explicit policy/queue absence.

The [architectural decision](../../docs/decisions/0003-flux-namespace-defaults.md)
retains historical generator failures. The abandoned implementation is linked
from the [historical assessment](../../docs/kyverno/migration-plan.md); its replay
tooling is retired. No GeneratingPolicy or additional generator write RBAC is
needed by the selected architecture. The legacy bootstrap policy and
activation/freeze.yaml remain inputs to isolated qualification.

Run the [manual deployment workflow](../../.github/workflows/kyverno-cel-rehearsal.yml)
on the exact implementation head with **reuse_validation=false**. Leaving scenarios
blank runs **all four mandatory jobs**:
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
identical verified release artifacts. The retained validation contract is
historical: build.py is an implementation input and its old hashes must never be
refreshed to approve untested changes. This retirement requires fresh admissions
with reuse_validation=false. The manual workflow's reuse default is unchanged;
set the input explicitly. Historical incomplete or mismatched contracts fail closed.
Kyverno remains separate from Monday maintenance, with manual review and no
automerge. Task 21 records removal of the temporary Renovate cap after live upgrade
acceptance, with no permanent single-release pin.

For local reproduction, use a disposable Linux Docker host with Python/PyYAML
6.0.3, run trust.sh successfully, then set KYVERNO_REHEARSAL_LOCAL=1,
CANDIDATE_TRUST=success and REHEARSAL_SCENARIO to one of the mandatory jobs before
running harness/run.sh under timeout 1500 with REUSE_VALIDATION=false. The
entrypoint refuses existing kind clusters/kubeconfigs. GH_TOKEN may provide read-only GitHub metadata access;
credentials are not written into evidence. Artifact trust uses Sigstore's official
certificate/transparency/TUF checks. New hosted runs stay manual and bounded.

Policy regeneration remains harness/build.py followed by
harness/render-report-identities.py. Migration inventory and policy identity
checks remain required. Task 21's historical acceptance does not establish fresh
qualification or security-project acceptance; #2133 and #2139 remain independent.
