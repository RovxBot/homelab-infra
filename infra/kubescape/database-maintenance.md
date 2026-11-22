# Verified Grype database maintenance

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
copy inside that image before starting the engine. It mounts only `/tmp`,
never the DB PVC. This prevents starting against an old mirror during
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
