# Newest remote recovery point per volume

Keep the newest completed B2 data backup for each Longhorn backup volume,
including the most recent recovery point of retired volumes. Keep the newest
successful system archive and the newest Restic photo snapshot. Retire older
history through each backup application's native garbage collection; shared
blocks referenced by the retained recovery point must survive.

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

Review one volume before applying its exact protected backup name:

```sh
python3 scripts/longhorn-retire-backup-history.py VOLUME
python3 scripts/longhorn-retire-backup-history.py VOLUME --expected-latest BACKUP --apply
```

Use `--system-archives --expected-latest SYSTEM --apply` only with a fresh Ready
system backup that includes completed volume data. The one-time maintenance
run also reviews all retired volume recovery points.

B2 lifecycle deletes hidden or superseded object versions after one day under
the approved prefixes. Current objects never expire by upload age. Provider
cleanup and usage counters can lag, so the account cap is set after completed
uploads and the measured footprint falls below its limit. Storage caps stop
new uploads; they do not remove existing billable storage.

References: [B2 lifecycle](longhorn-b2-lifecycle.md),
[local snapshot retention](longhorn-snapshot-retention.md), and
[photo backup retention](immich-photo-backup-retention.md).
