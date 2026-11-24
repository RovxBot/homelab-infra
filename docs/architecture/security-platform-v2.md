# Security Platform v2 — native Kubescape event delivery and lean incident lifecycle

**Status:** proposed architecture, review only. **No production migration or cleanup is authorised by this document.**  
**Owners/issues:** [epic #2132](https://github.com/RovxBot/homelab-infra/issues/2132), [runtime #2136](https://github.com/RovxBot/homelab-infra/issues/2136), [private incident bridge #2138](https://github.com/RovxBot/homelab-infra/issues/2138).  
**Prepared:** 24 November 2026.  
**Scope:** eight Talos nodes metal0, metal2, metal3, metal4, metal5, metal6, metal7, metal8.

## Non-negotiable upstream product contracts

The project must use each component **only through documented capabilities and configuration on the pinned version**. GitOps can supply documented configuration; it must not redefine how an upstream product works.

| Product | Supported responsibility | Explicitly prohibited |
|---|---|---|
| **Kubescape** | Official Helm installation, posture/CVE scans, node-agent eBPF detection, runtime rules, documented event exporters and API/CRDs | Forked node-agent, fabricated exporter endpoints/fields, unsupported Helm feature bypass, custom in-agent interception, undocumented configuration keys, claiming a digest match without proof |
| **Kyverno + PSA** | Kubernetes admission control, documented CEL policies, exceptions and policy-report workflows | Runtime threat detection, SIEM/event transport, second custom admission controller, silent fail-open |
| **Gatus** | Endpoint/control health checks and documented GitHub health alert lifecycle | Per-event ingest, CVE database, security finding dedupe, threat incident auto-resolution |
| **Renovate** | Documented dependency discovery, grouping, schedules, advisory/vulnerability alert PRs and manual reviews | Direct Kubescape finding ingestion without a documented Renovate integration, image scanner replacement, automatic remediation |
| **Flux** | Official HelmRelease, Kustomize, documented postRenderers, reconciliation and drift reporting | Untracked live `kubectl edit` config that Helm will overwrite, hidden imperatively managed chart internals |
| **Our integration** | Read documented exporter/API contracts; persist private evidence and publish carefully redacted incidents | Modifying upstream tools, assuming unsupported data contracts, publishing raw security payloads |

**Configuration is not modification of product behavior:** Kubescape explicitly documents its native stdout, Syslog, Alertmanager and HTTP exporters, including editing the node-agent `config.json` ConfigMap for HTTP delivery. Flux separately documents `HelmRelease.spec.postRenderers`. Both mechanisms are upstream-supported, but **their combination for chart 1.42.0 is not accepted until its rendered output is proven**. Helm chart source gates HTTP configuration on Synchronizer; do not invent a value that bypasses this gate or enable Synchronizer merely to carry our events. A minimal Helm post-render ConfigMap change is permitted *only* when it expresses Kubescape's documented node-agent exporter configuration, preserves all other chart-generated keys, is deterministic on chart upgrades, and passes explicit source/renderer tests. If this remains operationally brittle, use an independently supported Syslog/Alertmanager exporter via chart values or stdout feeding a standard supported forwarder. Never fork or mutate the node-agent binary to force delivery.

For every PR that touches a third-party component: cite the exact upstream documentation, chart/version, values/CRDs used, rendered result, and the boundary between native features and our integration. If a required behavior is **not** documented or provable, stop that path and pick a documented alternative.

References: [Kubescape runtime exporters](https://kubescape.io/docs/operator/runtime-threat-detection/), [Kubescape documented ConfigMap HTTP setup](https://kubescape.io/docs/operator/runtime-threat-routing-to-graylog/), [Flux HelmRelease post-renderers](https://fluxcd.io/flux/components/helm/helmreleases/), [Gatus alerting](https://gatus.io/docs/alerting-getting-started), [Kyverno ValidatingPolicy](https://kyverno.io/docs/policy-types/validating-policy/), [Renovate configuration](https://docs.renovatebot.com/configuration-options/).

## Decision

Use the **existing official Kubescape Helm deployment and its native node-agent runtime alert exporter**. Stop using Kubernetes Pod logs as the primary security event transport. Build **one small private event receiver** with a persistent SQLite store and a **separate, tightly restricted GitHub issue publisher**. Reuse existing normalization/incident/deduplication logic where proven; remove the custom log-polling engine and exact named agent-Pod enrollment after cutover.

No hosted Kubescape backend, Prometheus, Grafana, Loki, Alertmanager, alternate runtime detector or second admission engine. No Cilium enforcement enablement, automatic termination, policy relaxation, auto-merge or destructive remediation.

**Do not turn on the HTTP exporter by Helm value alone.** The upstream node-agent ConfigMap template has a chart-level gate that renders `httpExporterConfig` only when Synchronizer is enabled. Our deployment intentionally disables Synchronizer. The *packaged, pinned chart 1.42.0* must be rendered/tested to confirm its exact behavior. Select one supported minimal approach in the implementation PR:
1. Prefer a future or separately qualified chart-native opt-in independent of Synchronizer if available.
2. Otherwise verify the upstream-documented `config.json` HTTP exporter contract and implement that *documented configuration* through the officially supported Flux post-render mechanism **only if** it preserves the complete pinned Helm-generated config, is reproducible, and passes version-specific upgrade tests. A blind replacement or undocumented chart gate bypass is prohibited.
3. If that cannot be kept simple and maintainable, select the chart's documented Syslog/Alertmanager output or native stdout with a standard supported forwarder, and require an explicit threat-model review of auth/TLS/retries. Do not silently enable Synchronizer, change upstream binaries, or deploy a whole observability stack.

Source references to verify against *exact pinned artifacts*: [Kubescape runtime exporter documentation](https://kubescape.io/docs/operator/runtime-threat-detection/), [node-agent configuration](https://github.com/kubescape/node-agent/blob/main/docs/CONFIGURATION.md), [Helm ConfigMap template](https://github.com/kubescape/helm-charts/blob/main/charts/kubescape-operator/templates/node-agent/configmap.yaml), [node-agent v0.3.310 release](https://github.com/kubescape/node-agent/releases/tag/v0.3.310). Mainline documentation is not acceptance of pinned-version behavior.

## Target topology

```text
PREVENT:  Kyverno CEL + PSA  --------> admission; unchanged
DISCOVER: Kubescape scanner + storage + recurring assessments ----+
DETECT:   node-agent DaemonSet on all 8 eligible Talos nodes     |
                 |                                                |
         native exporter (authenticated HTTPS)                    |
                 v                                                |
         security-event-receiver (one ClusterIP service)          |
                 |                                                |
             SQLite/PVC <----- bounded, read-only CVE summaries --+
                 |
      independently scheduled security-issue-sync
             | allowlisted, redacted findings only
             v
          GitHub security incidents (source-owned)
HEALTH: Gatus + existing cluster-health / Kyverno issue owners
DELIVERY: Flux / GitHub & Gitea CI / manual Renovate updates
```

**Responsibilities / ownership:**

| Plane | Owner | Retain / change |
|---|---|---|
| Admission | Kyverno CEL and PSA | Keep and test all existing exceptions, fail-closed semantics and ordering |
| Posture/compliance + vulnerabilities | Kubescape operator, storage, scanner, Grype DB mirror and recurring scan jobs | Keep current scheduling, report provenance and storage; no second scan engine |
| Runtime detection | Official Kubescape node-agent | Keep pinned Talos/eBPF security context, rule binding and chart; expand to eight nodes only after transport canary |
| Event delivery | Native runtime HTTP exporter | Introduce authenticated HTTPS push to receiver; keep stdout only as bounded debugging fallback |
| Private events and incidents | One receiver + one SQLite PVC | Persist validated metadata, stable event receipts and deduped incidents; no GitHub credentials in receiver |
| Security GitHub publication | One narrowly permissioned periodic publisher | Separate process/credentials; publish sanitized summaries; no other security-event issue writer |
| Endpoint and control health | Gatus, pod-report, native Gatus issue sync | Keep: these are NOT runtime event transports |
| Kyverno findings | Existing Kyverno issue sync | Keep sole ownership; no parallel publication |
| CI/remediation | GitHub/Gitea scans, Renovate, Flux | Manual review/merge; only verified fixes, no auto-closure of threats |

### Why the previous approach failed

The R1000 alert was genuinely emitted on metal3 at 2026-11-23T10:31:32Z. The collector ingested none because its image digest equality check required that the event's digest equal `status.containerStatuses[].imageID`; the event supplied `73e8dcc5…`, while Kubernetes inventory and pinned spec reported `0f9552bb…`. Pod UID, container ID, Deployment UID, owner, node, timing and version tests passed. An OCI index/child-manifest relationship is plausible but **unproven** until registry content-addressed verification. The old collector then froze its cursor at 10:30:41Z and returned `invalid_input`; persistent runtime evidence and incidents stayed zero.

This is not a Kubescape detector failure. It is a self-imposed hard coupling between log replay and event attribution. The replacement should **not** require event image digest equality to make progress for unrelated observations, and must not equate incompatible digests as proof of same image without a verified relationship.

## Ingress contract and threat model

1. **No public ingress or external webhooks.** ClusterIP service only, but **do not claim ClusterIP alone isolates traffic**: Cilium policy enforcement stays intentionally disabled.
2. Require **TLS with a verified receiver identity** before sending bearer credentials. Supply and rotate a trustworthy service certificate and ensure node-agent clients actually trust its CA. Choose a concrete, tested issuance/CA-mount method using existing infrastructure or a minimal reviewed addition; do not silently add an ingress controller or certificate authority. If the pinned exporter cannot validate the receiver TLS certificate, stop.
3. Use node-agent v0.3.310's documented `tokenFile` support, not a hard-coded shared secret or token in a ConfigMap. Verify on the *pinned agent* that the exporter sends its projected token to the exact permitted host and rereads rotation. Server-side, perform Kubernetes TokenReview with the expected audience and require the **kubescape/node-agent** ServiceAccount and its trusted bound Pod identity where supplied. Restrict receiver RBAC to TokenReview plus the minimum independently queried inventory; never grant Secret access, Kubernetes write, pod log access or nodes/proxy. Do not reuse unrelated credentials or accept arbitrary service accounts.
4. Accept a single, documented fixed event endpoint with a bounded POST body, content type, schema versions, timestamp limits, maximum events/second, explicit 4xx/5xx semantics and request timeout. Reject oversized, malformed, unauthenticated and cross-cluster content; never print/process arbitrary process arguments or tokens into public surfaces.
5. **Persist before acknowledging success.** A successful HTTP response means the transaction has committed the receipt or typed quarantine record, not that a GitHub issue was created. Duplicate deliveries are idempotent. Unknown schema / unresolvable owner becomes a *bounded private unattributed record* and an explicit unhealthy/attention metric, rather than holding back all other events. Hard failures remain visible and cannot silently drop the whole stream.
6. The server independently resolves the workload's current Deployment -> ReplicaSet -> Pod chain, Pod UID, container ID, node, container name, and trusted running image identity from Kubernetes. Claims in the event are evidence, not ownership proof. If the event digest differs from running imageID, classify it as **mismatch / unverified relationship**; keep trusted Kubernetes imageID separately. Only enrich with a child-manifest/index identity after verifying the OCI parent/index and platform descriptor; do not add arbitrary digest aliases.
7. **Do not claim lossless delivery.** HTTP exporters may retry only within bounded memory and may drop alerts on restarts, 429, rate limits, or network failures. Verify the exact v0.3.310 request schema, response handling, retries/bulk behavior and any non-durable queue by code inspection plus interruption tests. Missing native delivery must surface as degraded/unknown health. Continuous zero-alert periods are not proof of functioning detection.

The receiver should be a small standard-library service if viable, not another general-purpose sensor, workflow engine or event bus. No OTEL collector is required merely to receive one Kubescape event format. Avoid adding a proxy/sidecar until transport security actually requires it and the cost is reviewed.

## Incident model

- **Runtime event receipt:** stable hash of authenticated sender/source, sensor event unique ID (if stable), rule ID and trusted Pod/container identity; duplicate POSTs return success without incrementing incident observations. Do not key only on ingest wall time.
- **Runtime incident:** stable source/category + rule + logical workload/container identity. Repeat occurrences update one private incident's count/first/last; a workload rollout should not fork the incident solely because its digest changed. Preserve old incident IDs on schema migration if possible.
- **Vulnerability backlog:** the existing Kubescape assessment stream, not runtime POST; group by affected workload/container, immutable *independently verified* digest and actionable fixes. Stale/missing reports are *unknown*, not clean. Never promise a recurring CI scan if it has not been deployed/verified.
- **Public-safe summary fields:** rule/CVE category, non-sensitive allowlisted namespace/workload, severity, first/last observed, count, state, bounded investigation/remediation guidance, internal evidence reference that reveals no URL secret or raw evidence. No process args, raw event JSON, tokens, env, arbitrary labels, commandlines, paths with secrets, private Gitea details or Pod/container UIDs.
- **Publication:** dedicated `security-issue-sync` uses a separate credential (SOPS, minimal GitHub issue permissions) and reads *normalized private summaries*, not raw event payloads. Dry-run by default until reviewed. It is the only writer for Kubescape security incidents. Cap number of issue creates/updates per hour/day; use backoff and durable receipts to avoid storms or missing retries. No automatic issue resolution on silence, reboot, missing scan, queue failure, clean assessment, or healthy Gatus.
- **Ownership boundaries:** Gatus remains endpoint/health issue owner, Kyverno sync remains policy-issue owner, runtime receiver owns threat incidents, CI/Renovate owns remediation proposals.

## Health, failure and capacity

Monitor distinct signals; do not report a single ambiguous 'green':

- Kubescape operator, scanner/storage/Grype cache, report recency and evidence coverage (existing flows).
- Agent DaemonSet **eligible nodes / ready agents / node identity**, not just desired DaemonSet count. Eight real nodes: metal0, metal2, metal3, metal4, metal5, metal6, metal7, metal8. Validate scheduling/taints/Talos capabilities before setting desired count to eight. Excluded nodes must be explicitly marked uncovered.
- Effective rendered rule binding, exporter configuration, TLS/token rotation, outbound successes/failures and receiver *authenticated accepted* events. A synthetic, benign R1000 test is the end-to-end acceptance; absence of future alerts is not a liveness test.
- Receiver cert expiry, TokenReview failures, invalid/quarantined events, SQLite health and headroom, queue/drop/backpressure, last authenticated receipt, publisher backlog and last successful GitHub sync. The service must return UNKNOWN/DEGRADED on stale data rather than HEALTHY.
- Bounded log and database retention; retain receipt identities as long as incident dedup promises require. Full disk/DB failure must reject acknowledgement and surface degraded state; never overwrite or silently evict evidence to keep green.
- Node-agent stdout remains accessible to an administrator for troubleshooting only; **no scheduled log scraping / private log transport**.
- Gatus checks health endpoints; `pod-report`/Kubernetes diagnostics provide node coverage without a new metrics stack. Preserve Gatus issue creation and Kyverno sync; do not add parallel issue creators.

## What stays, what goes

| Existing resource / code | Final action | Gate |
|---|---|---|
| `infra/kubescape/helmrelease.yaml`, namespace, HelmRepository, official operator/scanner/storage, Grype offline mirror | **Keep**; replace only two-node affinity, token/exporter wiring, relevant rule/namespace scope once tested | Rendered chart, Talos and admission requalification |
| `infra/kubescape/recurring/*`, existing scanner PVCs, assessment report history | **Keep** | Never purge historical assessments |
| `infra/kubescape/runtime/admission.yaml` (Kyverno named-agent boundary) | **Keep and adapt** exact DaemonSet/node affinity, TLS CA/token mounts and eight-node list | Negative/positive CEL tests across all eight; maintain restricted for all other Pods |
| Gatus, `pod-report`, `kyverno-github-issue-sync`, current GitOps/CI/Renovate | **Keep** with small new health checks only | Ownership/recovery regression tests |
| `scripts/security_collector/engine.py`, validated parts of `model.py`, `issues.py`, normalization and SQLite receipt design | **Reuse selectively**; simplify around native POST + database transactions | Receipt/incident/replay/privacy test suite |
| `scripts/security_collector/runtime.py` and log-specific `sources.py:log_path` | **Remove** after accepted cutover (entire timestamp-overlap/anchor/cursor polling path) | Native ingestion + rollback qualified |
| `scripts/security_collector/intake.py` agent-log cursor and source-observation machinery | **Remove or trim**; keep generic durable queue/receipt semantics only if needed | Additive SQLite schema migration and replay acceptance |
| `ops/security-collector/enroll-agents.py`, exact `node-agent-*` Pod-name/UID configuration, narrow `pods/log` collector Role | **Remove**; receiver uses authenticated source SA + independently verified workload inventory | No code/CI/Gatus refs remain; RBAC least-privilege diff |
| `scripts/tests/test_security_collector_overlap.py`, log-specific tests/fixtures, old per-agent cursor contracts | **Replace** with exporter HTTP request/authorization, persisted ACK, retry/dedup, TLS, identity mismatch, schema fuzz, webhook privacy, restarts and recovery tests | Full affected CI passes |
| `infra/kubescape/collector/*`, `ops/security-collector/production-image.json`, image build, Gatus collector health | **Replace/rename** to one receiver + one separately credentialed issue publisher with explicit image pins; reuse tested storage logic | Two-node live acceptance; no double writer |
| `ops/security-collector/acceptance/trigger` and test `kubescape-runtime-test` namespace, workload/RBAC and enrollment | **Remove deployed disposable resources** when native R1000 accepted. Versioned fixture may remain under tests; do not keep it in production Kustomization | Confirm new receiver receipt, private incident and replay dedup first |
| `infra/kubescape/collector/rbac.yaml` and config | **Remove old agent-specific log GET**; introduce only required TokenReview, read-only inventory and receiver/publisher rights | Privilege and SA-review audit |
| `security-collector-state` 256Mi Longhorn PVC and `/state/private/collector.sqlite` | **Retain as historical read-only evidence** pending operator-approved archival/migration. No automatic prune, reset, drop, or replace-in-place while live | Checksum snapshot, B2/Longhorn recovery proof, old receipt/incident inventory |
| Wiki Kubescape/runtime runbooks and `ops/security-collector/contract.md` | **Update** to reflect native exporter, authenticated receiver, honest delivery limits and rollback; mark old log-poller guidance historical | Merge with cutover |

A versioned disposable test fixture can remain as source-controlled test infrastructure without being an active Flux resource. Do not delete historical provenance, published issue references, or acceptance evidence merely because an implementation is retired.

### Dependencies that must NOT be accidentally deleted

The node-agent's **nodes/proxy** permissions and eBPF host mounts are a different data plane than the collector's log-reader **pods/log** permission. Keep the former if still required for runtime detection, even after removing the latter. Keep scanner/storage PVCs, Kyverno policy exceptions and Flux dependencies. Avoid weakening the rule binding, namespaces, and scanner cadence as collateral cleanup.

## Execution sequence (reviewed GitOps PRs, no gap in monitoring)

**PR 1 — Design/contract and preflight (this proposal).** No production changes. Render exact chart 1.42.0 / agent v0.3.310, prove exporter payload/schema, enabling without Synchronizer, authenticated HTTPS/TLS/TokenReview, retry/failure behavior and expected resource footprint. Choose minimum safe chart integration. If any cannot be proven, **stop instead of building unsupported assumptions**.

**PR 2 — Receiver implementation, off by default.** Add isolated one-replica private receiver, dedicated read-only identity API rights, TokenReview permission, bounded database PVC, schema and unit/integration tests. Implement an additive migration or independently preserved legacy evidence snapshot. No GitHub writer token, no exporter change, no Pod-log reads by this service. Validate malformed events, trust boundaries, HTTP 2xx only after commit, duplicates, backpressure, TLS rotation and reboot.

**PR 3 — Native two-node canary and acceptance.** Keep metal3/metal6 only; change official node-agent exporter configuration and Kyverno guard in one reviewed transaction, preserving stdout as backup diagnostics. Prove exact two Ready agents, client TLS verification and SA token rotation, one harmless R1000 delivered over authenticated POST, normalized private receipt/incident, no duplicate on replay, and reasoned failure on receiver interruption. No unknown coverage or false-positive suppression by convenience. The previously observed 2026-11-23 log-only R1000 is **historical detector proof**, not a POST delivery acceptance; do not fabricate a delivered receipt.

**PR 4 — Single eight-node expansion.** After two-node acceptance only, extend agent DaemonSet scheduling and rule/admission guards in one manually merged **2 -> 8** change (no intermediate four-node phase). Verify host capabilities, scheduling, resource budgets, agent readiness, rule configuration and native exporter on every named node. Demonstrate at least one safe detection per representative previously uncovered node and receiver node attribution; if all eight can be safely tested, record that. Do not equate Ready with end-to-end delivery.

**PR 5 — Publication / vulnerability issue lifecycle.** Enable publisher only with reviewed private storage boundary and SOPS GitHub credential. Start with sanitized dry-run and public-template tests. Prove one redacted runtime incident, replay updates (no duplicate), one grouped actionable vulnerability case, rate limit and API outage recovery. Preserve existing Gatus/Kyverno issue owners; do not close threat incidents on silence.

**PR 6 — Retirement and cleanup.** First stop old collector writes and snapshot/checksum historical SQLite. Replace legacy Gatus endpoints only after replacement health is green. Remove log reader source, Pod-log RBAC, agent exact pins, overlap tests, unused CI/helm inputs, stale wiki pages, and disposable R1000 test workload + namespace + RBAC. Keep old Longhorn PVC **unpruned and recoverable** until separate approved retention/export decision. Resolve draft cleanup PR #2210 as superseded by this complete cutover, rather than merging it ahead of acceptance. Confirm no orphan services, ConfigMaps, Roles, RoleBindings, Jobs, PVC consumers, or old Pods; continue to retain scanner CRDs/storage. Verify Flux 27/27 (or current expected count), Gatus freshness and no double issue ownership.

Stages may be combined into fewer implementation PRs only when independent gates remain observable. **Do not merge any of PRs 2–6 automatically; the design PR has no effect on the cluster.**

## Acceptance matrix

- [ ] Exact packaged 1.42.0 chart and pinned v0.3.310 agent prove the selected exporter is supported and installed with Synchronizer disabled.
- [ ] Validating TLS and per-agent ServiceAccount authentication; failed CA, expired/unrecognised token, wrong audience, wrong SA, wrong sender, forged identity and replayed/untrusted requests are denied.
- [ ] Legitimate image-index/manifest mismatch is not silently discarded, not falsely treated as matching, and does not block later good alerts.
- [ ] Two-node R1000 native POST gives one durable private receipt and one incident; identical retry/restart does not increment, distinct occurrence increments the same incident.
- [ ] Producer/receiver disconnection, rate limit, queue full, storage full, certificate renewal and agent restart surface known limits/gaps. Nobody claims lossless delivery.
- [ ] All eight listed Talos nodes have intended agent/eligible node coverage and independently observed exporter health; excluded nodes explicit.
- [ ] Kubescape vulnerability and posture assessment pipeline remains healthy; freshness/coverage and private vulnerability grouping verified.
- [ ] Gatus and Kyverno issue owners retain creation and closure behavior; publisher writes only bounded, redacted security summaries with explicit approved enablement; no raw payload crosses to public GitHub.
- [ ] CI/digest provenance and Renovate Monday grouping, urgent security PR behavior, manual merge remain unchanged.
- [ ] No unintended PSA/Kyverno admission relaxation, Cilium policy mode change, destructive remediation, automated issue closure, secrets exposure or unnecessary stack installation.
- [ ] Old log polling/UID-enrollment and test resources removed from active GitOps, but existing security-collector PVC and historical evidence archived/preserved; clean rollback and forward recovery rehearsed.

## Decisions NOT yet proven

1. **Exporter template:** exact chart 1.42.0 may gate HTTP on Synchronizer; inspect the packaged chart and test minimal override. Do not rely on documentation for newer chart branches.
2. **Transport security:** choose concrete TLS certificate/CA distribution and audience-bound token verification; verify the node-agent actually enforces TLS and rotates tokenFile on the pinned version.
3. **Delivery semantics:** inspect native exporter request framing, retry/delivery behavior, loss/queue limits and what a successful response means. The sender may not have durable disk buffering. Document and monitor the limitation; do not promise a lossless stream.
4. **Native event format:** capture a controlled, sanitized pinned-agent HTTP fixture and exercise parser. Do not assume stdout JSON equals HTTP request body.
5. **Storage migration:** inspect legacy `security-collector-state` contents under read-only access before deciding whether to import existing incidents, or preserve separately as archival state. Never truncate production SQLite.
6. **Actual eight-node eligibility:** node-agent can run only where Talos kernel/eBPF, workload scheduling and host-access controls are compatible. Review each node and enforce intended node placement.
7. **Vulnerability publisher:** existing scanner reports exist, but the proposed public issue bridge has not yet been qualified with a live end-to-end case.

**Rollback:** disable native exporter first while retaining stdout and node-agent detection, roll receiver/publisher back through Flux, preserve both new and legacy PVCs and private evidence, then re-enable only after trust/identity/cert fixes. Never unwind the Kubescape Kyverno/PSA guard before the node-agent privilege requirement is removed. Never auto-close historical threat incidents.
