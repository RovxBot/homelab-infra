# Explicit B2 backup inventory refresh

The default Longhorn BackupTarget has `pollInterval: "0s"` to disable its idle
five-minute inventory scans. Backup creation and deletion still request their
own metadata synchronization and retain the remote reads, writes and locks
needed for those operations. This change does not stop backup jobs, uploads or
restore operations, and it deletes no backup data.

Refresh the inventory explicitly in the Longhorn UI before investigating
changes made outside this cluster or restoring externally created backups.
The target's last-sync status is a recorded observation, not a continuous
availability check. Failed-backup cleanup that depends on periodic inventory
polling also requires an explicit refresh.

Minimizing request traffic does not enforce the storage budget. As of October
2026, B2 pay-as-you-go Class A/B/C API calls are free; storage, tax, exchange
rates and downloads beyond the free allowance determine the account cost.
The requested limit is AUD $5/month for the whole account, including photos.
Retaining fewer backup sets must use native Longhorn or Restic cleanup so
blocks referenced by the newest recovery point remain available. Bucket
upload-age expiration is unsuitable for either shared-block backup store.

References: [backup target configuration](https://longhorn.io/docs/1.13.0/snapshots-and-backups/backup-and-restore/set-backup-target/),
[native polling and explicit sync](https://github.com/longhorn/longhorn-manager/blob/v1.11.1/controller/backup_target_controller.go),
and [B2 pricing](https://www.backblaze.com/cloud-storage/pricing).
