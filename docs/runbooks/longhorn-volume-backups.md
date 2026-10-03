# Longhorn recovery backups for maintenance

The weekly system backup captures volume data and cluster configuration with
`volume-backup-policy: always`. It runs Sunday at 04:15 UTC (13:45 Adelaide
standard time or 14:45 daylight time). The guarded retention worker subsequently
keeps the newest completed remote backup per volume and the newest successful
system archive. See [remote retention](longhorn-latest-backup-retention.md).

Before engine upgrades, snapshot compaction or replica maintenance:

1. Confirm the manager rollout is complete, the backup target is available, and
   every attached volume has at least three healthy RW replicas on distinct
   nodes. Detached volumes must retain three healthy copies.
2. Capture a reviewed native SystemBackup with volume policy `always`. The
   initial repair used `ops/longhorn/pre-upgrade-system-backup.yaml`. The final
   1.13.0 recovery capture uses:

   ```sh
   kubectl create --dry-run=server -f ops/longhorn/post-upgrade-system-backup.yaml
   kubectl create -f ops/longhorn/post-upgrade-system-backup.yaml
   ```

   Only create the final capture after managers, engine and replica processes,
   and all four RWX exports have converged to 1.13.0 and original applications
   and claims remain healthy.
3. Wait for the SystemBackup to report `status.state: Ready` with the running
   Longhorn version. Independently check every current volume's `lastBackup`:
   its referenced Backup must be Completed at 100%, have no error, name the
   original volume, and have `snapshotCreatedAt` after this operation started.
   A Ready configuration-only archive is insufficient. Maintenance guards
   require the source snapshot capture to be no older than 24 hours.
4. Restore the new Gatus backup into a separate temporary three-replica volume
   and check SQLite integrity. Verify original PV/PVC UIDs and Bound status
   before and after. Remove only the verification resources after success;
   never restore over an original claim.
5. Keep current volume data, original claims, healthy replica copies and the
   newest verified remote recovery points. Retire older historical snapshots
   and backups only through the reviewed native retention helpers after their
   health, freshness, identity and CSI-reference checks pass. Local policy
   keeps two recent points without archiving older versions to B2.

The one-time manifests are outside Flux reconciliation. Reuse requires a new
unique name and a reviewed manifest. Native backup controllers can temporarily
attach an unused detached volume to capture it, then release the attachment.

These are live filesystem snapshots. Transactional applications may recover
from their logs after a restore, as they would after an unexpected shutdown.
The isolated SQLite check validates that selected backup; it does not establish
application-level consistency for every database.

References: [system backups](https://longhorn.io/docs/1.13.0/advanced-resources/system-backup-restore/backup-longhorn-system/),
[local retention](longhorn-snapshot-retention.md), and
[share-manager refresh](longhorn-share-manager-refresh.md).
