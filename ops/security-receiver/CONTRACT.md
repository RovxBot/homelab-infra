# Native Kubescape runtime event receiver — implementation PR 1

**Status:** code-only contract foundation. No receiver Deployment, Service, token, TLS listener, Helm change, or public incident write. Nothing here changes a live node-agent or the existing collector. This PR must NOT be represented as completed runtime ingestion.

## Exactly supported upstream behavior

- **Node agent**: [kubescape/node-agent v0.3.310, pkg/exporters/http_exporter.go](https://github.com/kubescape/node-agent/blob/v0.3.310/pkg/exporters/http_exporter.go).
  - `HTTPAlertsList`: JSON `kind: RuntimeAlerts`, `apiVersion: kubescape.io/v1`, `spec.alerts[]`.
  - `sendHTTPBody`: documented POST to `/v1/runtimealerts` by default (or configured `path`).
  - `tokenFile` support; bearer rotation must still be proved with the real projected token/TLS path.
  - HTTP body is **not** the stdout logger's event record.
- **Exact model dependency**: node-agent v0.3.310 `go.mod` pins [armosec/armoapi-go v0.0.742](https://github.com/armosec/armoapi-go/blob/v0.0.742/armotypes/runtimeincidents.go).
  - Embedded `BaseRuntimeAlert` and `RuntimeAlertK8sDetails` fields are flattened by Go JSON encoding. Examples: `ruleID`, `timestamp`, `severity`, `podUID`, `containerID`, `workloadUID`, `imageDigest`, `agentVersion`.
  - Not `RuleID`, `BaseRuntimeMetadata`, or `RuntimeK8sDetails` from stdout.
- **Helm**: pinned [kubescape-operator 1.42.0 node-agent ConfigMap template](https://github.com/kubescape/helm-charts/blob/kubescape-operator-1.42.0/charts/kubescape-operator/templates/node-agent/configmap.yaml) only renders `httpExporterConfig` under `$components.synchronizer.enabled`, whereas this homelab has Synchronizer disabled. **No undocumented Helm value or automatic Synchronizer activation is permitted.** Stage 2 must prove a maintainable, upstream-documented configuration method in a rendered pinned chart, or choose another officially supported exporter.

## What this code does

`scripts/security_receiver/native.py` is a pure and bounded adapter, not a server. The integration will call `normalize(body, VerifiedSource, TrustedPod, now)` only after it authenticates the sender and independently resolves the currently running Pod/Deployment identity.

The adapter:
- Refuses unrelated source service accounts and mismatched nodes, owners, Pod UID, container ID, image version and event timestamps.
- Rejects duplicate JSON keys, malformed/oversized messages and unsupported bulk format without falsely acknowledging part of a batch.
- Produces a small, allowlisted `SafeEvent`, discarding raw process args, raw messages, environmental data and arbitrary cloud metadata.
- Uses stable event IDs based on confirmed workload/container plus producer `uniqueID`; a retry has the same ID.
- Keeps the independently observed running image digest as the trusted identity. If the event reports another digest, records `digest_relation=different`; does **not** claim the values represent the same OCI artifact.
- Makes no network connections, API reads, disk writes, GitHub calls or other side effects.

Unit tests validate all of these boundaries against synthetic fixtures; the code is **not yet qualified against a captured authentic native HTTP request**, which is an explicit later canary requirement.

## Must be implemented before deployment

1. **Authentication**: a verified TLS endpoint and Kubernetes TokenReview using an audience agreed with the projected SA token; constrain the principal to node-agent SA and trusted bound Pod identity. Do not accept `VerifiedSource` from request headers or JSON.
2. **Inventory**: read independently from the Kubernetes API with minimal read-only permissions. Only the trusted Deployment→ReplicaSet→Pod chain may construct `TrustedPod`; handle stale/unavailable owner as explicit private, bounded quarantine rather than creating an incident.
3. **Durability**: single-writer SQLite on a protected PVC; commit allowlisted event, digest relation, replay receipt and logical incident before returning HTTP 2xx. Ensure overflow/storage failure returns a deliberate retryable failure. Never store raw payloads or overwrite the old production database.
4. **Native exporter**: prove exact source config, certificate validation, token renewal, response/retry semantics, no silently dropped alerts, rate limit/bulk mode. The current code intentionally rejects bulks; explicitly keep bulk **disabled** in the reviewed canary configuration.
5. **End-to-end**: a new, harmless R1000 is exported over authenticated HTTP from metal3/metal6, survives receiver restart, produces one private incident and replay receipt, with no auto-closure or GitHub writes.
6. **Gatus**: health/freshness of exporter, receiver and database with unknown/failure states, not a fake pass from a silent alert feed.

Only after these gates should the separate reviewed rollout PR expand **2 → 8 nodes directly**, then enable public-safe security issue publication and retire the legacy log reader. Existing [cleanup draft #2210](https://github.com/RovxBot/homelab-infra/pull/2210) stays unmerged until replacement acceptance. Preserve old `security-collector-state` PVC and historical SQLite until explicitly archived.

## Scope enforcement

This PR changes only isolated new code, synthetic tests and an independent CI workflow; its files are intentionally outside the old pinned `scripts/security_collector/**` image source manifest, avoiding unintended image churn. Existing Kyverno/PSA, Gatus, Renovate, Flux and Kubescape manifests remain unchanged. Production delivery and source authentication are **not implemented yet**.
