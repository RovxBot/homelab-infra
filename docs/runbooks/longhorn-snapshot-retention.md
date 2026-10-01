# Keep two recent local Longhorn restore points

Local snapshots provide quick rollback. Completed volume backups store their
data independently in B2, so retaining months of local history is unnecessary.
Radarr and Sonarr each had 34 local points from December 2025 onward, with
snapshot chains consuming roughly 28 GiB and 27 GiB per replica. This contributes
to disk pressure and lengthens maintenance.

The local policy retains two recent snapshots per volume. Older local historical
versions are intentionally retired, including points without a retained B2
backup. Do not archive those older versions or delete existing B2 backups.
Apply this policy through the guarded one-volume-at-a-time procedure below.
Future cleanup requires the same backup, replica-health and CSI-reference gates.

For the initial cleanup:

1. Verify fresh Completed backups for every current volume and the one-time
   SystemBackup Ready, following [the backup runbook](longhorn-volume-backups.md).
   The isolated Gatus backup restore passed SQLite integrity checking.
2. Complete engine upgrades to the running manager's default engine first.
   Every attached volume must have at least three healthy RW replicas on
   distinct nodes, with no rebuild, restore or migration in progress. Each
   replica disk must have temporary space for native snapshot coalescing.
   The helper initially allows one additional volume plus 10%. On constrained
   replicas it reads allocated blocks with `stat`, then bounds the extra space
   needed to fill each removed parent before Longhorn replaces its child.
   It retains the 10% margin and does not edit any replica file.
3. Process exactly one volume at a time after this PR is merged. For example:

   ```sh
   volume=pvc-49027cc6-2e4d-474b-99d5-be64f0d48c3b
   python3 scripts/longhorn-prune-snapshots.py "$volume" \
     --snapshot-name "post-upgrade-20261001-$volume"
   python3 scripts/longhorn-prune-snapshots.py "$volume" \
     --snapshot-name "post-upgrade-20261001-$volume" \
     --apply --retire-older-history
   ```

4. The helper takes a new maintenance snapshot and retains it alongside the
   latest completed backup snapshot. It refuses to delete a selected point
   referenced by CSI VolumeSnapshotContent and waits for native compaction to
   finish, checking volume health throughout. The backup's snapshot capture time
   must be within 24 hours; an old snapshot backed up today is insufficient.
5. Verify the two retained points are ready, the selected older points are gone,
   and healthy replicas and B2 recovery points remain. Stop on any error before
   processing another volume. Snapshot retirement cannot be undone unless a
   corresponding remote backup exists; never manually remove replica files.
6. Reassess disk space. If metal5 now exceeds the 25% free-space minimum with
   margin, no replica evacuation is needed. If pressure remains, use a separate
   reviewed native disk evacuation that builds replacements before removing
   source copies.

After subsequent weekly backups complete, use the same reviewed procedure to
keep local history bounded. Do not add an unattended global `snapshot-delete`
job: it affects all snapshot kinds beyond its count, including manual and CSI
snapshots, without this helper's safety gates. Maintenance that needs more than
two local points must adjust the retention policy through review first. This
procedure does not create data backups or change remote backup retention.

References: [snapshot retention](https://longhorn.io/docs/1.13.0/snapshots-and-backups/scheduling-backups-and-snapshots/),
[space management](https://longhorn.io/kb/space-consumption-guideline/), and
[native snapshot deletion](https://longhorn.io/docs/1.13.0/snapshots-and-backups/setup-a-snapshot/).
The V1 allocation calculation follows [the 1.11.1 native purge implementation](https://github.com/longhorn/longhorn-engine/blob/v1.11.1/pkg/sync/rpc/server.go).
