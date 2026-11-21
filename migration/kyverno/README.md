# Isolated CEL rehearsal (draft)

This implementation reuses [the preflight contracts](../../docs/kyverno/migration-plan.md)
from commit `117ad3913928195a5365011d96f18fb241e639d9`. These manifests have no
production Kustomization entrypoint. Qualification results and remaining activation
gates are recorded in [evidence/qualification.json](evidence/qualification.json).

## Reproduce

The [Actions workflow](../../.github/workflows/kyverno-cel-rehearsal.yml) runs on an
ephemeral GitHub-hosted Ubuntu runner. Re-run the recorded validation job at its
tested commit with:

```sh
gh run rerun 44879145011 --repo RovxBot/homelab-infra
gh run download 44879145011 --repo RovxBot/homelab-infra \
  --name kyverno-cel-trust --dir /tmp/kyverno-cel-evidence
```

Wait for the bounded run to complete before downloading its artifacts. The job
has a 35-minute ceiling and the rehearsal has a 25-minute ceiling. Every Kubernetes
call selects the task's fresh kind context. The shell trap and an unconditional
workflow step delete the cluster, including on failure.

For a fresh disposable Linux Docker environment, install Python with
`PyYAML==6.0.3`, then run from a clean checkout of the tested commit:

```sh
bash migration/kyverno/harness/trust.sh
CANDIDATE_TRUST=success KYVERNO_REHEARSAL_LOCAL=1 \
  timeout 1500 bash migration/kyverno/harness/run.sh
```

Run the second command only after `trust.sh` succeeds. The harness downloads exact
kind v0.30.0, Kubernetes v1.34.0 and Helm CLI v3.19.0 artifacts and installs the
committed Flux v2.9.0 components. It tests Kyverno 1.19.2/chart 3.9.2 followed by
1.20.0/chart 3.10.0. Candidate runtime images use the digests verified by Sigstore.
It never applies `gotk-sync.yaml` or imports an existing kubeconfig.

## Evidence and scope

The uploaded artifact contains raw admission decisions, comparison differences,
policy status, governance decisions, actual report results, controller logs and
teardown output. The trust directory contains certificate-identity signature and
SLSA verification outputs using the official Sigstore trusted roots. Committed
evidence records the source runs and artifact hashes.

Generator tests are disabled by default after the recorded upgrade/restart
failure. [generator-lifecycle.json](evidence/generator-lifecycle.json) preserves
the successful controlled handoff and the unresolved failure. The optional
`generators` workflow input (or `REHEARSAL_GENERATORS=true` locally) retains the
reproducer for a separately reviewed investigation; it is not an activation gate
waiver. Empty-output cutover and rollback remain unqualified.

Policy generation is deterministic: run `python3 migration/kyverno/harness/build.py`
and review the resulting diff. `policy-map.json` maps each authored CEL validation
to its original rule. Native exception-accountability admission is separate from
Kyverno's webhook so its own controller can bootstrap. Historical exception dates
and scopes are retained; [expired-exceptions.json](evidence/expired-exceptions.json)
flags the five dates requiring owner review.
