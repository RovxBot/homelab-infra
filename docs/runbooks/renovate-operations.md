# Renovate Monday patch day

The refactor reduces 52 package rules / 819 configuration lines to 24 rules /
534 lines. It removes repeated application-directory and automerge rules,
the global approval requirement and custom title-parsing approval workflow,
and the broad Actions, mixed-language runtime and cross-root Terraform buckets.
Critical exceptions follow the broad maintenance rules; disabled dependencies
are terminal safeguards. The sole later approval exception names specific
tested Python helpers and is covered by precedence tests.

The hosted Renovate GitHub App owns dependency discovery, the Dashboard and PRs.
There is no scheduled Renovate Actions runner or Dashboard-editing approval bot.
All updates require CI and a manual merge. Merging GitOps manifests to `main`
allows Flux to reconcile them; creating the implementation PR deploys nothing.

Normal maintenance uses **one `Update weekly maintenance` PR** on the persistent
`renovate/weekly-maintenance` branch, Monday **06:00–18:00 Australia/Adelaide**
(`* 6-17 * * 1`, including daylight saving). The hosted App must actually run
inside that window; the schedule does not start a job. An unmerged batch stays
open across weeks. It requires no Dependency Dashboard approval.

Routine patches, digests, pins and digest pins share this branch across
applications, helpers, Dockerfile bases, ordinary Helm charts and Actions.
The Terraform regex manager's PostgreSQL image digests join it too. Minor
updates join only for the named packages in the explicit allowlist in
[`renovate.json`](../../renovate.json). That list includes the approved Gitea,
Immich, Wger, Invoice Ninja, media and supporting-container release channels.
Unlisted minors open independently, or in their application's release group.
Action minors are not implicitly allowlisted. Calendar versions, floating-tag
digest changes and upstream minor releases still need a careful diff review.

`prPriority: 10` prioritises maintenance. PR, branch and hourly creation limits
are `0` (unlimited), so outstanding exceptional PRs cannot consume its capacity.
Grouping and explicit approval gates control the queue instead of throttling it.
`groupSingleUpdates: true` and `minimumGroupSize: 1` let even one eligible update
create the maintenance PR. Its group branch template also overrides Docker's
separate `digests-pin` template. Do not add a minimum batch size globally.

`updateNotScheduled: false` and `rebaseWhen: conflicted` retain a stable PR
outside Monday. They do not guarantee one immutable commit or one CI execution:
new releases during Monday, conflicts, explicit Dashboard rebase/retry requests,
security fixes and branch-protection requirements can create fresh commits.
GitHub currently requires up-to-date branches on `main`, so merging another PR
first may require rebasing maintenance and running CI again. Keep the existing
five required checks (`repo-checks`, `actionlint`, `gitleaks`, `validate`,
`yamllint`); irrelevant checks may report skipped through the existing selector.
Code-owner review and dismissal of stale reviews also remain configured.

For the usual single CI cycle, review and merge the current maintenance head
before advancing `main` with exceptional upgrades. Request an individual
Renovate rebase/retry only when necessary, then wait for fresh CI and renew any
stale review. Avoid the Dashboard's rebase-all control. GitHub's Update branch
button can add a human commit and leave a Renovate PR blocked as edited.

## Upgrade exceptions

All ordinary exceptions use Monday's creation/refresh window, but may be
reviewed and merged later. The Dashboard gates below approve PR creation;
they never approve merging or deploying.

| Change | Result |
| --- | --- |
| Major application releases | One release group per application; related deployments and non-routine supporting releases share it. Unrelated applications stay separate. |
| Gitea runner majors | One gated `gitea-runners` PR for both runner deployments, separate from the Gitea application. |
| Action, reusable workflow and hosted runner majors | Separate per dependency, shared across workflows using that dependency. |
| CI language release lines | Gated groups per language, separating Node from Python. Home Assistant's regression workflow remains restricted to Python 3.14. |
| Terraform providers, modules and CLI | Gated per dependency across roots. No directory-wide infrastructure group; embedded Docker images follow image policies. |
| Longhorn, Kyverno, GPU Operator, snapshot-controller, Flux control components | Gated by component, including patches and digests; review API, CRD, RBAC and maintenance compatibility. Kyverno remains `<3.10.0`; Longhorn still requires its supported upgrade sequence and engine procedure. |
| Identity images (Vaultwarden, OAuth2 Proxy, Synapse, Element) | Isolated, gated per dependency, including patches and digests. |
| OS/language/MySQL release lines | Gated by application or image-build context and package. Unrelated MySQL/Ubuntu environments do not share upgrade PRs. |
| Home Assistant release lines | Gated `home-assistant` PR while #2021's AirTouch regression remains unresolved. Patches and compatible helper maintenance can batch. |
| PostgreSQL, Immich PostgreSQL, MySQL, MariaDB, Redis and SQL Server majors | Disabled; explicit migration and rollback plans are required. Never include them merely because they share an application directory. |

