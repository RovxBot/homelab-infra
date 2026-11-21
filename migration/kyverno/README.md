# Kyverno deployment qualification (draft)

**Not deployable yet:** restart recovery fails for fresh CEL generators on both
releases. The empty-state cutover mechanics passed, but the complete generator
path and rollback did not. See the [Task 17 qualification](evidence/task17/qualification.json),
[findings](evidence/task17/findings.md), [activation/rollback proposal](activation/README.md)
and [exception decisions](activation/exception-review.md).

No production Kustomization references these policy manifests. The production
inventory was refreshed read-only; no production mutation, deployment, merge,
legacy acknowledgement bypass or Renovate unpin occurred. #2133 remains open.

The unchanged validation contracts reuse successful run
[44879145011](https://github.com/RovxBot/homelab-infra/actions/runs/44879145011):
596 actual decisions per engine, both runner suites, native governance and
representative background/Audit reports. `evidence/validation-contract.json` pins
the exact input and decision hashes and target release identity. Reuse fails if
those inputs or the verified chart/application pair change. Proposed future
releases run the actual old/new admission contracts instead.

Run [deployment qualification](../../.github/workflows/kyverno-cel-rehearsal.yml)
manually on the implementation branch. Its default chart is the explicit Task 17
target, 3.10.0. Leave `scenarios` empty for full qualification. A JSON subset such
as `["empty-upgraded"]` runs a bounded diagnostic and **cannot** qualify deployment.
The required scenarios are empty/upgraded generation, fresh target generation,
mutation and validation; adoption is optional diagnostic evidence.

The reusable [compatibility workflow](../../.github/workflows/kyverno-compatibility.yml)
reads the proposed HelmRelease chart when called without a chart override. Repo
Checks passes the actual proposed chart for chart/controller image changes. The
resolver verifies the Helm index/chart metadata, release assets, source commit,
signed CLI and exact executed CLI version. New charts require signed release
bundles; only the immutable historical chart 3.9.2 has an explicit documented
pre-bundle exception. Runtime candidates use signature/provenance-verified image
digests. Unknown artifacts fail without falling back to an older version. An
application major change requires a reviewed migration contract; compatible minor
and patch versions have no fixed ceiling.

Every scenario runs on disposable hosted kind v0.30.0/Kubernetes v1.34.0 with
Calico v3.31.0, Helm v3.19.0 and committed Flux v2.9.0. The scenario has a 25-minute
ceiling and the job 30 minutes. Teardown runs even after failure. No production
kubeconfig or Flux sync is imported. Continuous watches, independent explicit
NotFound/unavailable snapshots, triggers, policies, URs, readiness and relevant
controller logs are retained across restart and upgrade. Sampled traffic probes
include working positive controls; object watches alone do not prove enforcement.

To reproduce one scenario in a fresh disposable Linux Docker environment with
Python/PyYAML 6.0.3:

```sh
KYVERNO_CANDIDATE_CHART=3.10.0 bash migration/kyverno/harness/trust.sh
# Continue only if verification succeeded.
KYVERNO_REHEARSAL_LOCAL=1 CANDIDATE_TRUST=success \
  REHEARSAL_SCENARIO=mutation timeout 1500 bash migration/kyverno/harness/run.sh
```

`GH_TOKEN` may supply a read-only GitHub token for release metadata rate limits;
it is sent only to api.github.com, never written to evidence. Sigstore retains
official TUF/certificate/transparency checks. See historical trust evidence for
the verifier bootstrap boundary. Do not reuse the task's kubeconfig or an existing
cluster. New runs are manual to keep CI expenditure bounded.

Regenerate policy manifests with `harness/build.py`, then report aliases with
`harness/render-report-identities.py`. Review the diff. No exception scope/date is
changed. `activation/prepare.py` creates review-only sequential Git patches; it
never applies them or contacts Kubernetes.
