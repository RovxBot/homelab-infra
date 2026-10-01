# Recover metal5's crowded default disk

The default disk on metal5 has about 22% free space, below Longhorn's 25%
minimum. Its five remaining replicas include large Radarr and Sonarr snapshot
chains. The extra disk remains healthy. Evacuate the default disk using
Longhorn's native disk eviction rather than deleting data or weakening the
free-space threshold.

Before merging the evacuation, complete and verify the recovery points described
in [volume backups](longhorn-volume-backups.md). Every attached volume must have
at least three healthy RW replicas on distinct nodes; all managers must be ready.
The repaired control-plane disks provide additional replacement capacity.

The Node manifest disables new placement on this disk and requests eviction.
Longhorn [creates an extra replica and then removes an evicting healthy copy only
when the number of healthy replicas exceeds the configured count](https://github.com/longhorn/longhorn-manager/blob/v1.11.1/controller/volume_controller.go).
Keep the conservative one-per-node rebuild limit. Observe every affected volume
during reconciliation; stop subsequent work if any loses its required healthy
copies. Never remove source replica directories manually.

After merge:

1. Verify the live disk has `allowScheduling: false` and
   `evictionRequested: true`.
2. Wait until no Replica CR references this disk UUID, its scheduled-replica map
   is empty, and its scheduled storage is zero. Confirm all affected volumes
   still have the configured number of healthy replicas on distinct nodes.
3. Check actual free space exceeds the 25% threshold with margin.
4. In a separate PR, clear eviction and enable disk scheduling again. Verify
   readiness, schedulability, free space and volume health after it reconciles.

If evacuation stalls, leave the source copies intact and investigate replacement
capacity. Stop eviction through a reviewed change if necessary. Clearing the
eviction flag cancels further evacuation; copies already moved remain healthy
on their replacement disks. Disk paths, UUIDs, storage reservations, snapshot
retention and volume replica counts do not change.