Only the named Python helper images in the four existing issue-sync/retention
manifests and the BirdNET dashboard may bypass the controller/runtime minor gate.
Their patches, digests and minors batch; interpreter majors require approval.
Existing CI tests proposed issue-sync/retention images in isolated containers.
BirdNET's stable eight-digit date tags remain a narrow weekly exception to
Docker's major classification. Wger prerelease, LinuxServer `-ls` and Jellyfin's
two/three-component versioning remain supported.

Native per-application grouping replaces the old title-parsing approval bot.
`separateMajorMinor: false` on the application fallback allows the components
of a release to share a branch; later routine rules move low-risk updates into
maintenance. Grouping alone does not prove compatibility or select a matching
helper release. Review migration notes and change compatibility constraints
explicitly when required. Routine helper updates are not duplicated into a
simultaneous risky release PR.

Immich server and machine-learning accept only stable `vX.Y.Z` tags and share
maintenance or their application's release group. CI's
`check_immich_versions.py` rejects unequal application tags and missing digests,
so an asynchronous registry publication cannot produce a mergeable partial
upgrade. Each image keeps its own digest. If one image's lookup lags, wait for
both and refresh the PR; do not loosen the check. PostgreSQL is checked under
the separate database policy.

GitHub vulnerability-alert fixes are immediate, ungrouped, labelled `security`,
without Dashboard approval, and bypass normal schedule and concurrency limits.
They still cannot auto-merge. Their branches may rebase behind `main` for timely
validation. This uses Renovate's supported GitHub alert integration, not a
promise to discover every container CVE.

## Immutable promotion and lookup findings

Docker digest pinning and Actions SHA pinning remain enabled. Terminal disabled
rules protect pipeline output references across managers and paths, including
hardcoded admission-policy references. The Gitea rootless regex manager alone
updates its plain tag and separate digest atomically; duplicate Flux image
extraction remains disabled. The Terraform image regex manager is unchanged.
Cilium's OCI source remains disabled for its dedicated primary-CNI workflow,
and generated Flux bootstrap components remain excluded and digest checked.

Promote Gitea toolchain/backup digests together through the Linux-toolchain
build, smoke tests and artifact review; update runner configuration, admission
policy, backup jobs and restore tools together. The wowdecomp toolchain and
Kubernetes MCP are also immutable pipeline outputs. MoP, WotLK, registration
and OpenBrew database outputs remain under their respective image workflows.
Renovate tracks upstream Dockerfile bases but does not retarget these outputs.
Never add floating `latest` tags to silence a lookup failure.

Audit observations on 15 November 2026 (Adelaide):

- Both public Immich `v3.4.0` tags and pinned manifests returned HTTP 200 with
  anonymous pull tokens. Native Renovate lookup found **both `v3.4.1` images**
  and their distinct digests. No credential fix was needed locally. The hosted
  Dashboard's earlier `no-result` cannot be attributed conclusively to auth,
  missing tags or configuration; neither image is disabled by this refactor.
- Kubernetes MCP permits anonymous pulls of its pinned digest and exposes
  revision tags. `latest` returned **404 MANIFEST_UNKNOWN**. Digest-only
  extraction caused Renovate to look for a default tag the publishing workflow
  never creates. The pipeline-owned exclusion removes that inappropriate lookup.
- The private wowdecomp registry returned an edge **403 / code 1010** to a
  generic HTTP client. Native Renovate reached its registry challenge but the
  anonymous token request returned **401** and reproduced
  `No docker auth found - returning`. These are distinct responses, not evidence
  that every failing package needs credentials. Its security-policy image must
  be promoted with the owning pipeline, so it is excluded instead.
- Renovate emits `Invalid registry response` when the auth probe gets neither
  HTTP 200 nor a valid HTTP 401 authentication challenge. An edge-block response
  can cause it, but the Dashboard omits the originating request. Its precise
  historical cause remains unverified without the hosted Mend job log.

