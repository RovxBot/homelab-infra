# Protected repositories

`ops/gitea/protected-repositories.json` is the source allowlist. It contains
exactly `RovxBot/wowdecomp` → `github-dr/wowdecomp` (`main`) and
`RovxBot/wowdecomp15595` → `github-dr/wowdecomp15595` (`w15595`). The second
must contain commit `959b0b85c5f55c565328d7c3ca33027a65e41327`.

Both destinations are private pull mirrors owned by a dedicated non-admin
`mirror-bot`. GitHub remains primary. Never push to, promote, rename or delete
these production mirrors as part of a test. The writable `ci/dr-test-*` copies
are the exercise targets. The `actions` organization contains three explicitly
copied CI dependencies, not additional protected project repositories.

## Safety boundary

The pinned RayLabsHQ application is **3.50.0**, reviewed at source commit
`4a4550b94f4170dd9a2918542730e3d28fbae550`. Its force-push detector catches
errors and proceeds, so `block-on-force-push` alone is insufficient. Its startup
discovery also runs despite `autoImport=false`, and its JSON client rejects
Gitea 28's empty HTTP 200 mirror-sync acknowledgement. The implementation
accounts for all three observed behaviors.

`gitea-protection` fetches GitHub into a hidden candidate ref namespace on its
own retained PVC. It publishes branches/tags only after checking:

- Every previously accepted ref is still present; branches advance by ancestry
  and tags keep their exact object IDs.
- The default branch and required reference commit exist.
- LFS fetch/fsck succeeds, and a complete bundle of the previous accepted refs
  is created, verified and checksummed before publication.

Unchanged refs refresh source health without creating duplicate full bundles.

Publication uses a Git ref transaction. Fetch failures, HTTP 403/404, missing
refs, rewrites, LFS failures and failed bundle verification leave accepted refs
available. Candidate refs are hidden from clones. Bundles never expire
automatically; monitor free space and archive them before deliberate retention
maintenance. A full protection PVC blocks future publication instead of
discarding recovery history.

Gitea pulls only from the authenticated guard's read-only Git/LFS endpoint.
Native mirror timers and `cron.update_mirrors` are disabled. A separate API
proxy denies every DELETE, denies writes outside the allowlist, fixes migration
sources to the guard, and prevents source identity changes. The source GitHub
URL is presented only to the metadata client's idempotency check; the real
Gitea pull URL stays internal. Never point the metadata app directly at Gitea.

## Scheduling and manual operation

The guard checks sources every 1,800 seconds. Kubernetes CronJob
`gitea-selective-sync` runs at minutes 0 and 30, logs into the private mirror
console and submits only the configured two repositories. It waits for a new
completed sync timestamp. Its 14-minute ceiling avoids the 02:15 backup window.
Do not set `SCHEDULE_INTERVAL`, `GITEA_MIRROR_INTERVAL` or `DELAY` in the app's
environment: their presence enables the defective account-discovery startup
path even when `SCHEDULE_ENABLED=false`.

```bash
kubectl -n gitea create job --from=cronjob/gitea-selective-sync \
  gitea-sync-manual-$(date -u +%Y%m%d%H%M%S)
kubectl -n gitea get jobs
kubectl -n gitea logs job/NAME
kubectl -n gitea port-forward service/gitea-mirror 14321:4321
```

Open `http://localhost:14321` only through this localhost forward. The console
password is in the encrypted `gitea-mirror` Secret. The supported per-repository
Sync command refreshes Git and metadata; the initial Mirror command alone can
simply acknowledge an existing mirror without refreshing its history.

The GitHub fine-grained PAT expires **29 January 2027**, selects only the two
repositories and grants contents, metadata, issues and pull requests read-only.
GitHub inherently permits fine-grained tokens to read other public repositories;
the scheduler and guard enforce the narrower destination scope. Rotate via a
new restricted PAT, update SOPS ciphertext and the mirror deployment's
credential revision annotation, reconcile and verify a scheduled refresh.

## Metadata and limits

Issues, labels, comments, release metadata/assets and PR discussions use the
GitHub API. Mirrored PRs are historical issues with upstream links and authors,
not native Gitea PRs with executable merge state. New development creates native
Gitea PRs. The reference repository had no issues/PRs/releases; neither source
exposed a wiki Git repository or LFS objects during verification. Wiki mirroring
is disabled; enabling a new upstream wiki requires a separate protected copy
and restore test. Release assets have no production fixture yet. Do not claim
an unexercised metadata type is restored merely because its setting is enabled.

PR issue bodies alone do not preserve their discussions in this application.
`discussions.py` therefore copies ordinary/PR comments, inline review comments
and review events through the guarded API, preserving source author, URL,
timestamps and full inline context/JSON. Stable content hashes make this
append-only and idempotent; changed source records produce new snapshots.
Existing normal issue comments are recognized by the upstream app's provenance
marker. The scheduled freshness marker is written only after this pass succeeds.

## A suspicious update

Inspect `/healthz`, `/mirrorz`, the guard log and saved accepted refs. Stop the
selective CronJob through a Git change if investigation will exceed its normal
window. Preserve a cold off-host backup and the verified pre-update bundles.
Investigate from a scratch clone, compare old/candidate commits and contact the
repository owner. Prefer restoring the upstream branch to a descendant of the
accepted tip or merging reviewed work locally. Never approve an upstream
rewrite merely to clear a health alarm.

An intentional ref replacement is a maintenance operation: freeze the guard,
take and restore-check a cold snapshot, record exact old/new ref IDs and obtain
operator review. Work on a restored/scratch copy first. Publish changes only
after proving the old tip can be recovered from the off-host bundle. There is
no automatic approval endpoint or garbage collection that discards that tip.

References: [mirror source](https://github.com/RayLabsHQ/gitea-mirror),
[Gitea mirroring](https://docs.gitea.com/usage/repo-mirror).
