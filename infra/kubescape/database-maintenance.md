# Verified Grype database maintenance

Current recurring execution and the opt-in fresh-matching scanner are described
in the [recurring runbook](recurring/README.md). The Task 24 observations below
remain historical acceptance evidence. Before a future DB replacement, suspend
both new assessment CronJobs through GitOps and wait for active scans to finish;
include retained assessment history in free-space qualification.

Tracking [#2135](https://github.com/RovxBot/homelab-infra/issues/2135) remains open.
The [original canary](image-assessment.md) and its JSON evidence remain the
comparison baseline. Chart 1.42.0, Grype 0.121.0, Syft 1.55.0, the 10Gi database
and 5Gi reports PVCs, suspended schedules, disabled image routing and admission
enforcement are retained.

## Discovery and review

`Grype Database Refresh` runs daily at **07:19 UTC (17:49 Adelaide during
daylight saving)**, independently of Monday Renovate, and supports **Run
workflow**. Its only write credential is the existing `WORKFLOW_PR_TOKEN`,
supplied to the final PR-preparation step. Use a repository-restricted credential
with Contents and Pull requests write access. There is no cluster credential.
Checkout retains no credentials; the built-in token has Contents read only.
There is no default `GITHUB_TOKEN` fallback because its PRs would not trigger
normal PR CI. A credential's actual scope must be maintained by its owner;
workflow permissions cannot reduce the scope of a PAT stored in a secret.

The fixed branch `ci/grype-database-refresh`, concurrency lock and PR lookup
allow one open proposal. Identical/older releases are skipped. An explicit
force-with-lease replaces only the updater's own three-file branch; unrelated
changes on that branch stop publication. Existing branch protection dismisses
obsolete reviews. No automatic merge is configured. An operator reviews the
identities, headroom and normal CI before merging. Failed discovery leaves a
failed workflow and verification artifact, without deploying or opening an
incident. Check Actions if scheduled discovery has not succeeded recently.

Manual fallback on a clean checkout:

```sh
python3 -m pip install PyYAML==6.0.3 zstandard==0.25.0
python3 -m unittest discover -s scripts/tests -p 'test_grype*.py' -v
python3 scripts/grype_db.py --prepare
git diff -- infra/kubescape
# With an appropriate GH_TOKEN in the process environment and only the
# prepared three-file change in the working tree:
python3 scripts/grype_propose.py
```

`v6-latest` discovers the index; every platform manifest, config and layer is
then fetched by digest and checked for SHA-256 and descriptor size. Production
uses only the immutable index. This phase qualifies **linux/amd64**, the
architecture of all eight nodes, and **schema v6.1.10**. Platform/schema
expansion requires compatibility review.

The archive SHA-256, SQLite `quick_check`, internal `db_metadata`, build time
and schema must agree. Unsafe paths, links, ambiguous files, invalid/future
builds and databases older than **72 hours** fail. Build, schema, archive path
and checksum must also match a separate HTTPS request to **Anchore's publisher
endpoint** (with Anchore's `toolbox-data.anchore.io` distribution host as a
second publisher endpoint). If both are unavailable, or metadata is mismatched, the automated proposal
fails closed. Mirror-provided attestations/checksums cannot substitute for it.
This establishes publisher linkage through authenticated HTTPS and matching
bytes; no independently signed OCI build is claimed.

`grype-database.json` preserves previous/new identities, verification method,
sizes and headroom budget. CI independently reverifies changed locks and
regenerates the exact chart render, validating the inventory including mirror
pin and startup gate. Historical lock evidence does not expire in static CI;
live freshness always uses the current clock.

## Replacement and storage

Before merge, require healthy Bound PVCs, three healthy Longhorn replicas,
exported named reports and an identified recovery point. Check physical free
space on replica disks as well as logical filesystem free space. A PVC's
stated 10Gi capacity alone is insufficient. Compare the candidate budget with
the read-only audit's actual free bytes:

```sh
python3 - <<'PY'
import json, sys
sys.path.insert(0, 'scripts')
from grype_db import storage_budget
lock = json.load(open('infra/kubescape/grype-database.json'))
audit = json.load(open('.work/kubescape-db/cache-audit.json'))
print(storage_budget(lock['database']['bytes'], lock['database']['archiveBytes'],
                     audit['sqlite']['filesystem']['availableBytes']))
PY
```

Required additional space is **2 × new expanded DB + 2 × compressed archive +
512Mi**, with the old hydrated DB still charged as used. This reserves 1.5 ×
expanded size for hydration and 0.5 × for SQLite index temporaries, plus archive
temporaries. This is a qualification budget, not a guarantee of arbitrary
future growth. Stop if hydration exceeds it or replica disks lack space.
Local verification also needs free disk for layers, archive and extraction.
No unverified archive is downloaded into the live cache.

The pin updates the engine Pod annotation and a bounded startup init. The init
uses the same immutable DB image and compares served `latest.json` with the
copy inside that image before starting the engine. Its explicit data mount is
`/tmp`, never the DB PVC; it inherits the engine Pod's ServiceAccount projection.
This prevents starting against an old mirror during
reconciliation. The engine retains **Recreate**: the old engine stops before
the replacement mounts its RWO cache. Image assessments briefly pause;
posture/storage Deployments are unchanged. Grype hydrates under the same cache
root before deleting/renaming the active directory. This is not an atomic
rollback guarantee. Neither PVC is deleted/recreated.

After merge, verify Flux source revision, child/HelmRelease readiness, runtime
imageID, loaded cache identity and freshness. Measure DB-load start/end, CPU,
working set, cgroup usage, ephemeral storage and PVC/Longhorn usage. Verify
existing reports and application/security health before the authorised rescan.

## Freshness and manual assessment

```sh
python3 scripts/kubescape_db.py audit-manifest > .work/cache-audit-preview.json
python3 scripts/kubescape_db.py check --audit-cache
```

The transient Job uses pinned Grype 0.121.0 and the repository's pinned Python
image, restricted contexts, no ServiceAccount token and a **read-only PVC
mount** on the engine's node. It requests 200m/256Mi total, with 500m/512Mi
limits for each of two containers. Grype validates the hydrated DB's stored
xxh64 checksum across the complete hydrated file; Python reads internal metadata
and computes its SHA-256. Full SQLite `quick_check` runs on the isolated candidate
before deployment; repeating its random reads on the live Longhorn volume was
too slow for a routine check. Hydration adds indexes, so that SHA differs from the
archive's original expanded SQLite SHA. The Job has a ten-minute deadline,
no retries and a ten-minute completed TTL; logs are saved locally first.

The check reads authenticated in-process diagnostics, mirror imageID/metadata,
live GitOps pin and the last full named report. Tokens remain in memory and
last fifteen minutes; port-forward binds localhost only. Audit evidence is
bound to engine Pod UID and expires after fifteen minutes. Re-audit on restart
or expiry; `check` without `--audit-cache` can reuse a recent saved audit.

JSON includes `state`, `databaseState`, `canAssess`, `assessmentTrustworthy`,
`lastSuccessfulAssessmentTime`, loaded identity, age, integrity and distinct
`reasons`. Exit codes: **0 healthy, 2 stale, 3 unknown, 4 failed**. The output
supports a later Gatus adapter; no service/Gatus/incident bridge is installed.
After a refresh, a fresh database can permit a scan while its previous report
remains `unknown`/`assessment_previous_database`. Ready Pods cannot mask expiry.
An empty match array is valid only when all other evidence gates pass.

For a separately authorised scan of the **unchanged original Memos digest**:

```sh
python3 scripts/kubescape_db.py scan-memos --audit-cache
```

Submission is refused when the database freshness/integrity gate fails. The
procedure saves the previous full manifest, SBOM and summary, checks running
imageID/workload, submits exactly one immutable `linux/amd64` image request,
waits for terminal success, and requires a newly timestamped full report using
the expected DB. It verifies SBOM manifest/config against the original canary
and confirms the Deployment remains unchanged. Evidence is under
`.work/kubescape-db/`; export/retain it before later assessments. Never blindly
retry a timed-out request: inspect its saved job ID first.

The gate is enforced by this **supported client procedure**. The underlying
Kubevuln API is **not claimed to enforce 72-hour freshness**; direct callers
can bypass the client. Preserve API authentication. HTTP acceptance and old
successful reports do not establish a fresh assessment.

## Failure and recovery

Publisher unavailability, corrupt/checksum-mismatched data, unsupported schema,
invalid/future timestamps, insufficient storage or failed audits stop the
proposal/assessment. Retain existing data. Retry discovery when the source is
reachable; do not relax checks. Schedules and routing remain disabled.

If loading fails, inspect init/engine logs, mirror identity, PVC attachment and
Longhorn health. Fix availability/storage first. Revert digest, startup image,
rollout annotation and matching lock/inventory through a reviewed PR if needed.
An older mirror does not force Grype to downgrade an already newer cache.
Verify actual diagnostics/audit after recovery; an old snapshot may be Ready
but too stale to assess. A corrupt cache requires a reviewed recovery plan
using its retained PVC/recovery point. Do not clear the cache, delete/recreate
PVCs, uninstall Helm or delete reports as a shortcut. Report exports and the
original canary remain the comparison evidence; finding counts may stay equal.

## Production qualification — 22 November 2026

[PR #2182](https://github.com/RovxBot/homelab-infra/pull/2182) merged at
`b9de87b31450917482fc8a9680ff2ea3be05d8e5` after normal CI and independent
publisher verification passed. The [machine-readable acceptance evidence](database-maintenance-evidence.json)
preserves the qualification, original/new hashes, live cache audit, scan request,
terminal status, resource samples and recovery points. All times below are UTC.

The mirror changed from the original 20 November build to **21 November
06:20:06**, schema **v6.1.10**, archive SHA-256
`921d1b1b4151a211d39efbb8a7da54caabfc6eb717112766cb5bdee7920809b5`.
The runtime mirror imageID matches index
`sha256:cd4f2c18691791b2c7754a619d011eb388773cdc39c973b621cbcc4217356449`.
Independent Grype cache checksum validation, SQLite metadata, in-process
diagnostics and the persisted vulnerability descriptor all identify that build.
The hydrated cache's SHA-256 is
`aaceea30e7446b2a365d171916ef3fbcaf719e194e0062c97c9ce2b4f0ccdfd8`.

Helm revision **3** retained chart 1.42.0. Upgrade began **03:42:31**, the
mirror-wait init exited successfully, and DB loading ran **03:43:05–03:49:01
(356 seconds)**. Helm Ready followed at **03:49:10**. Recreate reused the same
RWO PVC on metal4; PVC UIDs and `keep` annotations are unchanged. The new mirror
and engine have zero restarts. Expected readiness failures occurred while the
old mirror terminated and the new database hydrated; they were not treated as
freshness evidence.

The pre-scan gate returned `databaseState: healthy`, `canAssess: true`, but
`state: unknown`, `assessmentTrustworthy: false` and reason
`assessment_previous_database`. The one authorised unchanged-Memos request
`memos-db-refresh-20261122035111` then succeeded **03:51:11.697–03:51:18.317**
in **6.619133 seconds**. The supported post-check returned `healthy`, verified
integrity, **21.52-hour** database age and `assessmentTrustworthy: true`.

The result remains **7 package/advisory matches, 5 distinct advisory IDs**.
The **115-artifact SBOM** was reused for the unchanged immutable image;
embedded manifest/config hashes match the original canary. The persisted full
vulnerability report has the new timestamp and DB identity. The image-derived
scan ID remains stable and is not used as proof of a new assessment. Named
rereads reproduce the complete new specs. Original full reports were exported
before replacement and their earlier repository evidence remains unchanged.

Observed load maxima: **801.6m CPU / 1,040.7Mi working memory** for Kubevuln.
Cgroup usage reached **3,220,697,088 bytes**, close to the unchanged 3Gi limit,
including filesystem cache; no OOM, eviction or restart was observed. Ten-second
metrics samples are not instantaneous peak guarantees, especially for a short
rescan. The evidence records all component/window measurements. Future DB
growth still needs resource qualification before expanding scan coverage.

The 10Gi filesystem had **6,919,675,904 bytes free** before replacement against
**6,338,427,164 required**. Peak sampled usage was **7,072,632,832 bytes**, with
at least **3,374,612,480 free**. After hydration, usage is **3,545,190,400 bytes**;
temporary/ephemeral usage peaked near 206MB. Three healthy replicas remain for
each PVC. Retaining the old DB recovery snapshot increases physical DB allocation
from **3,766,988,800 to 7,313,797,120 bytes per replica**. The two named recovery
snapshots are ready; a destructive restore was not performed. Pre-existing
Longhorn disk scheduling pressure was recorded and no unrelated cleanup run.

All **27 Flux Kustomizations / 7 HelmReleases** were Ready after acceptance.
Posture specs, existing SBOM, Kyverno policies/Deployments, admission webhooks,
PSA labels, Cilium configuration and the Memos Deployment compare unchanged.
Gitea, WotLK, Cilium and Longhorn workloads are Ready; no existing Pod restart
increase or new failure was observed. Both scan schedules remain suspended with
no last run, image routing remains disabled and result counts remain one each.

The [manual run of the scheduled workflow](https://github.com/RovxBot/homelab-infra/actions/runs/44981809543)
succeeded, independently verified the current release and reported `changed:
false` / `No database refresh needed`, creating zero updater PRs. Twenty focused
tests cover all four freshness states, invalid/future timestamps, unavailable
publishers, mismatched checksums, incompatible schemas, insufficient storage,
deduplication and refusing stale scan submission. Gatus, incident automation,
recurring scans, automatic routing and CVE remediation remain outside this task.
