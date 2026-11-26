# GitHub failover

`github-dr/wowdecomp` was deliberately promoted in place on 10 November 2026.
Gitea is now its writable primary; GitHub imports are stopped. Its existing
issues/releases and the separate frozen `github-dr/wowdecomp15595` reference
repository are retained. Current roles are recorded in the
[protection configuration](../../ops/gitea/protected-repositories.json),
[operations ledger](operations.md) and [Gitea Wiki](https://github.com/RovxBot/homelab-infra/wiki/Gitea).
GitHub remains primary for the cluster's Flux infrastructure repository.

Before that cutover, GitHub remained primary until an operator declared an incident. A 403/404 can be
an authentication outage; it does not authorize repository deletion or mirror
promotion. First preserve the last good state and review the source health.

## Safe exercise

The retained [failover exercise](../../ops/gitea/failover-exercise.py) is a
historical **mirror-only** drill. It requires an existing protected read-only
pull mirror and checks `mirror: true` before creating a scratch repository and
again at the end. Its unchanged default, `--source wowdecomp`, is incompatible
with that repository's current writable-primary role. Do not invoke it without
an explicit, verified eligible source or weaken the mirror guard to make it run.

Use a [localhost Service forward](codex-integration.md) for HTTP recovery.
Private SSH is available at `ssh://git@192.168.1.190:30222/...`; another healthy
node can serve the same NodePort. The existing WireGuard path reaches the LAN
without Cloudflare or public DNS. Check the authenticated host-key fingerprint
before accepting it. The source snapshots remain independent of Entra, and
`recovery-admin` is a separate local account.

Before any exercise, the operator must:

1. Explicitly select a source in `github-dr` from the script's fixed allowlist:
   `wowdecomp` or `wowdecomp15595`. Exclude every writable primary;
   `wowdecomp` is currently ineligible. The frozen reference is only a
   candidate: its retained role does not prove current exercise eligibility.
2. Through the trusted localhost recovery connection, use the dedicated
   source-read credential to inspect `GET /api/v1/repos/github-dr/<selected-name>`.
   Confirm the exact `full_name`, `mirror: true`, the protected read-only role
   and read-only source permissions. Verify the default branch, last accepted
   refs and any required commit in a scratch clone. For `wowdecomp15595`, that
   commit is `959b0b85c5f55c565328d7c3ca33027a65e41327`. Retain this evidence
   privately; do not publish credentials or private repository details.
3. Confirm existing protection bundles and an independently recoverable backup
   are retained. Keep source roles, refs, permissions and stopped upstream
   imports unchanged. Use a separate CI-owner credential scoped to disposable
   `ci/dr-test-*` targets; the source credential must remain read-only.

**If no eligible protected read-only mirror exists, or preflight cannot confirm
one, do not execute the exercise.** Do not re-enable mirroring, promote a
repository or change source protection to satisfy this prerequisite. A
role-aware successor needs separate review.

After preflight, the exercise creates `ci/dr-test-<UTC timestamp>`, imports from
internal Gitea into that writable copy, lists refs, clones locally, commits and
pushes a development branch, then creates and merges a native PR. All writes
belong to the scratch target; the production source receives only reads.
Select the verified source interactively rather than relying on the default:

```bash
# Load dedicated ci-owner and source-read tokens from secure storage into
# GITEA_TOKEN and GITEA_SOURCE_TOKEN; never put them in command arguments.
read -r -p 'Verified eligible protected mirror name: ' GITEA_DR_SOURCE
python3 ops/gitea/failover-exercise.py \
  --url http://127.0.0.1:13000 \
  --source "${GITEA_DR_SOURCE:?Complete mirror eligibility preflight first}" \
  --evidence /tmp/dr-exercise.json
```

Afterwards, verify the source is still read-only and compare its accepted refs
with the preflight record. The script's final mirror check alone does not prove
that refs are unchanged. Preserve source snapshots, bundles and recovery points.

### Optional generated CI checks

[generate-harness.py](../../ops/gitea/generate-harness.py) generates local CI
adaptations without editing the product checkout:

```bash
python3 ops/gitea/generate-harness.py /path/to/local/product /tmp/dr-harness
```

Only add `--workflows /tmp/dr-harness` to the explicit-source invocation above
after separately reviewing the generated workflows, read-only source-token
access and available runner labels. The generator reads `github-dr/wowdecomp`
at `origin/main` and, for the reference audit, `github-dr/wowdecomp15595` at the
pinned commit. The exercise's `--source` does not retarget those CI checkouts.
These optional workflows perform real Linux checks with GitHub blocked in
their pod template; a scratch PR merge alone is not evidence they passed.

Inspect the actual run and artifacts, rather than inferring success from a PR
merge. The job first proves GitHub is unavailable, then reads project/reference
Git and pinned action dependencies locally. The reference checkout must resolve
`959b0b85c5f55c565328d7c3ca33027a65e41327`. The artifact round-trip deletes its
original file, downloads from Gitea and checks its SHA-256. Lifecycle probes
exercise cancellation and one-minute timeout; verify their pods are removed.
See the [operations ledger](operations.md) for historical recorded runs and the
independent restore result. Neither those runs nor a new scratch-only exercise
establish a fresh production restore or writable-primary recovery acceptance.

## Production promotion

These retained gates come from GitHub-primary recovery planning.
`wowdecomp` is already writable-primary; do not repeat its
promotion or resume imports. Any future production promotion requires separate
operator review and recovery verification. The scratch exercise authorizes no
production changes.

1. Declare GitHub unavailable and record the last accepted refs, metadata sync
   time and last verified off-host snapshot. Stop the selective CronJob through
   Git and reconcile. Preserve the source guard and historical bundles.
2. Take a cold backup and verify it can be restored to new storage. Clone and
   fsck the protected repositories and verify required commits/LFS objects.
3. Prefer creating a new writable organization/repository from the last accepted
   local source, retaining the original DR pull mirror as evidence. Reassign
   developer/team permissions deliberately. For an intentional in-place
   promotion, turn off mirror status in the Gitea administrator UI only after
   the scheduler is stopped and a verified recovery point exists.
4. Update developer remotes, branch protection and native PR workflows. Add
   `.gitea/workflows` from the reviewed adaptation, the read-only source secret,
   local action URLs and only runner labels backed by real hosts. Enable Actions
   in the writable repository. Original GitHub workflows are historical input.
5. Verify clone/fetch/push, a native PR merge, CI and artifact retrieval through
   the LAN/WireGuard path. Monitor storage, database, runners and backups.

Do not resume upstream pulls into active local development. When GitHub returns,
compare histories in scratch clones, preserve local branches and use a reviewed
merge or repository migration. Never force-push recovery work back to GitHub
automatically. Re-establish mirrors only after agreeing which history is primary.

## Infrastructure bootstrap without GitHub

The live root Flux GitRepository intentionally still points to GitHub. Existing
workloads continue running during a source outage, but new reconciliation cannot
rely on fetching that repository. Do not switch the primary Flux source to a
Gitea server that is itself waiting for Flux to restore it.

The recovery input set contains an infrastructure Git bundle/archive, the exact
Gitea chart, tool binaries and a Talos OCI image cache for Gitea, PostgreSQL,
guard, mirror, runner, toolchain, backup and Flux controllers. It is stored under
the separate `gitea-bootstrap-inputs` Restic tag. All seven application images
are also pre-pulled on the eight nodes using `cache-images.py` with
IfNotPresent. Node image garbage collection can remove cached data, so the
off-host OCI input set is required as well.

1. Retrieve `gitea/bootstrap/gitea-recovery-2026-10-31.tar.gz.gpg` from the
   Backblaze account console, independently of Restic. Verify its checksum and
   decrypt with the passphrase stored separately in Bitwarden. Recover the
   Restic credential Secret and the SOPS age identity into an empty mode-0700
   recovery directory. Never extract these into a Git checkout or display them.
2. Recover Kubernetes/Talos administrative access from the separate encrypted
   Talos archive if Entra or the cluster is unavailable. This plan does not
   replace the Talos/etcd/Longhorn disaster recovery runbooks.
3. Restore the bootstrap-inputs snapshot using the recovered Restic password and
   B2 credentials. Verify checksums, clone the infrastructure Git bundle and
   inspect its recorded revision. Keep the public GitHub source unchanged while
   bringing up the recovery platform.
4. If registries are unavailable and node caches were lost, use the archived
   image cache from an independent LAN workstation. The default OCI export is
   a cache artifact: run `python3 ops/gitea/unpack-image-cache.py image-cache
   cache-flat` first, then point `talosctl image cache-serve` at `cache-flat`.
   Future exports can use `cache-create --layout=flat` directly. Serving the
   outer OCI artifact as a registry returns 404 and is not a recovery method.
   `talosctl image cache-cert-gen` supplies the TLS certificate/CA patch. Review
   and apply its registry-mirror patch only as an incident maintenance action,
   one node at a time; include docker.io, docker.gitea.com and ghcr.io. Keep TLS
   verification. This is an operator recovery step, not an automatic cluster
   registry/networking change.
5. Restore/apply pinned Flux controller manifests from the local checkout if
   they were lost. Suspend the Gitea app Kustomizations during manual recovery
   to prevent competing changes. Decrypt the saved SOPS Secrets through pipes
   using `SOPS_AGE_KEY_FILE`, then recreate namespaces/storage/database resources
   from local manifests. Preserve damaged PVCs and restore into new PVCs.
6. Use the archived Gitea 12.7.0 chart with `helm upgrade --install gitea
   ./charts/gitea-12.7.0.tgz --namespace gitea --values gitea-values.yaml` and the
   archived HelmRelease values. They reference existing Secrets. Restore the
   database, Gitea files and mirror state together, using the verified restore
   procedure. No GitHub source or action download is required for this step.
7. Bring up guard/mirror/runners from the local checkout, verify all acceptance
   checks, then establish an independent Git source for deliberate recovery
   reconciliation. Only after the restored Gitea is healthy should it be
   considered as an infrastructure source. Record and review the final source
   decision and remove temporary recovery overrides.

The image Dockerfiles use Ubuntu/Node inputs independent of GitHub. The exact
SDL archive is preserved in the toolchain image and can be supplied through
`SDL_URL` during a rebuild. Preserving the built OCI cache is the quickest
recovery path; an empty Docker cache is not an offline build environment.
