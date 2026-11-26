# Fresh evaluation extension

The qualified Kubevuln source at commit
`45d265e3b46af4d7fc1c896ecc22a93915f2d6fe` caches a vulnerability manifest for
the same image, Syft, Grype and database version. A successful cached request
preserves its original evaluation timestamp. Task 25's first production request
demonstrated this: the request completed successfully, but the strict client
rejected the unchanged older report. No check was relaxed and no report deleted.

The local patch adds one opt-in `args.forceRescan: true` branch to **ScanCVE**.
Only a typed JSON boolean true skips the CVE cache lookup. SBOM caching, actual
Grype matching, database loading, authentication, authorization, concurrency,
timeouts, result persistence and all default caller behavior remain upstream.
It never substitutes a new timestamp for old findings: the new timestamp comes
from actual Grype matching. Tests prove that default/false/string callers keep
their cache semantics, true invokes matching again on an unchanged database,
and the persisted report's UID/creation time survive the update.

The dedicated build workflow fetches a SHA256-verified source archive, applies
the small checked-in patch without fuzz, runs focused upstream regression tests,
and compiles with locked Go modules. Builder and runtime images are pinned by
digest. The runtime derives from the exact qualified scanner image and replaces
only `/usr/bin/kubevuln`. Publishing uses the existing same-repository GitHub
package pattern; no cluster credential is supplied. Deployment must pin the
published digest and record its source/build run. An independent signature is
not claimed. Do not deploy a mutable build tag.

The workflow's normal PR path verifies the existing proof and retrieves its
immutable pin. A reviewed `workflow_dispatch` with `build_candidate=true` is the
only build/publish path. After successful tests, publication and anonymous
retrieval it uploads `scanner-image-digest.txt` and generated
`scanner-build-evidence.json`; it never edits the committed proof or deployment.
The candidate's source commit and hashes must match the later promotion PR.
Human release initiation/review belongs in the Wiki's Kubescape page (the
23 November production-feedback change includes a review draft).

Rollback restores the original scanner digest through GitOps and suspends the
new vulnerability schedule: the original binary cannot satisfy the fresh
evaluation contract for cached reports. Existing reports, SBOMs, history and DB
remain preserved. No unrelated upstream issue/PR is introduced.

The isolated read-only scan job now assesses the exact verified published
scanner digest using the pinned Grype contract shared by the receiver and MCP
builds. Its source commit is the original recorded build commit, not the
current PR when reusing existing build evidence. It retains only sanitized
provenance, not CVE IDs, counts, package names or raw reports. A scan failure
is an unknown result and fails the scan job; a successful scan does not mean
zero vulnerabilities. No promotion, cluster access or vulnerability threshold
is introduced. Runtime private assessment remains a separate contract.
