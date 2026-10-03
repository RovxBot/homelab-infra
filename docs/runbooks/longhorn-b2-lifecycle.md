# B2 object versions and Longhorn backup retention

The reviewed bucket-side policy in `ops/longhorn/b2-object-lifecycle.json`
preserves the current version of every object under `cooked-k8s/longhorn/`
and `cooked-photos/restic/immich-uploads/`, and
deletes superseded or hidden versions after one day. It does not expire current
objects by upload age. B2 performs lifecycle deletion internally, without a
cluster cleanup job listing and deleting those object versions.

Review the current bucket rule and revision:

```sh
python3 scripts/longhorn-b2-object-lifecycle.py --bucket cooked-k8s
python3 scripts/longhorn-b2-object-lifecycle.py --bucket cooked-photos
```

After the policy PR is merged, apply the reviewed revision with
`--apply --expected-revision REVISION`, processing one bucket at a time.
The helper verifies the live Longhorn or photo backup destination,
keeps credentials in memory, preserves unrelated prefix rules,
refuses conflicting overlapping rules, and uses B2's `ifRevisionIs` condition.
It lists bucket configuration only, never backup data objects. Re-run the dry
run to confirm the installed rule.

An object version is not a complete Longhorn backup. Backup manifests have
unique names and reference shared, content-addressed blocks that can be much
older than the newest backup. B2 cannot decide which blocks remain referenced.
Age-based expiration of current objects can make the newest backup unrestorable.

Keep one completed recovery backup **per volume** and the newest system archive
through Longhorn's native backup retention and deletion, after replacement
backups complete. Keep the latest backup's local source snapshot as one of the
two local points so later backups can compare incrementally. Old recovery points
are intentionally retired under this policy. Never replace native backup-store
garbage collection with bucket upload-age expiration.

Bucket lifecycle lets B2 reclaim hidden versions left by native backup
deletion. Without this rule, hidden data can remain billable even after its
backup set is retired. It reduces old-version storage and client cleanup requests; it
does not remove the reads and writes needed to create, verify, lock and retire
Longhorn backup sets. Reduce idle inventory polling separately from backup
frequency. Explicit refresh is required when periodic polling is disabled.

References: [B2 lifecycle rules](https://www.backblaze.com/docs/en/cloud-storage-lifecycle-rules),
[conditional bucket updates](https://www.backblaze.com/apidocs/b2-update-bucket), and
[Longhorn recurring backup retention](https://longhorn.io/docs/1.13.0/snapshots-and-backups/scheduling-backups-and-snapshots/).
