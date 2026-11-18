# Backup and restore

The dedicated Restic repository is
`s3:https://s3.us-east-005.backblazeb2.com/cooked-k8s/gitea/restic`.
Its password and bucket credentials are in `secrets/gitea-backup.enc.yaml`.
This prefix is independent of both Longhorn backup data and Immich retention.
Longhorn's three replicas and retained PVCs are availability measures, not
off-cluster backups.

## Capture

CronJob `gitea-cold-backup` runs at **02:15 Australia/Adelaide** after the initial
restore gate has passed. Its script verifies repository access and staging
space before stopping Gitea and the source guard. The retired metadata
console must already be at zero replicas and stays stopped. This
causes a planned Gitea outage, including the off-site upload. The initial
906 MiB capture took about twelve minutes; size and upload speed determine
future maintenance-window duration. PostgreSQL stays running; with all writers
stopped, a custom-format `pg_dump` and role definitions correspond to the copied
repositories, LFS, artifacts, Gitea configuration, guard refs/bundles and mirror
SQLite database. Role definitions omit password hashes; recover passwords from
the encrypted Secret archive instead.

The app's `gitea` database role is not a superuser and cannot create roles or
databases. A separate `gitea_admin` is used only by PostgreSQL initialization and
backup. PostgreSQL's original bootstrap role must remain a superuser; on the
initial installation it was renamed and ownership of application objects was
transferred to the new limited role. Changing POSTGRES_USER alone does not
migrate an existing volume. `database-init.sh` handles empty volumes only.

Gitea, guard and mirror pods share a node to make their RWO volumes available to
the cold backup Job. Mirror and backup use the same supplementary volume group.
The backup account can scale only these three deployments, inspect pods and
list namespace Jobs to detect overlapping maintenance;
it cannot read arbitrary Secrets or alter other workloads. Recovery Secrets
are explicitly projected into the encrypted backup.
The guard and mirror manifests omit `spec.replicas` so periodic Flux
reconciliation respects the backup's scale operations. Helm drift correction
is not enabled for Gitea. Do not change that setting without coordinating the
cold-backup maintenance window.

Every capture verifies the dump's table of contents, records its SHA-256, runs
Restic backup and repository integrity checking, then applies **14 daily,
8 weekly and 12 monthly** retention to `cooked-gitea`/`gitea-cold` only.
Pruning is deliberate maintenance, never an automatic side effect of a failed
capture. Back up images and bootstrap inputs with their separate tag as well.

```bash
# First installation ONLY; do not initialize over an existing repository.
# Create a Job from the CronJob and set container args to [init], then inspect
# its completion before enabling normal captures.
kubectl -n gitea create job --from=cronjob/gitea-cold-backup \
  gitea-backup-manual-$(date -u +%Y%m%d%H%M%S)
kubectl -n gitea logs job/NAME
```

The supervisor writes a mode-0600 kubeconfig to the existing `/work` emptyDir.
It uses `https://kubernetes.default.svc:443`, the mounted cluster CA and a
`tokenFile` reference to the rotating ServiceAccount token. Discovery cache also
stays under `/work`; no token is copied to argv, logs or a persistent volume.
This works with disabled service links, UID 1000 and the read-only root filesystem.

Before any scale-down, it reads all three deployments (expected replicas:
Gitea=1, protection=1, mirror=0), checks scale/get/patch/update and pod list/watch
permissions, lists active namespace Jobs, checks Restic connectivity and locks,
and sizes the database export. Staging requires at least 1 GiB free and twice
the current database size plus 128 MiB, within both node free space and the
3 GiB emptyDir budget. Preflight failures leave application replicas untouched.
The database-size query is a read-only PostgreSQL operation.

`concurrencyPolicy: Forbid` covers scheduled runs. An atomic directory lock at
`/protection/backup.lock` also excludes overlapping manual backup Jobs. Planned
maintenance must first check for backup Jobs/Pods and this lock, then create
`/protection/maintenance.lock` before starting; remove it on completion. Do not
race a backup start or run competing storage/chart maintenance. Restic locks
and other active namespace Jobs also stop preflight. A stale backup lock after
SIGKILL/node loss fails safely: an operator must establish that no backup
process/Job/Pod remains and no capture is writing before removing **only the
empty lock directory**. Do not remove Restic locks without separately proving
that their owning process is gone.

