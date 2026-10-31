# Operations and acceptance

Implementation date: **31 October 2026, Australia/Adelaide**. GitHub remains
primary. PR [#1625](https://github.com/RovxBot/homelab-infra/pull/1625) was approved and
merged as `10bb51b5`. Both app Kustomizations now reconcile the normal
`flux-system` source on main; the temporary `gitea-preview` source was removed.
Shared Cloudflare ingress and Gatus monitoring are deployed and verified.

## Access and daily checks

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

Take a verified cold snapshot first. Update one component at a time, pin images
and charts, render/schema/policy-check, review, reconcile and inspect data and
health. PostgreSQL major upgrades require a database migration plan. Application
schema rollback requires restoring a matching database/files snapshot to new
volumes; reversing an image pin alone may be insufficient. Keep retained PVCs
and independent credentials even if an app manifest is removed accidentally.

Rotate the GitHub PAT before **29 January 2027** and Entra client secret before
**31 October 2027**. Regenerate the independent encrypted credential archive
after rotation, verify its decryption, upload a new dated object and retain the
last verified recovery set. Never remove superseded credentials before testing
new sign-in/sync and recovery access.
