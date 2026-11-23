# Stage A collector contract (#2138)

This is an implementation/review contract, not a deployed runbook. Maintained operation belongs on the [Kubescape Wiki](https://github.com/RovxBot/homelab-infra/wiki/Kubescape). `wiki-proposal.patch` is a security-sensitive proposal against that separate repository, retained for review rather than published as accepted architecture. Its pinned Wiki base is in `wiki-base.txt`; check/apply it in that checkout with `git apply --unidiff-zero --check` / `git apply --unidiff-zero`.

## Entry points

- `scripts/security_collector/adapters.py`: `EventAdapter.read(bytes, Deployment, now)` returns one immutable `Observation`. `RuntimeStderrAdapter` consumes one JSON line from the pinned exporter. `VulnerabilityAdapter(AssessmentContext)` consumes a private `{report, sbom, summary}` bundle. Neither adapter fetches data or authenticates a sender. These are private caller interfaces, not network endpoints.
- `model.py`: validated source/category, rule/advisory IDs, workload/container, optional image digest, severity, observation ID/time, provenance, confidence, freshness, remediation and qualified-clean assessment. `Deployment` and `AssessmentContext` must come from independent trusted readers; accepting them from an event sender would defeat the recovery checks.
- `engine.py`: `Collector.deployment`, `ingest`, `telemetry`, `refresh`, and trusted-operator `disposition`. Transactions commit sanitized state and replay receipts together. Raw input is transient and never saved. First/last observation, occurrence/assessment count and a bounded evidence-change history belong to each incident. `telemetry` must be called on reader/qualification failures; source silence never implies remediation.
- `issues.py`: explicit internal-workload-to-`Publication` mapping is required. The mapping, public aliases, remediation issue and related issue numbers are reviewed configuration. No mappings ship enabled. `Delivery` defaults to `enabled=False` and `allow_closure=False`. There is **no production GitHub client, deployment, receiver, credential configuration or network-enabled CLI** in Stage A. Tests alone inject a mock and enable delivery.

## Identity and recovery

Runtime identity is SHA-256(source, category, logical cluster/namespace/kind/workload/container, rule). Vulnerability identity substitutes `image-backlog` for the rule: CVEs and image digests update the same backlog. Controller resolution uses supplied independent inventory, never guessed pod-name suffixes. Logical workload recreation retains the incident; deployed generation/digest and evidence snapshots show changes.

Runtime replay identity hashes workload/rule plus upstream uniqueID, falling back to the full nanosecond timestamp/container ID/PID. It never persists the raw identifier or process data. Without a uniqueID, exactly identical fallback tuples collapse; this is not a claim of a lossless transport. Vulnerability replay identity hashes workload/digest/report timestamp/locked DB checksum. Report mutation without a new assessment timestamp is not new assessment evidence.

Runtime incidents require operator investigation. `acknowledged` keeps the incident unresolved. Trusted `false_positive`, `investigated`, or `accepted_risk` disposition requires a reviewed repository issue number; a new observation reopens the incident, while exact replay preserves disposition. Event fields cannot invoke disposition. New digest/generation invalidates vulnerability recovery. A clean assessment resolves only with ready report/SBOM, valid manifest/config identity hashes, summary workload/container/ref attribution, matching locked DB, post-deployment report, current generation, fresh independent inventory, healthy latest attempt and complete coverage. Report age is at most 72 hours and DB age at most seven days; inventory expires after one hour. Expired/missing inventory or failed/stale telemetry reopens assessment-based resolution. Following a gap, fresh qualification is required; replay alone does not restore resolution. Unsupported/malformed reports must be surfaced as unsupported/unknown, never empty success.

Admission/policy findings and service-health recovery cannot resolve these incidents. Gatus, Kyverno issue sync, Renovate/CI and Flux retain their existing ownership and code.

## Bounds and delivery

| State/action | Default bound |
| --- | --- |
| Runtime JSON / assessment bundle | 1 MiB / 4 MiB |
| CVE/GHSA IDs per complete assessment | 5,000; reject rather than truncate qualification |
| Incident records, including resolved keys / inventory entries | 200 / 200; saturation rejects new entries |
| Replay receipts / acceptance window | 20,000 / seven days; reject saturation, never evict live receipts |
| Accepted observations | 1,000 per rolling hour, persisted across restart |
| Evidence snapshots / public IDs | Latest 32 per incident / first 50 plus full count |
| GitHub requests / writes / creates | 60 per hour / 10 per hour and 20 per day / three per day |
| Delivery backoff | 60 seconds initially, exponential to one hour; persisted |

Normalized state uses a private mode-0600 SQLite file and one writer. Resolved keys are bounded tombstones, not automatically deleted: archival/retirement at capacity requires a future reviewed policy. Receipt and quota retention is enforced on ingestion/reconciliation; SQLite reuses freed pages. No raw-retention subsystem exists in Stage A. The proposed separate private spool is a later integration gate.

Only confirmed High/Critical evidence initially qualifies for publication; existing tracked incidents receive updates, including reassessment/unknown state. Broader exploitability/baseline selection is pending review, not inferred from a zero-match report. Unknown advisory families outside CVE/GHSA reject the whole assessment. Public summaries include only typed identifiers/digests, reviewed workload aliases, canonical times/counts, fixed investigation text and configured same-repository issue numbers. They exclude raw names/descriptions, package versions/paths, command lines, environment, payload URLs and source-provided remediation text. Scanner fix availability is labelled unreviewed applicability; reviewed remediation is a separately configured issue link. No private Gitea source or evidence links are accepted.

The GitHub interface requires complete lookup across open/closed **collector-owned** issues and optimistic concurrency for updates. Human text before/after the owned marker is preserved. Missing/ambiguous markers fail closed. Persisted create intent fences a crash/lost response. After an uncertain create, lookup may recover an existing issue, but an empty lookup never authorizes another create. No automatic fence-clearing API exists. Retries are for lookups/idempotent updates only. Exceptions are never copied into persisted state or public output. Stage B must implement and qualify these guarantees against GitHub before any writes are enabled.

## Local validation

```sh
python3 -m unittest discover -s scripts/tests -p test_security_collector.py -v
python3 -m compileall -q scripts/security_collector scripts/tests/test_security_collector.py
```

The suite exercises adapters → durable state → renderer → mocked GitHub, including restart/outage/ambiguous-write and disabled rollback paths. Fixtures are synthetic: R1000 shape and severity follow the [accepted canary](https://github.com/RovxBot/homelab-infra/issues/2136#issuecomment-7087742825) and pinned exporter/types; assessment envelope follows the existing qualified runner and live field-name checks, with positive matches from the [Grype contract](https://github.com/anchore/grype/blob/v0.121.0/grype/presenter/models/match.go). They are not retained production logs or fresh scanner acceptance. Source/pin observations and exact missing integrations are in `compatibility.json`. Existing Repo Checks automatically discovers `test_security_collector.py` for scripts changes; no scanning schedule or production CI credential is added.
