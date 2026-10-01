# Longhorn volume backups before maintenance

The October 2026 audit found that the weekly system backup saved configuration
only. Several current volumes had never been backed up, and other volumes' last
backups dated to May. The weekly job now uses `volume-backup-policy: always`,
which captures each volume's data as well as system configuration. Its B2 target,
credentials, Sunday schedule, concurrency and retention remain unchanged.

Before engine upgrades or replica evacuation, establish fresh recovery points:

1. Confirm all managers are ready, the backup target is available, and every
   attached volume is healthy with at least three RW replicas.
2. After this correction is merged, create the reviewed one-time backup:

   ```sh
   kubectl create --dry-run=server -f ops/longhorn/pre-upgrade-system-backup.yaml
   kubectl create -f ops/longhorn/pre-upgrade-system-backup.yaml
   ```

3. Wait for `pre-upgrade-20261001` to report `status.state: Ready`. Also verify
   each of the 25 current volumes has a new `status.lastBackup`, with the
   referenced Backup reporting `Completed`, 100% progress and no error. Check
   `lastBackupAt` is later than the start of this operation. Stop on any failed
   or missing volume backup; a Ready configuration-only backup is insufficient.
4. Retain the recovery points throughout maintenance. Do not delete the original
   volumes, their replicas, snapshots or backups to resolve capacity problems.

The one-time manifest is deliberately outside Flux's Kustomizations: it is a
reviewed operation, not a resource to recreate or prune during reconciliation.
Reuse requires a new unique name and a reviewed manifest. Use Longhorn's native
backup controllers, which can temporarily attach a detached volume to take its
snapshot and then release that attachment.

These are live filesystem snapshots. Applications with a transaction log must
recover consistently after a restore, just as after an unexpected shutdown.
Validate restorability on a separate test volume; never restore over an original
claim as an upgrade check.

References: [system backup policy](https://longhorn.io/docs/1.11.1/advanced-resources/system-backup-restore/backup-longhorn-system/)
and [snapshot attachment handling](https://github.com/longhorn/longhorn-manager/blob/v1.11.1/controller/snapshot_controller.go).
