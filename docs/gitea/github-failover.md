# GitHub failover

GitHub remains primary until an operator declares an incident. A 403/404 can be
an authentication outage; it does not authorize repository deletion or mirror
promotion. First preserve the last good state and review the source health.

## Safe exercise

`ops/gitea/failover-exercise.py` accepts only the two protected source names. It
creates `ci/dr-test-<UTC timestamp>`, imports from internal Gitea into a writable
copy, lists refs, clones locally, commits a development branch, pushes it,
creates a native PR and merges it. It confirms the production source is still
a pull mirror. The optional generated workflows perform real Linux checks with
GitHub blocked in their pod template.

```bash
# Load dedicated ci-owner and source-read tokens from secure storage into
# GITEA_TOKEN and GITEA_SOURCE_TOKEN; never put them in command arguments.
python3 ops/gitea/generate-harness.py /path/to/local/product /tmp/dr-harness
python3 ops/gitea/failover-exercise.py \
  --url http://127.0.0.1:13000 --workflows /tmp/dr-harness \
  --evidence /tmp/dr-exercise.json
```

Use a localhost Service forward for HTTP recovery. Private SSH is available at
`ssh://git@192.168.1.190:30222/...`; another healthy node can serve the same
NodePort. The existing WireGuard path reaches the LAN without Cloudflare or
public DNS. Check the authenticated host-key fingerprint before accepting it.
The source snapshots remain independent of Entra, and `recovery-admin` is a
separate local account.

Inspect the actual run and artifacts, rather than inferring success from a PR
merge. The job first proves GitHub is unavailable, then reads project/reference
Git and pinned action dependencies locally. The reference checkout must resolve
`959b0b85c5f55c565328d7c3ca33027a65e41327`. The artifact round-trip deletes its
original file, downloads from Gitea and checks its SHA-256. Lifecycle probes
exercise cancellation and one-minute timeout; verify their pods are removed.
See `operations.md` for recorded runs and the independent restore result.

## Production promotion

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
   `talosctl image cache-serve` input set from an independent LAN workstation.
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
