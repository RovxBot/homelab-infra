# Kyverno CEL migration preflight

Status: **stopped before deployable migration changes**. Inventory and bounded
source tests are verified; policy equivalence, generator lifecycle transfer and
artifact signature verification are not qualified. This change does not contain
PR A or PR B and must not be used as deployment approval.

Assessed 21 November 2026 against main
`2951cb17177c69e2f394bb55bb4ebf64cf8d39bb`. Related investigation:
[#2133](https://github.com/RovxBot/homelab-infra/issues/2133). Historical timeout
causation remains unproven. The existing SAR proposal
[#2158](https://github.com/RovxBot/homelab-infra/pull/2158) remains independent.

## Verified baseline and candidate

| Item | Result |
| --- | --- |
| Live installation | Chart 3.9.2, all four controllers v1.19.2; admission 2/2 ready |
| Candidate, not activated | Exact chart **3.10.0**, application **v1.20.0**; latest stable release checked upstream |
| Kubernetes | v1.34.1; vendor support matrix lists 1.34–1.37 for v1.20 |
| Current source | `e63308f1e16c05cd009ac4864be6ec25c66b49be` (v1.19.2) |
| Target source | `f666af06e9bd310c7e730f1523c0430e90371a89` (peeled v1.20.0 tag; tag object `95b0c7f6d3e3916ff70799eb19caa76223ca7eed`) |
| Chart SHA-256 | `55961392dc0821eb367323f0e9585c061dd4407ef7ada921bd56ea1642c2a301` |
| Target Linux x86-64 CLI archive SHA-256 | `06b8f3ca7b5626473be8fb4d343ad54ab84e317142766f72420e7d44b80942e2` |
| Checksums | Downloaded chart matches Helm index and GitHub release asset digest; CLI archive matches GitHub asset digest |
| Sigstore identity verification | **unavailable**: both verification attempts fail fetching Sigstore TUF root metadata; the trust service is unreachable from this environment |

The expected signature identity is
`https://github.com/kyverno/kyverno/.github/workflows/release-publish.yaml@refs/tags/v1.20.0`,
issuer `https://token.actions.githubusercontent.com`. Reading that identity from
the certificate is not verification. No `insecure`, ignored transparency-log, or
substituted trust-root option was used. The downloaded target CLI was not run.
The Go source tests below use exact Git commits; they do not establish release
artifact provenance. Do not describe checksum agreement as completed provenance.

Primary references, checked during the assessment:

- [Official CEL migration guide](https://kyverno.io/docs/guides/migration-to-cel/)
- [Official upgrade guide](https://kyverno.io/docs/installation/upgrading/)
- [Release and Kubernetes support matrix](https://kyverno.io/docs/installation/releases/)
- [v1.20.0 release](https://github.com/kyverno/kyverno/releases/tag/v1.20.0)
- [Official chart index](https://kyverno.github.io/kyverno/index.yaml)

## Complete inventory

Read-only all-namespace queries returned **six ClusterPolicies, two namespaced
Policies, six legacy PolicyExceptions**, and zero CEL policies/exceptions. No
CleanupPolicy or ClusterCleanupPolicy was found. Every live policy has a Git
source; there were no extra live-only policies in this snapshot. The two runner
Policies are outside `infra/kyverno` and must migrate before the chart upgrade.
A follow-up read-only report query returned 261 PolicyReports and 70
ClusterPolicyReports, with zero GlobalContextEntries or ephemeral reports at
that moment. Report counts are transient and do not establish policy outcomes.

The Git and live specs agree on explicitly authored fields. Live objects also
contain defaults: admission=true, skipBackgroundRequests=true,
allowExistingViolations=true on validations, and the legacy top-level Audit
default. Rule-level Enforce/Audit remains authoritative. Longhorn mutation and
runner policies have default emitWarning=false; the other ClusterPolicies set it
true. These defaults are part of the behavioral comparison, not noise to discard.

At 00:34 UTC the infrastructure Flux Kustomization was Ready. The
`kyverno-policies` Kustomization reported Ready=False following a timeout calling
the policy-validation webhook for namespace bootstrap. HelmRelease Ready=True
does not establish policy reconciliation success. No admission probes, server
dry-runs, or writes were made to production.

The [machine-readable inventory](migration-inventory.json) contains every old
resource identity, all **29 authored rules**, proposed target identity, planned
test cases, exact exception references/matches/governance, and source contract
hashes. All migration behavior entries are deliberately marked **not run**.
Run its coverage check with:

```sh
python3 scripts/ci/check_kyverno_migration_inventory.py
```

This detects omitted legacy resources anywhere under apps/infra/clusters, changed
source specs/annotations, missing rules/tests and broadened exception targets. Its unit
tests run in the existing CI test-discovery step. It is an inventory check, not a
policy behavior or deployment gate. Future generated/autogen rules and any new
live drift must also be inventoried when re-running the migration preflight.

### Rule mapping

All proposed targets use `policies.kyverno.io/v1`, already served in the deployed
cluster. Names below are design proposals, not installed resources. `pod/<rule>`
means a separate ValidatingPolicy named `platform-pod-standards-<rule>`. This
split is necessary because CEL PolicyExceptions reference whole policies rather
than individual named rules. Combining these seven rules would widen exceptions.

| Existing policy / rule | Proposed target | Retained behavior and required comparison |
| --- | --- | --- |
| platform-namespace-standards / require-psa-and-owner-labels | ValidatingPolicy platform-namespace-standards | Enforce, background, warning; owner plus all six PSA labels nonempty; exclude default, kube-node-lease, kube-public, kube-system, sops |
| platform-pod-standards / require-owner-label | pod/require-owner-label | Enforce; workload and CronJob pod-template owner |
| platform-pod-standards / require-requests-and-limits | pod/require-requests-and-limits | Enforce; CPU/memory requests/limits on regular and init containers, including CronJobs |
| platform-pod-standards / require-runtime-hardening | pod/require-runtime-hardening | Enforce; non-root inheritance, privilege/escalation, drop ALL, RuntimeDefault; regular/init and CronJob |
| platform-pod-standards / disallow-host-network | pod/disallow-host-network | Enforce; optional hostNetwork and legacy anyPattern alternate-path behavior |
| platform-pod-standards / require-digest-pinned-images | pod/require-digest-pinned-images | Enforce; exact legacy substring check on regular-container images |
| platform-pod-standards / disallow-latest-tags | pod/disallow-latest-tags | Enforce; exact legacy :latest substring check on regular-container images |
| platform-pod-standards / require-probes | pod/require-probes | Enforce; readiness and liveness for containers with ports; only Deployment/StatefulSet/DaemonSet |
| policy-exception-accountability / require-owner-reason-expiry-and-issue-tracking | ValidatingPolicy policy-exception-accountability | Enforce governance on exceptions in kyverno; both API groups during overlap; verify actual admission execution |
| platform-flux-guardrails / audit-manual-edits-to-flux-managed-resources | ValidatingPolicy platform-flux-guardrails | Audit + warning, no background; Flux label, CREATE/UPDATE/DELETE, exact identities/exclusions |
| platform-namespace-bootstrap / generate-default-limitrange | GeneratingPolicy platform-namespace-bootstrap, output 1 | Synchronize LimitRange default-container-limits; contents/lifecycle/UID proof required |
| platform-namespace-bootstrap / generate-default-resourcequota | Same GeneratingPolicy, output 2 | Synchronize ResourceQuota namespace-defaults; contents/lifecycle/UID proof required |
| platform-namespace-bootstrap / generate-default-networkpolicy | Same GeneratingPolicy, output 3 | Synchronize NetworkPolicy default-deny; uninterrupted ingress/egress denial |
| longhorn-csi-registrar-lifecycle / use-native-registrar-shutdown | MutatingPolicy longhorn-csi-registrar-lifecycle | Exact apps/v1 DaemonSet, namespace/name, container index zero, six image spellings and /bin/sh command; remove only preStop |
| gitea-job-security / restricted-with-sanitizer-capability | NamespacedValidatingPolicy gitea-jobs/gitea-job-security | Enforce every restricted PSA control with only the three existing pinned-image control exclusions |
| gitea-job-security / bound-job-environment | Same namespaced policy | Exact owner sam, token=false, pinned image, job container and four budgets |
| gitea-job-security / one-job-container | Same namespaced policy | Exactly one regular container, no init/ephemeral containers |
| gitea-job-security / drop-all-and-only-allow-ptrace | Same namespaced policy | Drop ALL; only SYS_PTRACE can be added |
| gitea-job-security / ptrace-only-in-sanitizer-jobs | Same namespaced policy | Exact sanitizer label required for SYS_PTRACE |
| gitea-job-security / ordinary-job-identity | Same namespaced policy | Ordinary jobs retain pod/container UID 1000 and non-root constraints |
| gitea-job-security / bounded-sanitizer-identity | Same namespaced policy | Sanitizer container UID/GID 0, non-root=false, SYS_PTRACE and ALL drop |
| gitea-job-security / no-extra-job-credentials | Same namespaced policy | Only emptyDir or Secret volumes, preserving current scope |
| wowdecomp-linux-security / restricted-with-sanitizer-capability | NamespacedValidatingPolicy wowdecomp-ci-jobs/wowdecomp-linux-security | Restricted PSA controls and exact existing pinned-toolchain exclusions |
| wowdecomp-linux-security / bound-job-environment | Same namespaced policy | Exact owner, token=false, pinned image, budgets and imagePullSecrets |
| wowdecomp-linux-security / one-job-container | Same namespaced policy | Exactly one regular container, no init/ephemeral containers |
| wowdecomp-linux-security / bounded-toolchain-capabilities | Same namespaced policy | Drop ALL; only CHOWN/DAC_OVERRIDE/FOWNER/SETUID/SETGID/SYS_PTRACE |
| wowdecomp-linux-security / no-extra-job-credentials | Same namespaced policy | Only emptyDir/Secret volumes |
| wowdecomp-linux-security / no-mounted-registry-credential | Same namespaced policy | Registry credential cannot be mounted as a volume |
| wowdecomp-linux-security / no-registry-credential-in-environment | Same namespaced policy | Registry credential excluded from env secretKeyRef and envFrom |

Pod standard rules retain background evaluation/warnings and exclusions for
flux-system, kube-node-lease, kube-public, kube-system and kyverno. Owner,
budgets, runtime, networking and image rules match Deployment, StatefulSet,
DaemonSet, Job and CronJob, not standalone Pods. Do not invent Pod autogeneration.
Native namespace PSA remains an independent admission control; exceptions do not
override it. Read-only namespace inspection shows both runner namespaces use
**privileged PSA enforcement with restricted audit/warn**, while their Kyverno
policies impose restricted controls with bounded exceptions. Losing those
Kyverno rules would therefore remove their effective restricted enforcement.

### Behavioral gates that remain unresolved

- Compare valid/invalid CREATE and UPDATE, including already violating oldObject
  and updates introducing a violation. Legacy allowExistingViolations=true is
  present live; a direct CEL predicate need not preserve that update behavior.
- Preserve legacy truthiness and inheritance. JMESPath `false || podValue` can
  fall back to a pod value; CEL optional-field coalescing is not automatically
  equivalent. Test absent maps/arrays, empty values and explicit false separately.
- Compare both anyPattern paths for CronJobs versus other controllers, including
  paths that do not exist. Do not infer enforcement solely from policy wording.
- Image rules currently inspect regular containers, whereas budgets and runtime
  also inspect init containers. Moving the image rules to init containers would
  be a separately reviewed strengthening, not an unnoticed equivalence change.
- Preserve the exact digest/latest substring behavior unless a deliberate change
  is reviewed. Every malformed/missing-field case must produce a deliberate
  decision rather than an accidental evaluation error or skip.
- Flux auditing needs oldObject on DELETE, both exact Flux service-account
  usernames, excluded endpoint/report kinds and namespaces, and the conjunction
  of Longhorn service-account identity, BackupTarget kind and namespace. A human
  editing BackupTarget must remain audited.
- CEL has no direct legacy podSecurity block. Both runner policies require a full
  restricted-control translation plus comparison against native Kubernetes
  v1.34.1 PSA and the pinned images. Removing only their podSecurity rules would
  lose protections not duplicated by the other runner rules.

## Exceptions and accountability

All six live exception specs equal Git. Every exception is in namespace kyverno,
has owner=platform, a nonempty reason, an expiry annotation and
github.cooked.beer/issue-create="true". None currently has an issue URL in Git or
the inspected live metadata. Preserve those values exactly; do not invent URLs
or extend expired dates. These policies require a nonempty expiry string, not a
future-date check. Five dates have passed as of 21 November 2026 and need owner
review; that is existing governance debt, not grounds to drop the exceptions.

References below become the corresponding separate `pod/<rule>` policies.

| Exception | Existing rule references | Exact match scope | Expiry |
| --- | --- | --- | --- |
| chart-managed-workloads | require-requests-and-limits, require-owner-label, require-probes | Pod/Deployment/StatefulSet/DaemonSet/Job/CronJob in longhorn-system | 2026-08-31, passed |
| gpu-operator-workloads | require-owner-label, require-requests-and-limits, require-runtime-hardening, disallow-host-network, require-digest-pinned-images, require-probes | Same six kinds in gpu-operator | 2026-10-31, passed |
| home-assistant | disallow-host-network, require-runtime-hardening | Deployment home-assistant in home-assistant only | 2027-02-28 |
| mutable-image-tags | require-digest-pinned-images | Any kind/name in longhorn-system/wireguard/wotlk OR Deployment/CronJob/StatefulSet with names cloudflared/grimguzzler-registration/homepage-assets/pod-report in cloudflared/homepage/wotlk | 2026-08-31, passed |
| privileged-namespaces | require-runtime-hardening, disallow-host-network | Any kind/name in longhorn-system/wireguard/wotlk | 2026-08-31, passed |
| wotlk-latest-tag | disallow-latest-tags | CronJob wotlk-weekly-restart OR Job wotlk-weekly-restart* in wotlk | 2026-08-31, passed |

The mutable-image second branch is a Cartesian combination of allowed kinds,
names and namespaces, not a name-to-namespace pairing. Preserve match.any OR
semantics and each branch's AND semantics. The existing Job prefix wildcard is
intentional; introduce no additional wildcard. Tests must show each exception
works, other rules still deny, and neighboring names/kinds/namespaces do not gain
an exemption. Retain both old and new exception resources while their respective
policies coexist, then retire only the old API after live acceptance.

There is an additional admission gate: the observed Fail resource webhook
excludes kyverno through namespaceSelector; no ValidatingAdmissionPolicy or
binding exists live. The dedicated exception webhooks have no namespace exclusion
but validate exception structure, not automatically this custom governance
policy. The legacy dedicated webhook's configured versions are v2alpha1/v2beta1
while the exceptions are served at v2; equivalent matching requires care. Thus
the presence of the accountability ClusterPolicy does not prove that missing
governance is rejected in admission. No live invalid exception was submitted.
Qualify a reviewed governance route in an isolated cluster and test both API
groups before claiming enforcement. Do not broadly admit the controller's own
namespace into fail-closed resource webhooks without a bootstrap/deadlock review.

## Generator ownership gate

The read-only snapshot found:

- Zero namespaces labelled homelab.cooked.beer/bootstrap=enabled.
- Zero downstreams selected by the old policy label, and zero UpdateRequests.
- A separate all-namespace scan of **all 28 LimitRanges/ResourceQuotas/
  NetworkPolicies**, without a label selector, found none of the three target
  names, no generate.kyverno.io labels and no Kyverno managed-by objects.

There are no identified existing bootstrap outputs to transfer in this snapshot.
This is not permission to assume the set stays empty between reviewed commits;
refresh the inventory at every cutover gate. It also does not prove generator
lifecycle equivalence for future namespaces or for a rollback.

The source boundary reproducer
[`kyverno-generation-handoff_test.go`](../../scripts/ci/fixtures/kyverno-generation-handoff_test.go)
ran against unmodified source at both exact commits. It uses a synthetic
legacy-shaped ConfigMap and fake clients, not actual bootstrap outputs:

| Candidate approach | Executed result | Consequence |
| --- | --- | --- |
| New policy name, same trigger | Existing resource is ignored and not tracked | Renaming alone does not adopt/reconcile old outputs |
| Same policy name and trigger UID | Resource updates in place; UID retained; legacy generate-rule label disappears | Name equality can permit a write but is not a transfer of legacy cleanup/provenance/watch state |
| Same policy name, different trigger UID | Existing resource is ignored | Trigger identity is material |

This runs the real `contextProvider.GenerateResources` code in each release.
Its matching relies on managed-by, policy-name and trigger UID labels:
[1.19.2 implementation](https://github.com/kyverno/kyverno/blob/e63308f1e16c05cd009ac4864be6ec25c66b49be/pkg/cel/libs/context.go),
[1.20.0 implementation](https://github.com/kyverno/kyverno/blob/f666af06e9bd310c7e730f1523c0430e90371a89/pkg/cel/libs/context.go).
The test does not execute authentic legacy provenance signing, admission label
protection, informer/watch reconstruction, Kubernetes garbage collection, or
Flux pruning. It deliberately does not claim those are proven safe.

The deployed release's
[policy-deletion controller](https://github.com/kyverno/kyverno/blob/e63308f1e16c05cd009ac4864be6ec25c66b49be/pkg/policy/generate.go)
can enqueue deletion for synchronized outputs unless orphaning is configured;
[cleanup](https://github.com/kyverno/kyverno/blob/e63308f1e16c05cd009ac4864be6ec25c66b49be/pkg/background/generate/cleanup.go)
uses policy identity, downstream UIDs and provenance. This is not neutralized by
creating a same-named GeneratingPolicy. In 1.20 legacy generation UpdateRequests
are dropped rather than executed. A queue that happens to be empty now is not a
cleanup or handoff guarantee.

**Stop at this gate.** Do not connect a new synchronizing generator to Flux or
prune the old one until an isolated 1.19.2-to-1.20.0 GitOps rehearsal demonstrates:

1. Identical three output objects and namespace/system exclusions.
2. No overlapping active owners or competing writes at any step, including retries.
3. Same downstream UIDs across transfer, no delete/recreate interval, and continuous
   default-deny protection. Check API audit/watch evidence, not final YAML alone.
4. Policy and trigger updates, unlabelling, trigger deletion, downstream drift and
   deletion, rule/policy removal, controller restart and cache reconstruction.
5. No stale legacy UpdateRequest or authenticated cleanup request can delete the
   new owner's objects. Reverting the Git sequence also preserves their UIDs.

A disposable Kubernetes/Flux rehearsal environment was unavailable here (Docker
daemon unavailable). No empty-snapshot exemption, manual relabelling, policy
disablement, or speculative orphaning procedure has been implemented.

### Reproduce the bounded source tests

Check out the exact application source commit corresponding to the chart being
examined. Keep that tree clean. With `SOURCE` pointing to that checkout and
`REPO` to this repository, construct a test-only Go overlay:

```sh
export SOURCE=/path/to/verified/kyverno-source
export REPO=/path/to/homelab-infra
python3 - <<'PY'
import json, os, pathlib
source = pathlib.Path(os.environ['SOURCE']).resolve()
fixture = pathlib.Path(os.environ['REPO']).resolve() / 'scripts/ci/fixtures/kyverno-generation-handoff_test.go'
pathlib.Path('/tmp/kyverno-generation-overlay.json').write_text(json.dumps({
    'Replace': {str(source / 'pkg/cel/libs/migration_boundary_test.go'): str(fixture)}
}))
PY
cd "$SOURCE"
go test -p 4 -overlay /tmp/kyverno-generation-overlay.json ./pkg/cel/libs \
  -run '^(TestGenerateResources_|TestMigrationBoundary)' -count=1 -v
```

This is a source diagnostic, not the version compatibility gate requested for
deployment. Future source changes may invalidate the overlay and require review.

## Chart, CRD, webhook and reporting review

Both downloaded charts use the kyverno-api dependency for CEL CRDs, conditioned
on crds.install. Bundled kyverno-api changes from 0.0.1-alpha.4 to
0.0.1-alpha.5; those are upstream's bundled dependencies of the stable application
chart, not a selection of a prerelease application. Do not independently float
this dependency. The optional reports-server changes 0.1.6 to 0.1.7. There is no
installed upstream kyverno-policies Helm chart here: infra/kyverno-policies is the
repository's Kustomize aggregator.

Live CEL CRDs serve v1/v1beta1 (some also v1alpha1), with storedVersions=v1beta1.
The target moves storage to v1. Retain CRDs and their old served versions until
storage migration has completed and rollback compatibility is proven. The target
chart's default migration hook enumerates CEL resources, GlobalContextEntry and
UpdateRequest, and excludes legacy policy resources. Verify that it runs and
storedVersions are correct after upgrade; a successful Helm render cannot prove
that. Never patch status.storedVersions manually or delete legacy CRDs.

The existing migration hook registry override to ghcr.io remains necessary.
The target adds a pre-install/pre-upgrade legacy-policy check whose default
image registry is reg.kyverno.io, known to be unavailable on this network. PR B
must explicitly review/render an override for
upgrade.legacyPolicyCheck.image.registry=ghcr.io, verifying the exact image and
hook RBAC. Keep the check enabled and
upgrade.acknowledgeLegacyPoliciesNotEnforced=false. Neither disabling it nor
acknowledging loss of enforcement is part of this plan.

Preserve admission replicas=2, resource/policy webhook failurePolicy=Fail,
10-second timeouts, autoUpdateWebhooks and the existing narrowly scoped
Longhorn/Cilium/node-Lease match condition. Optional auxiliary Ignore webhooks
exist already; do not misrepresent every auxiliary webhook as fail-closed.
Recheck rendered and controller-reconciled rules, selectors, object protection,
restart/drift repair and exception behavior with the proposed policy set.

### Repository consumers and CI

| Location | Migration work required before qualification |
| --- | --- |
| infra/kyverno/policies, exceptions | All resources in the inventory; preserve defaults, matches and per-rule exception scope |
| infra/kyverno/automation | Issue sync reads wgpolicyk8s.io/v1alpha2 reports; it does not enumerate exceptions or implement their issue-create marker |
| github-issue-sync-rbac.yaml | Existing unused legacy policyexceptions read permission; move/add only the APIs actually needed during overlap, no broad RBAC |
| github-issue-sync-configmap.yaml | Preserve timestamps/persistence/exclusions; new CEL report policy/rule identities can change issue titles; avoid closing old issues merely because identities change |
| infra/kyverno-policies; clusters/home/infrastructure/kyverno-policies-kustomization.yaml | prune=true, dependency on infrastructure, no policy readiness wait; use explicit CEL health/readiness gates |
| clusters/home/apps; infrastructure/gpu-operator-kustomization.yaml | Home Assistant and GPU operator depend on kyverno-policies; runner policies are reconciled separately under apps-gitea-runners |
| .github/workflows/ci.yml | Baseline CLI is 1.15.2, resource-budget CLI separately fixed at 1.19.2; neither is a proposed-version gate; downloads lack signature verification |
| scripts/ci/kyverno_gate.py; .github/kyverno-baseline.yaml | Explicit legacy policy/exception paths and policy/rule debt keys; test real CEL report formats, Enforce/Audit and fail/error/skip; review identity changes rather than refreshing away violations |
| scripts/ci/test_platform_resources.py | Existing 123 resource-budget cases; extend to all migrated rules and both release engines |
| ops/gitea/check-job-policy.py; runner job-security.yaml files | Both pools, native PSA and every rule above; current CLI-exit-only checks must also distinguish intended deny from evaluation error/no-policy |
| .github/workflows/kubescape-checks.yml; scripts/ci/check_kubescape.py and tests | Additional consumer explicitly requires CLI 1.19.2 and platform-pod-standards path; update compatibility only in scope of later policy migration; keep Kubescape suspended |
| scripts/ci/check_python_helpers.py; tests/test_kyverno_gate_reports.py | Existing report/helper tests need CEL reports and identity transition fixtures |
| scripts/kyverno-timeout-evidence.py and tests | Keep bounded read-only evidence collection; recognise new policy kinds/routes without claiming incident resolution |
| README.md; docs/runbooks/renovate-operations.md; renovate.json; .github/tests/renovate-policy.test.cjs | Gate documentation and existing high-risk/version restriction tests must change only after qualification |

No PolicyReport consumer was found in infra/pod-report. Its cluster-health/Flux
collection still needs to distinguish reconciliation failure from collection
failure. Existing sync automation does not currently fulfill the issue-create
annotation itself; do not silently promise six tracked exception issues exist.

The compatibility gate to build after the ownership gate must resolve the exact
proposed HelmRelease chart version from the trusted upstream index, verify chart
bytes/signature, read its appVersion and bundled CRDs, verify the corresponding
immutable source and signed CLI, and assert the executed CLI version. Reject
prereleases, mutable overrides, unknown provenance and unsupported Kubernetes
versions. Verify image digests/signatures as part of artifact qualification.
Never fall back to a known older CLI when the proposed one fails.

For compatible updates, select test source/artifacts dynamically from that
verified pair; do not introduce an endless release allowlist. Use API/schema,
controller/webhook and decision/lifecycle contracts to reject breaking changes,
including breaks in minor releases. Require a reviewed migration artifact when
those contracts change. A chart/application major transition must require review.

Run the same meaningful fixtures on old/new engines on the current release,
then CEL on the candidate; include positive/negative assertions for every
inventory entry, exception, native PSA, missing/malformed field, controller
readiness, generation/mutation and Audit reporting. Fail on skipped or errored
security checks, not only unexpected denies. Compare final generated objects
and complete lifecycle histories, not CLI-rendered generate output alone.

### SAR review for v1.20

The two kind filters remain in the target chart, and resource-filter handling
still returns success before policy evaluation. However the legacy
generate-policy validation/auth path is removed. Target policy admission calls
the CEL GeneratingPolicy compiler/validator; it does not call the old nested
generate authorization helper. Self-SARs still occur during controller startup
permission checks. Thus the exact validation dependency motivating #2158 has
changed; source inspection does not establish the same request chain in 1.20.

Do not copy the SAR condition into PR B by inertia. Reuse the useful Kubernetes
matching/native-validation and real-controller reconciliation tests from #2158
with dynamically matched source and CLI if proposing that optimization on the
new release. Revalidate provenance guards and native SAR validation, LocalSAR,
other authorization operations, exception routes and Flux auditing. There is
no target-version admission-equivalence proof in this preflight, and no claim
that the upgrade fixes the historical incident.

## Controlled GitOps phases

Two adjacent PRs cannot currently be certified safe. Preparation and live
acceptance must be separated; the following are gated phases, not merge-ready
instructions. Platform owner RovxBot reviews and accepts each transition. Do not
merge/deploy any phase as part of this assessment.

| Phase | Proposed work | Mandatory acceptance before proceeding |
| --- | --- | --- |
| 0, this preflight | Inventory, matrix, source evidence, ownership/provenance gates | Owner acknowledges outstanding gates; no production changes |
| 1, future PR A | On 3.9.2/1.19.2 add equivalent CEL validations, scoped exceptions, tested mutation, report automation and readiness dependencies alongside legacy resources | Isolated equivalence suite passes; live CEL policies ready; invalid resources still denied and Audit reports present; both runner pools covered; old generator stays sole owner |
| 2, generator transition prerequisites | Separately reviewed/rehearsed GitOps ownership and cleanup protocol if any outputs can exist | Signature/provenance/watch/queue/UID continuity proof; no dual synchronizers; refresh live inventory; no speculative disablement/orphaning |
| 3, generator handoff | Only the exact protocol qualified in phase 2 | Three outputs/reconciliation/deletion semantics and rollback verified; no ownership competition or protection gap |
| 4, legacy retirement | After CEL live acceptance, remove legacy policies/exceptions through reviewed Git and Flux prune, including separately reconciled runner resources | Refreshed all-namespace legacy inventory empty; no stale URs; reports/issue identity transition accepted; CEL still ready/enforcing |
| 5, future PR B | Exact stable application/chart pair, necessary registry/CRD values, version-matched security CI; depends explicitly on phases 1–4 | Provenance verified; legacy check passes unmodified; storage migration passes; both replicas ready, Flux healthy, post-upgrade behavior accepted |
| 6, Renovate/cleanup | Lift <3.10.0 only after qualification; reviewed documentation/test updates | Kyverno remains separate high-risk PR, no automerge, explicit dashboard/reviewer gate; compatible updates use dynamic tests; breaking contracts fail |

If a rehearsed empty-output cutover proves some generator phases unnecessary,
document that proof and its race/rollback controls before reducing the number of
phases. Otherwise use additional reviewed PRs. Do not combine legacy removal
with the Helm upgrade: infrastructure readiness is upstream of kyverno-policies,
and the chart check would see old objects before the downstream Kustomization
could prune them. Adding a reverse dependency would create a cycle.

Replace syntax-only readiness with CEL Ready conditions and controller/webhook
acceptance, while preserving dependencies for Home Assistant/GPU and adding the
necessary explicit gate for separately reconciled runner policies. Respect
prune=true inventories: a new API group is a new object even with the same name.
Old/new exceptions need a deliberate overlap rather than an in-place API swap.
Update or retire this preflight's legacy-inventory assertions as part of the
reviewed legacy-retirement phase; they are not a requirement to retain legacy
objects indefinitely.

No production resource, CRD, admission setting or Renovate restriction changes
in this preflight. No merge of #2158, issue closure, activation of Kubescape or
unrelated infrastructure changes are authorized by this document.

## Backup and rollback

Read-only raw exports of the 14 legacy policy/exception objects, current CEL
inventory, all three generated-resource kinds, CRDs, UpdateRequests, HelmRelease,
controller deployments/config and webhooks were captured privately under
`/tmp/kyverno-cel-assessment` for this session. The directory is mode 0700 and
cluster JSON exports mode 0600. They are not committed or copied to issues.
Temporary storage is not a durable recovery backup. Before any production phase,
the operator must export a fresh copy to approved durable private storage and
verify restoration in the disposable rehearsal cluster.

Read-only export examples (choose a private destination first):

```sh
umask 077
kubectl get clusterpolicies.kyverno.io,policies.kyverno.io,cleanuppolicies.kyverno.io,clustercleanuppolicies.kyverno.io,policyexceptions.kyverno.io -A -o yaml > legacy-policies.yaml
kubectl get generatingpolicies.policies.kyverno.io,mutatingpolicies.policies.kyverno.io,validatingpolicies.policies.kyverno.io,namespacedvalidatingpolicies.policies.kyverno.io,policyexceptions.policies.kyverno.io -A -o yaml > cel-policies.yaml
kubectl get limitranges,resourcequotas,networkpolicies -A -o yaml > bootstrap-resource-inventory.yaml
kubectl get updaterequests.kyverno.io -A -o yaml > update-requests.yaml
```

Include any additional CEL kinds found by full API discovery. Record object and
trigger UIDs, managed fields, ownership labels, provenance, policy UID, output
contents and queued cleanup work. Keep raw backup metadata for analysis; prepare
reviewed restore manifests without server-managed metadata for GitOps. Never
restore old UpdateRequests blindly or commit raw cluster exports/secrets.

Rollback criteria: missing denies, missing Audit reports, unexpected exemptions,
bootstrap deletion/recreation or loss of denial, controller/webhook unavailability,
failed storage migration, or unhealthy dependent Flux Kustomizations.

- Before legacy retirement, revert only the new validation/mutation/exception
  manifests through reviewed Git. Generator rollback is a separate protocol;
  deleting a synchronized policy is not a safe generic rollback.
- After legacy retirement but before upgrade, keep qualified CEL enforcement
  running. If legacy restoration is required, restore reviewed manifests through
  Git while 1.19.2 still supports their writes; prove exceptions and readiness
  before retiring any equivalent CEL policy. Preserve one generator owner.
- After 1.20, legacy APIs are read/export/delete only and are not enforced.
  Recreating old YAML or reverting only the policy commit cannot restore
  protection. Prefer a forward fix. A chart downgrade to 3.9.2 must first be
  rehearsed with current CEL objects, retained CRDs and their storage versions;
  keep CEL protection active across it. Only then can legacy resources be
  restored through Git and accepted on the old engine.
- A recreated legacy policy has a new UID, so old provenance and cleanup identity
  cannot be assumed valid. Application downgrade alone does not recover generator
  ownership. Recover data only through the qualified ownership procedure, with
  known outputs retained. Never uninstall Kyverno, delete CRDs or force-prune
  generated outputs as recovery shortcuts.

RPO target: zero policy definitions or bootstrap objects lost. Continuity target:
no enforcement/default-deny gap. Cutover duration and recovery time are **not
qualified** until the full rehearsal measures them; no production window is
proposed. Confirm backup restoration, storage downgrade and ownership recovery
before accepting a live cutover.

## Verification record and handoff

| Evidence | Status |
| --- | --- |
| Git/live inventory, served/stored CRD versions, controller replicas, Helm values, webhook selectors, Flux state | verified, read-only snapshot |
| Eight policies / 29 rules / six exceptions inventoried; omission/widening negative controls | verified by inventory check and eleven unit tests |
| Same generation source cases on v1.19.2 and v1.20.0 | verified: 13 upstream tests plus three migration-boundary subcases per release |
| Target source configuration, webhook-handler and GeneratingPolicy validation test packages | verified |
| Target source generating-policy watcher test package | verified; does not rehearse transfer from the old generator |
| Current source existing-object/foreach validation tests | verified: two tests; not a migrated-policy equivalence suite |
| Chart and CLI byte integrity | verified against index/release digests |
| Release chart/CLI signature trust verification | unavailable: trusted-root service unreachable |
| All migrated validation/mutation/generation/exception behavior | not run: deployable migration intentionally not prepared beyond gates |
| Actual bootstrap output lifecycle, Flux handoff, PSA and rollback rehearsal | unavailable: disposable cluster not available; no production substitute used |
| Proposed final deployment free of legacy resources | not run; current deployment still has all 14 legacy resources |
| Version-matched CI and Renovate unpin | not implemented; blocked behind qualification |
| Post-upgrade live acceptance | not run; no upgrade/deployment |

Repository checks executed locally: 64 CI Python tests and 231 operational Python
tests passed without skips. The CI suite includes the eleven new inventory
tests. Whitespace checks passed. The full manifest/security behavior suite was
not substituted with these checks; its migration cases remain not run above.

The next safe step is to make signature verification and a disposable
version-matched Kubernetes/Flux rehearsal available, then qualify the generator
and governance route before producing deployment changes. Keep #2133 open until
post-upgrade verification and independent incident evidence justify closure.
