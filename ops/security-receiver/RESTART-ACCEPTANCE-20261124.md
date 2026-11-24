# Native receiver Longhorn restart acceptance — 24 November 2026

**Scope:** one GitOps-managed receiver Pod restart only, following PR #2216's
first valid native R1000. **Not a node-agent restart, R1000 re-trigger,
SQLite migration, PVC replacement, collector change or cluster-wide rollout.**

## Accepted pre-restart evidence

The operator verified the private receiver SQLite database *read-only*:

- receipts: **1**
- incidents: **1**
- stored rule: **R1000**
- stored source node: **metal3**
- stored workload: **home/kubescape-runtime-test/Deployment/runtime-acceptance/test**
- stored event observed_at: **2026-11-24T04:43:52.664665976Z**

Kubescape's native exporter detected the genuine one-shot test and the receiver
committed one safe normalized event plus one logical incident to its dedicated
Longhorn claim `kubescape/security-receiver-state`. The receiver
`/healthz` returned `receiver_ready: true`, receipts=1, incidents=1.
`delivery_coverage: "unverified"` is expected; it does not mean a valid
receipt was not persisted.

## Source-controlled one-time restart

This PR changes **only** the receiver Deployment Pod-template annotation
`homelab.cooked.beer/persistence-acceptance: "r1000-receipt-20261124-01"`
(and regression test and this runbook). The existing Deployment remains:

- One replica, **Recreate** rollout with a brief planned HTTPS interruption
- Same immutable GHCR receiver image and non-root Pod security
- Same dedicated Longhorn RWO PVC; `kustomize.toolkit.fluxcd.io/prune: disabled`
- Same namespace, TLS identity, RBAC, Service and receiver settings
- Old log collector and its independent PVC left untouched
- Only metal3 and metal6 remain in the native node-agent canary

A brief outage is expected for the single-replica receiver. This is an
acceptance exercise, not an HA test; do not generate new runtime test alerts
while it is replacing the Pod. Automatic delivery retries must not be
assumed to be lossless on agent restart.

**Never use `kubectl rollout restart`, `kubectl delete pod` or patch the
live Flux-managed Deployment by hand.** Merge this reviewed PR and let Flux
reconcile it.

## Operator acceptance

### 1. Before merge — baseline evidence

```sh
kubectl -n kubescape get pods -l app.kubernetes.io/name=security-receiver -o wide
kubectl -n kubescape get pvc security-receiver-state -o wide
kubectl -n kubescape get deploy security-receiver
```

Save the Pod name and bound PVC identity. Confirm the SQLite evidence again
using the read-only query below before the merge if necessary.

### 2. Merge reviewed PR, then verify only the receiver rotated

```sh
kubectl -n flux-system get kustomization kubescape
kubectl -n kubescape rollout status deployment/security-receiver --timeout=180s
kubectl -n kubescape get pods -l app.kubernetes.io/name=security-receiver -o wide
kubectl -n kubescape get pvc security-receiver-state -o wide
kubectl -n kubescape get deploy security-receiver
kubectl -n kubescape get daemonset node-agent
kubectl -n kubescape get deployment security-collector
```

Requirements: a **new receiver Pod name** with 1/1 Ready and zero restarts;
unchanged **Bound** `security-receiver-state` PVC (same volume), agent
DaemonSet 2/2 Ready, legacy collector Ready and Flux Ready at the new revision.
The receiver must not start a second writer on the SQLite database.

### 3. Read-only receipt and incident verification

Run the following against the newly Ready receiver. It does **not** query
secrets, print raw event data or read bearer tokens:

```sh
kubectl -n kubescape exec -i deployment/security-receiver \
  -c receiver -- python3 - <<'PY'
import json
import sqlite3

db = sqlite3.connect("file:/state/receiver.sqlite?mode=ro", uri=True)
db.execute("PRAGMA query_only=ON")
receipts = db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
incidents = db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
rows = db.execute("SELECT data FROM receipts").fetchall()
expected = {
    "rule": "R1000",
    "node": "metal3",
    "workload_key": "home/kubescape-runtime-test/Deployment/runtime-acceptance/test",
    "observed_at": "2026-11-24T04:43:52.664665976Z",
}
matches = sum(
    all(json.loads(row[0]).get(k) == v for k, v in expected.items())
    for row in rows
)
print(json.dumps({
    "database_quick_check": db.execute("PRAGMA quick_check").fetchone()[0],
    "receipts": receipts,
    "incidents": incidents,
    "original_r1000_receipt_preserved": matches == 1,
}, indent=2))
db.close()
PY
```

Expected: `database_quick_check="ok"`,
`original_r1000_receipt_preserved=true`, `receipts>=1`,
`incidents>=1`, and ideally unchanged 1/1 without new events.
Then verify authenticated HTTPS `/healthz` with the operator's **public**
`.work/security-receiver-pki/root.crt` as in #2216; TLS hostname validation
must still succeed. A health request alone is not a proof of receipt persistence.

### 4. Do not overstate replay acceptance

The existing `scripts/tests/test_security_receiver_service.py` test suite
already exercises duplicate-event idempotency and restart persistence
**offline**, using the real EventStore. A live second *fresh* R1000 is **not**
an identical-event replay and must not be reported as such.

Do **not** extract agent bearer tokens or raw Kubescape HTTP payloads just to
force replay. If we want an end-to-end live duplicate test, implement a
reviewed, narrowly scoped sender fixture that proves the same event ID is
redelivered without exposing private data, with a rollback plan.

## Rollback and further release gates

On rollout failure, inspect Pod/Longhorn/Flux state without exposing decrypted
TLS material. Revert only the receiver annotation through GitOps; **never
remove or recreate the PVC**. Leave node-agent, collector, Kyverno, Gatus and
Cilium as-is.

After this test, record the evidence in #2216 and consider the next reviewed
eight-node rollout separately. Native delivery is not yet declared lossless;
the existing collector and its state must remain until all downstream
incident and safety gates have passed.