Scale intent is tracked before each API call, including a response lost after
the server accepted it. EXIT/TERM/INT cleanup stops capture before restoring
`gitea` and `gitea-protection` to one replica; it never starts the mirror. Requests
are bounded to ten seconds, with a twelve-second whole-command limit for
scaling (two-second forced kill). Failed restoration gets two cleanup attempts.
Any failed restoration attempt leaves a nonzero result and failure marker even
if a retry recovers. Persistent API failure cannot guarantee recovery: the Job
fails visibly and requires the manual procedure in `operations.md`. SIGKILL or
node loss can bypass traps. The backup Pod must exit to release its RWO mounts;
replica restoration acknowledges desired counts, not readiness. Verify actual
availability after Pod exit.

`/backupz` fails immediately after a recorded failure, after 48 hours without
success, or below 1 GiB free protection space. Only a successful capture,
Restic integrity check, retention and successful initial replica restoration
advance the success marker and clear the failure marker. A later cleanup
failure writes a new failure marker. Also inspect failed Jobs: a pod which
never starts cannot write a marker. Deleting a failed Job does not clear the
backup incident. Production verification, including the temporary outage,
requires explicit authorization; see the coordinated
[repair runbook](../runbooks/cluster-alert-repairs.md).

## Isolated verification

```bash
python3 ops/gitea/start-restore-test.py
kubectl -n gitea get jobs,pvc -l app=gitea-restore-test
kubectl -n gitea logs job/NAME -c restore
kubectl -n gitea logs job/NAME -c verify
```

The tool creates a **new** retained 10 GiB PVC and a Job, with no production
volume mounts or Kubernetes token. It restores and verifies the latest tagged
Restic snapshot, checks the SQL checksum, initializes an isolated PostgreSQL 17
server listening only on loopback, restores globals/database and records table
counts. A second container fscks every hosted/protected Git repository, compares
all guard refs with the saved accepted state, checks hosted ref provenance and
the reference SHA, and
verifies every historical bundle/checksum. Evidence remains on the test PVC.

The source and destination schedules can be out of phase. A backup preserves
both repositories; the verifier reports any hosted ref lag at capture and
requires its old branch tip to be an ancestor of the restored accepted tip.
Changed tags, unexpected hosted refs or missing/corrupt accepted refs fail.
This distinguishes a faithful restore of a lagging mirror from lost history.
Save the logs and evidence before explicit test-resource cleanup. Retain means
deleting a PVC does not reclaim its underlying volume automatically.

For a real recovery, freeze scheduled sync, preserve damaged volumes and restore
to new PVCs first. Restore the database with the same PostgreSQL major version
and Gitea image as the snapshot. Recover the application Secrets before starting
Gitea; its internal/LFS/JWT keys and mirror encryption secret must match the
database and files. Validate Git, issues/comments, LFS if present and downloaded
artifacts before changing Services or publishing a hostname. Re-register a
runner from the recovered organization token if its registration PVC was lost.
Restore application, guard and mirror files with UID/GID 1000 and initialize
PostgreSQL with UID/GID 999. The isolated test restores files as UID 999 for
verification; its PVC is evidence, not a ready-to-mount production replacement.
Do not overwrite production PVCs in place or assume a Helm rollback reverses a
database migration.

## Independent recovery credentials

`export-recovery.py OUTPUT.gpg` streams the six relevant Kubernetes Secrets and
the existing SOPS age identity directly into a GPG AES256 archive. No plaintext
archive/key is written to disk. The operator enters and stores an independent
passphrase in Bitwarden. Verify it using:

```bash
python3 ops/gitea/export-recovery.py OUTPUT.gpg --verify
sha256sum --check OUTPUT.gpg.sha256
```

Keep verified ciphertext off the cluster, including a copy outside GitHub and
outside the Restic repository it unlocks. Store the independent passphrase
separately. The private `homelab-secrets` repository already holds separately
encrypted Talos recovery material with a Bitwarden passphrase. Verify that
archive independently before relying on it; its README does not establish that
the newer SOPS identity is included. Never encrypt the only copy of the age key
solely to itself or keep the only Restic password inside its own repository.

The 31 October archive was independently decrypted/verified and uploaded,
downloaded and checksum-verified at
`cooked-k8s/gitea/bootstrap/gitea-recovery-2026-10-31.tar.gz.gpg` with its checksum.
Use the Backblaze account console to retrieve it after a cluster loss.
`upload-recovery.py` is restricted to these encrypted bootstrap objects and
refuses to replace different contents. This object is outside Restic, so its
password is not needed to retrieve the archive containing that password.
