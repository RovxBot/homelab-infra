# Memos image assessment — 22 November 2026

Tracking [#2135](https://github.com/RovxBot/homelab-infra/issues/2135) stays open.
This phase extends the merged posture baseline at `10520c0f` without changing
chart 1.42.0, admission enforcement, Memos or Cilium.

## Qualification

Verified locally: exact 61-object render, 48 Kyverno CEL passes without failures,
errors, skips or warnings; live server-side admission dry-runs accept the render.
Additional objects over the 47-object posture baseline: two Deployments, two
Services, two ServiceAccounts, one PVC, one ConfigMap, one CronJob, one
ClusterRole, two ClusterRoleBindings, one Role and one RoleBinding. Node/host
collectors, external reporting and automatic image routing remain disabled.

Source reviewed at immutable tags: Kubevuln v0.3.578 commit
`45d265e3b46af4d7fc1c896ecc22a93915f2d6fe`, operator v0.2.209 commit
`09a2e5030d4b79b727830763e59e644ae5b3a74a`. Kubevuln's go.mod pins Grype
v0.121.0 and Syft v1.55.0. The [pinned operator watcher implementation](https://github.com/kubescape/operator/blob/09a2e5030d4b79b727830763e59e644ae5b3a74a/mainhandler/handlerequests.go)
starts image watchers independently of continuous posture scanning. The explicit
operator component override prevents startup, Pod, SBOM and ContainerProfile
image triggers. Direct scan API authorization remains mandatory.

## Database provenance and freshness

| Artifact | Verified identity |
| --- | --- |
| Official distribution | `quay.io/kubescape/grype-offline-db` |
| OCI index SHA-256 | `9dd3b2eb49d80612866cfb13f7da0038fe9e3d4108784881de9a7b554a29815a` |
| amd64 manifest | `sha256:71ec8c69b03677717a126ccf78cd28453e0a226886c44ba055000afb4c088eca` |
| DB layer SHA-256 | `553cbb4b2a278626a1db1713f13b3b322eb040f688be9a360ee02e23bef97599` |
| Image creation | `2026-11-21T01:05:33.753515764Z` |
| Database build | `2026-11-20T06:21:00Z` |
| Schema | `v6.1.10` |
| Archive | `vulnerability-db_v6.1.10_2026-11-20T00:20:17Z_1795155660.tar.zst` |
| Archive SHA-256 | `6866d1685025e6001d422fb4be59f26feebacb151b3ff77b012d999e12b9af24` |
| Archive / expanded SQLite | 205,235,440 / 2,679,586,816 bytes |

Downloaded registry index/platform manifest, config and every layer; content
hashes verified, archive bytes match mirror `latest.json`, SQLite `db_metadata`
confirms build/schema and `PRAGMA quick_check` returns `ok`. Registry TLS and
content addressing establish Kubescape distribution integrity. No independent
signature or byte-for-byte match to the original Anchore archive was established.
Do not infer that trust from a checksum distributed in the same mirror.

The [publisher index](https://grype.anchore.io/databases/v6/latest.json), read on
22 November, reports build `2026-11-21T06:20:06Z`, archive SHA-256
`921d1b1b4151a211d39efbb8a7da54caabfc6eb717112766cb5bdee7920809b5`.
The selected mirror is **one daily edition behind**, approximately 42 hours old
at qualification, within the explicit 72-hour limit for this bounded canary.
It is a recent snapshot, not the newest publisher database. Direct workstation
connections to both Anchore database hosts failed; the publisher metadata was
independently retrieved through web access. The [runbook](README.md#freshness-and-updates)
requires a new age/integrity check before every future assessment and a reviewed
immutable update; neither a cache nor healthy readiness proves freshness.

The pinned [Kubevuln DB loader](https://github.com/kubescape/kubevuln/blob/45d265e3b46af4d7fc1c896ecc22a93915f2d6fe/adapters/v1/grype.go)
uses the configured local listing URL and rechecks it after 24 hours. Its
installation configuration does not supply Grype age/checksum validation flags;
we therefore require explicit external integrity/freshness evidence. DB readiness
and successful scan completion alone cannot meet that requirement.

## Production acceptance — verified

Activation [PR #2179](https://github.com/RovxBot/homelab-infra/pull/2179) merged at
`dd962f42d802b3c75f2f74bc6e5fff51b67f3fd5` after applicable CI passed. One initial
schema check failed downloading the remote RoleBinding schema; the failed job's
retry passed. The final permission change also passed its complete CI run.
Flux deployed Helm revision **2** on the same chart. Upgrade started
`2026-11-21T23:43:16Z`; release Ready at `23:50:22Z`. All five Deployments are 1/1,
zero restarts; runtime imageIDs match every immutable image pin. Actual restricted
Pods and ServiceAccounts ran successfully. Additional scheduling uses the chart's
Linux selector with no affinity, priority or taint exceptions: engine on metal4,
mirror metal6, posture scanner metal0, operator metal5, storage metal2. Application
workload scheduling was unchanged.

Cold Grype loading ran **23:44:10–23:50:13Z (6 minutes 3 seconds)**. Readiness
remained false until loading finished. The authenticated diagnostics endpoint
then reported in-process Syft v1.55.0, Grype v0.121.0-matching-off, persistence
true and database checksum `6866d168…b9af24`, matching the inspected archive.
The persisted report independently records the full checksum, local URL,
`v6.1.10` schema and `2026-11-20T06:21:00Z` build. Its database age at the scan
was **41.49 hours**. Engine `valid: true` is recorded, but the independent age
and integrity checks remain required.

The mirror's content-addressed BuildKit attestation identifies source
`kubescape/kubevuln`, revision `8a037119b8af6dbb2bd15a02bfa5c18f73d88b76`,
`build/Dockerfile.offline-db`, and build time 21 November 01:05 UTC. The
[referenced Dockerfile](https://github.com/kubescape/kubevuln/blob/8a037119b8af6dbb2bd15a02bfa5c18f73d88b76/build/Dockerfile.offline-db)
downloads Anchore's v6 index/archive. This is content-linked provenance, not an
independently authenticated signature. Internal SQLite records **27 providers**,
including NVD, Alpine, GitHub, KEV and EPSS, with capture times on 20 November
00:20–01:12 UTC. No claim that every provider has equal advisory coverage follows.

### Canary identity and timestamps

Times here are UTC. The scan ran **22 November 2026, 10:20:37–10:20:55 Adelaide**.
The [machine-readable evidence](image-canary-evidence.json) preserves full IDs,
request, diagnostics, fix fields and canonical SHA-256 hashes of persisted specs.

| Evidence | Verified value |
| --- | --- |
| Request job ID | `memos-image-canary-task23-001` |
| Endpoint / terminal status | authenticated `/v1/scanImage`; `scanCVE`, `succeeded`, phase `completed` |
| Start / finish | `2026-11-21T23:50:37.735099721Z` / `23:50:55.037927558Z` |
| Duration | **17.302828 seconds** |
| SBOM / vulnerability report time | `23:50:44Z` / `23:50:54Z` |
| Scan ID | `651a63b86be994e4cff4cc696ac9ae87fcf1ebc66d7c1213b54d2fd546b15ce6` |
| Namespace / UID | `memos` / `3eb9baf6-0f47-46ab-9d4e-c089f59d0953` |
| Deployment UID / resourceVersion | `1e1e25ca-4b09-4d6e-98f5-afd3540fc866` / `188602150` |
| Pod / UID | `memos-7486b87cdd-9988x` / `84bb6780-6883-4dd2-8acd-40b16d02488d` |
| Runtime node / container | `metal8`, linux/amd64 / `memos` |
| Workload WLID | `wlid://cluster-home/namespace-memos/deployment-memos` |
| Deployed tag | `neosmemo/memos:26.11.1` with the immutable digest below |
| Deployment pin / runtime imageID index | `sha256:5dcd6e6b973a55b964f2fe2ccdaa33319463ab448a23404ebb5d2acbd19299ba` |
| amd64 child manifest | `sha256:cc2e0a1a1e518c7578955a6a86f3745bf859736b944200acbc0da61e79659288` |
| Image config / runtime image config ID | `sha256:4beb9fab55f04357e2d37a15331a696b62743807b9fc1ee6438776492d518be1` |

Fetched and hashed the registry OCI index, selected amd64 manifest and config.
The SBOM's embedded manifest/config decode to those exact content digests, and
its source metadata records amd64 plus the requested index digest. This proves
platform resolution rather than assuming the scanner host architecture matches.
The request used the immutable image reference and explicit `linux/amd64`.
Deployment UID, resourceVersion, spec and Pod imageID remained unchanged.

### Persisted results and interpretation

**Before the request:** zero SBOMs, vulnerability manifests or summaries, even
after all components were Ready. **After:** exactly one of each. No other
workload/image was assessed. Operator logs start command processing without
image watchers; only the explicit authenticated request appears in scan logs.
Both recurring CronJobs remain suspended with no lastScheduleTime. The original
posture OperatorCommand remains the only OperatorCommand; direct scanning did
not create a scheduler Job. Unauthenticated diagnostics returned **401**.

The SBOM is `ready`, with **115 package artifacts**, Alpine 3.21.10, complete
source identity and Syft version. The full vulnerability manifest is `ready`,
contains nonempty evaluated matches, report time, database identity and match
method. The Memos summary carries namespace/kind/name/container labels, WLID,
image ID and scan ID, and references the full manifest in `kubescape`.

**Seven package/advisory matches**, representing **five distinct advisory IDs**
(four CVE IDs and one Go advisory). Summary counts are 1 Critical, 4 Medium,
1 Low, 1 Unknown, 0 High/Negligible. The Medium count includes three packages
matching the same BusyBox CVE; do not call these seven distinct CVEs.

| Advisory | Severity | Package | Installed | Fixed version(s) | Engine fix state / availability |
| --- | --- | --- | --- | --- | --- |
| CVE-2026-4738 | Critical | zlib | 1.3.2-r1 | 1.4-r0 | fixed / available |
| CVE-2026-128085 | Medium | go.opentelemetry.io/otel/trace | v1.45.0 | 1.47.0 | fixed / available |
| CVE-2025-60876 | Medium | busybox | 1.37.0-r14 | none reported | empty / unknown |
| CVE-2025-60876 | Medium | busybox-binsh | 1.37.0-r14 | none reported | empty / unknown |
| CVE-2025-60876 | Medium | ssl_client | 1.37.0-r14 | none reported | empty / unknown |
| CVE-2023-36308 | Low | github.com/disintegration/imaging | v1.6.2 | none reported | not-fixed / no fix reported |
| GO-2026-5932 | Unknown | golang.org/x/crypto | v0.58.0 | 0.58.1-0.20261009181718-2f1f834330b2 | fixed / available |

These are engine observations, not verified exploitability or remediation
recommendations. `cveMatchingMode: off` means Grype's default matchers; it does
not turn vulnerability evaluation off. Persisted match details identify vendor,
Go and NVD/CPE matches. Backports, applicability and false positives still need
triage; no suppressions were added. Runtime relevance, reachability, VEX,
embedded/vendor SBOM reuse and host/node coverage were disabled. Unknown severity
and empty fix state remain unknown. Missing/failed/unsupported/incomplete reports
must never count as zero findings. A verified zero would require the same ready
SBOM, successful evaluation, attributable digest/database/time and an explicitly
empty full match array. This assessment has findings.

Later named GETs returned the same complete objects and timestamps. They are
served through the persisted local storage API; no restart/restore durability
test was performed. The following reads reproduce the assessment:

```sh
report=docker.io-neosmemo-memos-sha256-5dcd6e6b973a55b964f2fe2ccdaa33319463ab448a23404ebb5d2acbd19299ba-9299ba
kubectl -n memos get vulnerabilitymanifestsummaries deployment-memos-memos -o json
kubectl -n kubescape get vulnerabilitymanifests "$report" -o json
kubectl -n kubescape get sbomsyfts "$report" -o json
```

Hash each returned `spec` as UTF-8 `json.dumps(spec, sort_keys=True,
separators=(",", ":"))` and compare with `image-canary-evidence.json`.
Read full named objects; empty lists, summary-only zeros and a completed HTTP
submission are insufficient evidence.

### Measured overhead and storage

Metrics-server sampled approximately every ten seconds through baseline,
deployment, cold DB load, canary and idle. These are observed maxima, not
instantaneous peak guarantees. The scan window includes 20 seconds after
completion to account for metrics collection lag.

| Component | Deployment/DB load max CPU / working memory | Canary window max CPU / working memory |
| --- | --- | --- |
| Kubevuln | 751.5m / 250.5Mi | 154.2m / 76.0Mi |
| Offline DB server | 2.8m / 0.8Mi | 1.3m / 0.8Mi |
| Posture scanner | 15.4m / 33.5Mi | 0.5m / 25.7Mi |
| Operator | 16.4m / 19.3Mi | 1.8m / 18.2Mi |
| Storage | 181.5m / 46.6Mi | 14.5m / 49.0Mi |

Later idle samples: engine about 1m / 65Mi, mirror 2m / under 1Mi,
posture scanner 2m / 25Mi, operator 3m / 18Mi, storage 7m / 53Mi.
Kubelet sampling during hydration also observed **3,093,233,664 bytes total
memory usage**, mostly reclaimable filesystem cache; working set was
268,804,096 bytes. Do not equate working-set metrics with all cgroup-charged
memory. No OOM, eviction, throttling-related failure or container restart was
observed. Short certificate-init peaks were not captured.

| Storage observation | Value |
| --- | --- |
| DB PVC filesystem used after hydration | **3,527,569,408 bytes (~3.29GiB)** |
| DB Longhorn allocated per replica | **3,766,988,800 bytes (~3.51GiB)**, three healthy replicas |
| DB provisioning | 10Gi logical; 30Gi across three replicas before snapshots |
| Compressed DB archive / transient download | 205,235,440 bytes; `/tmp` returned to zero |
| Mirror image pull | 201,999,275 bytes on its node; immutable archive served from image |
| Reports PVC filesystem before / after scan | **512,000 / 847,872 bytes**; +335,872 bytes |
| Reports Longhorn allocation after | 154,673,152 bytes per replica; three healthy replicas |
| Reports provisioning | existing 5Gi logical; 15Gi across replicas before snapshots |
| Memos compressed registry layers | 25,773,823 bytes; SBOM reports ~74MB expanded image |

Hydration adds indexes beyond the downloaded SQLite size. Keep headroom for
hydrated old/new DBs and archive; the actual 3.29GiB cache supports the reviewed
10Gi allocation for this snapshot. Recheck size before every DB update. Other
Longhorn attached volumes remained healthy; no node pressure appeared.

API-server sampled CPU maxima before rollout were **318.8m / 707.6m / 201.3m**
for metal0/metal2/metal3. During deployment they were **233.3m / 636.7m / 164.1m**;
during the canary window **117.7m / 358.0m / 108.1m**. Working memory stayed under
1.76Gi per API server during deployment/canary. Core `/readyz` samples all
succeeded (116 observations at initial evidence analysis; maximum measured
kubectl command duration 142ms). This is an ambient-load observation, not an
isolated per-client QPS or server-latency benchmark. No scan-attributed API
request counter was available; no new monitoring stack was installed.

### Safety, locality and recovery

Final checks: all **27 Flux Kustomizations and seven HelmReleases Ready**;
Kyverno controllers Ready; policy and admission webhook specs unchanged; PSA
labels unchanged; Memos spec/UID/resourceVersion and Cilium data unchanged.
Existing Pods had no restart increases and no newly unhealthy Pods appeared.
A pre-existing failed Gitea backup Job from `2026-11-21T15:45:24Z` was already in
the before snapshot; this task did not change or repair it. The WotLK namespace's
Flux ownership label changed from infrastructure to apps-wotlk during normal
reconciliation of its existing duplicate declarations; PSA/owner labels and
workload specs did not change. No unrelated source was modified.

Both live negative admission tests still reject a Deployment missing its CPU
limit and a Pod with privilege escalation. Kubevuln cannot read Memos Secrets,
patch its Deployment or patch admission webhooks. API authentication is active;
image-scan API permissions use the existing short-lived operator identity.
The operator's extra registry-credential Secret grant was removed. No node
agent, privileged/host workload, new webhook, policy exception or enforcement
change was introduced.

Transient startup events were the new PVC's initial scheduling, a brief RWO
multi-attach handoff for storage, readiness 500/refused responses while storage
initialized, and Kubevuln readiness timeouts during cold DB loading. They
resolved without restart; final APIService Available, discovery and persisted
posture GETs succeeded. APIService spec and TLS CA match the prior baseline.
The stored Memos posture controls and severities remain identical. Its completed
assessment was reused; no second posture scan was needed.

Kubevuln explicitly logs `keepLocal` and that statuses/reports will not be sent
to the cloud. Effective configuration has no backend credentials, API URL,
synchronizer or telemetry endpoint. Pinned source skips telemetry discovery in
local mode. Local results and database URL were verified; egress is not isolated
and no packet capture was performed. No external commercial reporting was
configured or observed. No Gatus integration, incident bridge, application fix,
automatic remediation or recurring scan was added.

Use the [GitOps rollback procedure](README.md#rollback): contain Kubevuln through
its replica count if needed, keep both PVCs/data and suspended schedules, and
restore the reviewed posture values through Git while retaining PVC keep
annotations. Do not casually uninstall Helm. Raw reports, snapshots, logs,
metrics, registry artifacts and checksums remain local under `.work/task23/`.
The single-image results will age; future scans require the runbook's explicit
freshness/integrity checks and authorization.