After merging, let the hosted App reconcile and inspect its next log. If Immich
still reports `no-result`, record the exact tag-list/pagination request, HTTP
status and registry error, and request a fresh lookup/retry through Mend. Compare
that response with the successful anonymous GHCR probe before adding credentials.
A 401 requires checking the token challenge and package pull access; an edge 403
requires correcting the proxy/WAF allowance for the hosted runner. No credentials
are needed for excluded pipeline references. If intentionally restoring private
upstream tracking later, configure a narrowly scoped `docker` host rule for
`git.cooked.beer` in the hosted service with a package-read credential; never
commit it or restore the retired Actions Renovate PAT/secrets.

## Existing queue and validation

The audit found exactly five open Renovate PRs. None is edited, closed or merged
by this implementation. After the hosted App reconciles the merged configuration:

| Existing PR | Operator follow-up |
| --- | --- |
| #2090, runner 6 | Preserve the two-runner diff; the new gated runner group may supersede its branch. Review migration/security policy compatibility and keep only one reviewed proposal. |
| #2089, Node + Python | Retire the combined proposal after separate gated language entries appear. Approve languages independently; do not rebase or merge the old combined branch blindly. |
| #2087, Gitea MCP minor | Now eligible for weekly maintenance; close the old proposal only after confirming its replacement diff. |
| #2058, PostgreSQL digest | Now maintenance, no broad Terraform upgrade label. Confirm the exact pinned PostgreSQL digest survives regrouping before retiring the old PR. |
| #2021, Home Assistant | Keep isolated and unmerged. Job logs confirm `UnitOfTemperature` is imported from incompatible `homeassistant.components.climate`. Fix/test the integration separately and require passing `airtouch-regressions` before approving the release. |

Renovate may prune or supersede old branches during reconciliation; old PRs are
not automatically merged into maintenance. Preserve intentional manual commits
before requesting regeneration. Do cleanup individually after verifying the
replacement; never use rebase-all as a migration shortcut.

Run the strict validator and native policy tests with the CI-pinned version:

```sh
npx --yes --package renovate@44.126.1 renovate-config-validator --strict renovate.json
npx --yes --package renovate@44.126.1 -c 'node --test .github/tests/renovate-policy.test.cjs'
python3 -m unittest discover -s scripts/ci/tests -v
python3 scripts/ci/check_immich_versions.py
```

The policy tests resolve real presets and exercise Renovate's own package-rule
matching, versioning, extraction and branch generator. They cover single pins,
application coordination, critical precedence, disabled dependencies, safe minor
allowlisting and summer/winter schedule boundaries. They do not approximate
Renovate with a second rule evaluator. The CI selector runs them for config or
policy-test edits; the manifest gate checks actual Immich tags.

A read-only `--platform=local --dry-run=lookup` with JSON debug logs can validate
live discovery. Set `RENOVATE_LOOKUP_LOG` to that completed log when running the
policy tests to recompute effective branches from real lookups. Raw lookup
records include disabled candidates; count the resulting branches, not every
raw `updates` entry. The optional test prints the branch/title/package evidence.
Local lookup does not create PRs or exercise the hosted scheduler, Dashboard
approval state, vulnerability-alert delivery or a future Monday merge.

The implementation's live lookup classified 42 update occurrences across 19
packages into `Update weekly maintenance`, including both Immich `v3.4.1`
images, Gitea MCP `1.13.0` and the Terraform PostgreSQL digest. Seven exceptional
branches remained: Gitea runners (two occurrences), Home Assistant, CI Node,
CI Python, the MCP Python image/runtime pair, the Gitea toolchain Ubuntu line,
and hosted Ubuntu runners. The native integration test excluded database majors
and all pipeline-owned references from the effective branches. This is a
registry snapshot, not a promise that future releases have the same count.

Official semantics: [rule precedence and grouping](https://docs.renovatebot.com/configuration-options/#packagerules),
[scheduling](https://docs.renovatebot.com/configuration-options/#schedule),
[rebasing](https://docs.renovatebot.com/updating-rebasing/),
[vulnerability alerts](https://docs.renovatebot.com/configuration-options/#vulnerabilityalerts),
[read-only dry runs](https://docs.renovatebot.com/self-hosted-configuration/#dryrun).
