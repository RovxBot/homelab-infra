# Flux namespace onboarding contract

This reusable base is inert until a reviewed namespace overlay and its own Flux
Kustomization reference it. No current namespace is enrolled by this change.
Adding `homelab.cooked.beer/bootstrap=enabled` alone is unsupported. The legacy
generator is retired through the separately gated empty-state sequence; no CEL
GeneratingPolicy replaces it.

Copy the [isolated example](../../migration/kyverno/namespace-example/flux.yaml)
into the appropriate `clusters/home` entrypoint, change the namespace and paths,
and add a namespace overlay referencing `infra/namespace-defaults/base` with
`namespace: <name>`. Kustomize renames the Namespace and namespaces the other
three objects. Review the owner and all six existing PSA labels. Restricted PSA
is the base default; any workload needing a different level requires the existing
platform security review. Do not enroll system namespaces or existing namespaces
implicitly. Keep the exact legacy LimitRange, ResourceQuota and NetworkPolicy
specifications; extra allowances belong in separate reviewed NetworkPolicies.

Use a dedicated Flux Kustomization named `namespace-<name>` in `flux-system`,
labelled `homelab.cooked.beer/namespace: <name>`, with `wait: true`, `prune: true`
and `deletionPolicy: Orphan`. Its source contains exactly the four bundle objects.
Every workload reconciliation targeting that namespace, including HelmRelease
`targetNamespace`, must explicitly `dependsOn` this bundle. The repository CI
renders the production Flux graph and rejects partial bundles, malformed specs,
duplicate owners, mixed workload bundles and missing readiness dependencies.
Run `python3 scripts/ci/check_namespace_defaults.py` before review.

The [reviewed native admission safeguards](admission.yaml) activate in migration
phase 02d while the temporary freeze remains. They restrict enrollment and
Namespace lifecycle to the Flux kustomize-controller identity and expected Flux
inventory labels, require owner/PSA/contract metadata, deny removal of enrollment,
and restrict default writes to the authenticated Flux identity. CI verifies
exact Quantity-map specifications, which native CEL structural type projection
does not expose. Native guards also validate accessible shapes and the complete
NetworkPolicy spec. Manual default writes and steady-state deletion are denied.
Flux repairs trusted-identity spec/metadata drift and
recreates a deleted resource during an explicitly approved decommission test.
No generator write RBAC is added. Native policies cannot inspect a Git commit or
make a four-object transaction atomic: reviewed CI supplies bundle completeness,
and Flux dependencies hold workloads until reconciliation succeeds. Admission
guards alone do not prove all four objects exist. Platform administrators can
change admission policy; that existing administrative trust boundary is retained.

All four resources are prune protected. Accidental source omission retains them;
deleting their Kustomization orphans them. Reinstating the complete source restores
Flux ownership and drift repair. An orphaned bundle is a failed operational state
requiring restoration, not a completed decommission. Removing a bootstrap label
does not decommission a namespace and is denied once these safeguards activate.

Run `python3 scripts/ci/check_namespace_defaults.py --live` with the normal
administrative kubeconfig after reconciliation and during operational checks.
This read-only check fails for missing/suspended/stale Flux owners, missing
inventory membership or ownership labels, partial bundles and specification drift.
Collection failure or an incomplete inventory fails the check. It detects retained
resources after source or Kustomization removal; source-only CI cannot observe
that live orphan state. Restore the complete bundle and its original owner.

Decommission in separately reviewed Git changes: first remove consumers and wait
for their pruning, check Pods, PVCs, Jobs, Helm releases and external data/backup
dependencies, then mark the Namespace annotation
`homelab.cooked.beer/decommission: approved` and remove all four prune protection
annotations. CI rejects that approval while consumers still target the namespace.
Observe that revision before removing the four resources in a later change.
Namespace deletion destroys namespaced data; the platform owner must accept the
inventory and retained backups first. Keep enrollment metadata through deletion.
The isolated rehearsal uses only disposable, empty storage fixtures.

The base default-deny declares both directions. Production Cilium configuration
is unchanged, including `policyEnforcementMode: never`; these NetworkPolicies
do not enforce production traffic restrictions under that setting.
The qualification uses Calico with working positive controls to prove sampled
TCP ingress and egress denial in the isolated cluster.

Flux behavior follows its [Kustomization documentation](https://fluxcd.io/flux/components/kustomize/kustomizations/).
