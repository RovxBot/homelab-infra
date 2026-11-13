# Operations and acceptance

Implementation date: **31 October 2026, Australia/Adelaide**. On **10 November
2026**, `github-dr/wowdecomp` became the writable primary repository in place,
preserving its repository identity, issues, comments, releases and assets.
GitHub imports are permanently stopped. `wowdecomp15595` remains a frozen local
reference mirror with its required commit. PR [#1625](https://github.com/RovxBot/homelab-infra/pull/1625) was approved and
merged as `10bb51b5`. Both app Kustomizations now reconcile the normal
`flux-system` source on main; the temporary `gitea-preview` source was removed.
Shared Cloudflare ingress and Gatus monitoring are deployed and verified.

## Access and daily checks

Development uses `https://git.cooked.beer/github-dr/wowdecomp`. The selective
sync CronJob is suspended, the mirror console has zero replicas, Gitea's mirror
function is disabled and the source guard's `sync_enabled` is false. Do not
resume imports into the working repository. Its existing GitHub PR records are
historical issues; new work uses native Gitea PRs.

`/healthz` and `/mirrorz` now verify local repository availability, writable vs
frozen roles and required reference commits. They do not require new GitHub
metadata or compare current development branches to the archived source tips.
`/backupz` still verifies independent backup freshness. Cold backups preserve
the stopped mirror console and export repository roles for restore verification.
Historical guard repositories, bundles and the mirror console PVC are retained.

Public hostname: `https://git.cooked.beer`. LAN HTTP: `http://192.168.1.190:30301`;
private SSH: `ssh://git@192.168.1.190:30222`. Every healthy node serves the
NodePorts; HTTP recovery administration should use a localhost port-forward.
The verified proxied CNAME targets tunnel
`19dd24d6-5e63-4ba3-a23c-fbebc7312e3f.cfargotunnel.com`.

```bash
export KUBECONFIG="$HOME/.config/talos/cooked-k8s/kubeconfig-entra"
kubectl -n flux-system get kustomizations apps-gitea apps-gitea-runners
kubectl -n gitea get pods,pvc,cronjobs,jobs
kubectl -n gitea-runners get pods
kubectl -n gitea-jobs get pods,secrets
kubectl -n longhorn-system get volumes.longhorn.io
kubectl -n gitea logs deployment/gitea-protection --tail=30
```

Internal checks are `/api/healthz` (Gitea plus database), `/healthz` (accepted
upstream freshness/errors), `/mirrorz` (destination Git/metadata freshness),
`/backupz` (recorded failure, age and available protection space), mirror console
`/api/health`, and runner `:9101/readyz`. Metrics are available from Gitea/runner;
the existing platform has no Prometheus stack. Gatus checks these endpoints
after the shared observability change is merged. Gatus's current GitHub-issue
notification channel depends on GitHub: inspect its local dashboard/logs during
an outage and add an independently delivered alert channel before treating
remote notifications as an incident guarantee.

### Cold backup timeout recovery

On **14 November 2026**, the 02:15 Adelaide cold backup reached its old
30-minute Job deadline. Gitea and the protection service remained at zero
replicas after the backup Pod was terminated; both were restored to one replica
at approximately 08:13. The database stayed available. The failed Pod and its
logs were already unavailable, so the exact command that stalled is unknown.
Public application health, both repository checks and both runners recovered.

The backup supervisor now waits asynchronously for a separate capture process
group, so SIGTERM interrupts the wait and runs cleanup immediately. Cold capture
has a 20-minute timeout and a ten-second forced termination limit. Services
resume before remote integrity checks and retention; the overall Job deadline
is one hour. API requests during cleanup have a 15-second timeout. The retired
mirror remains stopped, and any failure records `backup-failure.epoch`.

If Gitea is stopped after a failed backup, first check that no backup Job or
Pod is active, PostgreSQL is available, and the retained volumes and their
replicas have no failures. Then restore `gitea` and `gitea-protection` to their
configured one replica; leave `gitea-mirror` at zero. Verify `/api/healthz`,
`/healthz`, `/mirrorz`, runner readiness and Longhorn volume health. SIGKILL or
loss of the backup node can bypass shell cleanup and still requires this
recovery procedure. Do not erase failure markers to silence an alert: a
successful backup and integrity check clears them.

The Kyverno chart 3.10 upgrade merged that morning was a separate incident:
it blocked infrastructure reconciliation because legacy policies still exist.
Keep chart 3.9.2 and the Renovate `<3.10.0` gate until all legacy policies and
exceptions are migrated. See the upstream
[CEL migration guide](https://kyverno.io/docs/guides/migration-to-cel/).

### Deleted upstream branches and snapshot failures

`wowdecomp` sets `retain_deleted_branches: true` in
`ops/gitea/protected-repositories.json`. Branches removed from GitHub remain
available in Gitea at their last accepted tips, including unmerged history.
Existing branches still require fast-forward updates; recreated branches with
incompatible history, changed/deleted tags and a missing default branch block
publication. The default for repositories without this option remains to block
on any removed ref. `/healthz` records the retained branch names and tips in
`retained_deleted_branches`.

The protection proxy also serves the allowlisted destination's Git
`info/refs?service=git-upload-pack` and `git-upload-pack` requests for the mirror
app's pre-sync backup. Gitea authenticates these requests; pushes and other
repository paths are denied. These destination snapshots do not replace the
guard's verified previous-ref bundles or the independent cold backups.

On 10 November 2026, sync was found blocked since 8 November by 53 deleted
`wowdecomp` branches and a 404 from the destination snapshot route. Retaining
deleted branches preserves the unmerged `native-port/test-server-character-selection`
tip as well as merged branches while permitting current fast-forward updates.
If this behavior needs reverting, remove the retention option and revert the
proxy change through Git; existing retained refs and backup bundles survive,
and deleted upstream branches will block publication again.

## Acceptance ledger

| Requirement | Recorded result |
| --- | --- |
| Flux-managed Gitea/PostgreSQL | Ready through normal main-source Flux; Gitea 28.2.0, PostgreSQL 17, retained volumes |
| Persistence/reconciliation | Pods restarted and reconciled while repositories/metadata survived |
| Recovery administrator | Local API and real browser sign-in verified; account remains separate from Entra |
| Entra configuration | Real Entra browser sign-in provisioned `sam`; intended administrator role verified, recovery administrator remains local; unassigned-user and existing-account linking flows require separate test identities |
| Public HTTPS Git | Both protected repositories cloned and fsck-verified over public HTTPS; scratch branch push passed; hostname-only HTTP 308 redirect preserves path/query/method |
| Private Git | LAN HTTP clone/fetch and private SSH clone/branch push verified; SSH host key checked through Kubernetes |
| Protected refs | 25 main-repository branches and one reference branch matched GitHub; no tags/LFS objects exposed; required reference SHA verified in local clone |
| Destructive-update protection | Nine tests passed, including rewrites, deletions, changed tags, fetch failures, failed bundle verification, historical recovery and multi-branch authenticated HTTP transport |
| Scheduled refresh | Scheduled refreshes continue successfully after recovery; latest closeout Job is recorded in `validation-results.json` |
| Issues and PR metadata | All 141 upstream numbers represented locally: 37 issues and 104 historical PR issues; provenance matched |
| PR discussions/reviews | All 102 source comments and one review checked; seven missing snapshots appended, second import added zero; no inline-comment fixture available |
| Releases/wiki/LFS | No production fixtures found; enabled release/LFS paths are not evidence of a real asset restore; wiki disabled |
| Linux runner | Registered to ci; Kubernetes job execution, source checkout and cleanup verified |
| Project smoke | Run 11 passed normal core and ASan/UBSan with leak checking and GitHub blocked |
| Full Linux matrix | Run 12 passed all 26 jobs, including Mesa/Xvfb, native client and ASan/UBSan; GitHub hosts blocked in every job |
| Cross-repository checkout | Required reference audit passed with internal source URLs and GitHub blocked |
| Artifacts | Run 10 uploaded, deleted, downloaded and SHA-256-checked the known fixture through local pinned v4 actions |
| Cancellation/timeout | Run 14 timed out at 61 seconds; running probe in run 15 cancelled; no job pods or temporary Secrets remained |
| Native failover | `ci/dr-test-20261031-062148`: writable copy, development push and native PR #1 merge verified; source stayed a mirror; scratch run 13 passed both normal and sanitizer jobs |
| Codex/MCP | Local agent edited/pushed the scratch clone; pinned MCP exposed eight read tools and read repository/reference branch/CI using a dedicated viewer token |
| Independent credentials | AES256 archive decrypted/verified; uploaded, downloaded and checksum-verified outside Restic in B2 |
| Application backup/restore | Snapshot `203a48ba` captured and integrity-checked; isolated PostgreSQL/Git restore passed with 28 bundles and 53 artifact ZIPs including the known payload; nightly 02:15 Adelaide schedule enabled |
| Offline bootstrap inputs | All seven app images cached on eight nodes; 11-image offline registry verified; B2 bootstrap snapshot restored with all file checksums and Git bundle verified |
| Windows/macOS | Unavailable locally; native deployment instructions provided, no false runner labels |
| Policy | Two positive and eleven negative cases passed both CLI evaluation and real server admission |
| Monitoring and cleanup | All six Gatus checks healthy; all Flux reconciliations Ready; temporary source/build servers removed, temporary API tokens revoked, no job pods/Secrets remain |

Machine-readable evidence is committed in `validation-results.json`. The first
906 MiB cold snapshot took about twelve minutes, including the off-site upload;
the isolated restore test completed in four minutes. The retained test PVC
`gitea-restore-test-20261031072825` holds recovery evidence and is not production
application storage.

Earlier failed/cancelled CI attempts are retained. They exposed real platform
constraints rather than being reported as passing. Final acceptance must use
completed jobs on the corrected toolchain and security profile.

## Merge and preview handover

The approved deployment is merged and the handover is complete. Both app
Kustomizations are Ready using `sourceRef: flux-system`; only the temporary
GitRepository was removed. No app namespace or production PVC was deleted.
Shared Cloudflare/Gatus changes, public HTTPS Git and real Entra sign-in passed.
GitHub remains the root Flux source and the local recovery administrator remains
independent of Entra.

Cloudflare has a hostname-specific Single Redirect named **Gitea HTTP to HTTPS**:
request pattern `http://git.cooked.beer/*`, target
`https://git.cooked.beer/${1}`, status **308**, preserve query string enabled.
This external zone rule is managed in Cloudflare, alongside the proxied CNAME.
The read-only MCP example remains available; the temporary validation token was
revoked. Generate an appropriately scoped operator token when installing it.

## Upgrades and rollback

Take a verified cold snapshot first. Reviewed application, chart, runner and
mirror minor updates may share the Monday Renovate maintenance PR. Inspect each
component's release notes and the combined diff, pin images and charts,
render/schema/policy-check, review, reconcile and inspect data and health.
Keep major upgrades and toolchain OS/runtime changes separate. PostgreSQL major
upgrades require a database migration plan. Application schema rollback requires
restoring a matching database/files snapshot to new
volumes; reversing an image pin alone may be insufficient. Keep retained PVCs
and independent credentials even if an app manifest is removed accidentally.

Rotate the GitHub PAT before **29 January 2027** and Entra client secret before
**31 October 2027**. Regenerate the independent encrypted credential archive
after rotation, verify its decryption, upload a new dated object and retain the
last verified recovery set. Never remove superseded credentials before testing
new sign-in/sync and recovery access.
