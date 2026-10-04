# Newest remote recovery point per volume

Keep the newest completed B2 data backup for each Longhorn backup volume,
including the most recent recovery point of retired volumes. Keep the newest
successful system archive and the newest Restic photo snapshot. Retire older
history through each backup application's native garbage collection; shared
blocks referenced by the retained recovery point must survive.

A retired volume's last recovery point can be removed only after explicit
approval of its exact identity inventory. Automatic retention continues to
preserve last copies of newly retired volumes.

The weekly Longhorn data backup runs Sunday at 04:15 UTC with volume policy
`always`. The retention worker starts at 05:00 UTC and waits on Kubernetes
status for that same run to reach Ready. It requires every current volume to
have a new completed backup captured during the run, a fresh archive for the
running Longhorn version, complete manager and engine convergence, and three
healthy replicas. A failed or incomplete backup stops retention.

The worker performs no direct bucket inventory, reads no credentials and
writes no volumes, replicas or local snapshots. Longhorn's native controllers
own backup removal, locks and shared-block cleanup. The worker checks exact
backup UID/resourceVersion, CSI snapshot references and the protected newest
identity before each removal, waits for native finalizers and stops on any error. Retired volumes
are excluded from future automatic runs and keep their latest recovery point.

In-cluster Kubernetes requests are paced at five per second without bursts.
Throttled reads retry at most five times, honoring a numeric `Retry-After` up
to 30 seconds or using bounded exponential delays. Deletes and other API
errors stop immediately; they are never retried automatically.

Review one volume before applying its exact protected backup name:

```sh
python3 scripts/longhorn-retire-backup-history.py VOLUME
python3 scripts/longhorn-retire-backup-history.py VOLUME --expected-latest BACKUP --apply
```

Use `--system-archives --expected-latest SYSTEM --apply` only with a fresh Ready
system backup that includes completed volume data. The one-time maintenance
run also reviews all retired volume recovery points.

For a long manual cleanup, run the worker as a Kubernetes Job so closing the
client does not interrupt it. Use the preview-only template
[`reviewed-backup-retention-job.yaml`](../../ops/longhorn/reviewed-backup-retention-job.yaml)
with the exact deployed retention-code ConfigMap and a separate immutable
ConfigMap containing `plan.json`. This template is not deployed by GitOps.

The reviewed plan contains `originalVolumes` (name to UID),
`originalClaimsVerified` (the number checked independently before the job),
`plans` (each volume's `volume`, `live`, `backupVolumeName`, `backupVolumeUID`,
`keep`, `keepUID`, and `older` name-to-UID map), `system` (name and UID), and
`olderSystems` (name-to-UID map). Include the successful isolated restore result
as `restoreVerification`, including its exact `sourceBackupUID`. Complete the
restore-copy cleanup and verify original claims and local compaction first.

Run the preview job and inspect its successful logs. Create a separately named
apply job with the same immutable plan and append `--apply` to its command.
Every native deletion checks the full plan again, including all protected live
and retired backups and the restore source. A changed identity, new backup,
expired 24-hour recovery window, or native error stops the job. Restarting with
the same plan permits already completed deletions without broadening the plan.
Do not refresh the recovery capture while a retention job is still running;
after it stops, take a fresh capture and review a new plan if needed.

For approved last-copy retirement, use the manual-only helper:

```sh
python3 scripts/longhorn-retire-retired-volumes.py --reviewed-plan PLAN.json
python3 scripts/longhorn-retire-retired-volumes.py --reviewed-plan PLAN.json --apply
```

Extend the completed history plan with `deletionAuthorized: true`, the exact
approval text in `authorization`, the original PV/PVC objects in
`originalClaims`, and `retiredRecoveryPoints`. Each approved point contains
`volume`, `backup`, `backupUID`, `backupVolume`, and `backupVolumeUID`, matching
the reviewed retired keeper. Keep the current-volume identities, restore
proof and full system archive unchanged. No historical deletes may remain.

The helper rechecks the existing 24-hour recovery gates, every original Bound
claim and all protected backups before each native BackupVolume deletion. It
blocks live storage, linked clones, CSI backup references, metadata-only
deletion labels and missing native ownership/finalizers. Longhorn removes the
retired remote volume; Kubernetes then clears its owned backup metadata. The
helper waits serially and stops on errors or a timeout. Resume with the same
plan to observe interrupted native cleanup; never force finalizers. It grants
no standing permissions and does not run automatically.

B2 lifecycle deletes hidden or superseded object versions after one day under
the approved prefixes. Current objects never expire by upload age. Provider
cleanup and usage counters can lag. Keep storage alerts useful and allow room
for new backups; storage caps stop uploads and do not remove billable storage.

Native deletion reference: [Longhorn 1.13 BackupVolume controller](https://github.com/longhorn/longhorn-manager/blob/v1.13.0/controller/backup_volume_controller.go).

References: [B2 lifecycle](longhorn-b2-lifecycle.md),
[local snapshot retention](longhorn-snapshot-retention.md), and
[photo backup retention](immich-photo-backup-retention.md).
