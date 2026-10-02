# Resume an idle volume's pending snapshot purge

Longhorn 1.11.1 can clear a stopped engine's current image. Its snapshot deletion
controller then interprets the empty image as an upgrade and postpones creating
its automatic attachment ticket. A marked parent can remain pending even though
the volume has three healthy retained copies and two protected restore points.

Use `scripts/longhorn-resume-idle-snapshot-purge.py` only for that idle-volume
case, after native retirement is already pending. The helper requires an unused
detached RWO volume, its original Bound PV/PVC, no active consumers or CSI
attachments, no native attachment tickets, complete manager and engine upgrades,
fresh completed backups for every volume, three healthy copies, a ready host,
compaction headroom and no CSI snapshot reference to the selected old parents.

```sh
volume=pvc-9f978c4b-ea57-4933-bd9d-6daa38f6d6dd
python3 scripts/longhorn-resume-idle-snapshot-purge.py "$volume" --node metal8
python3 scripts/longhorn-resume-idle-snapshot-purge.py "$volume" --node metal8 --apply
```

The helper creates one named native maintenance attachment with the frontend
disabled. No workload filesystem is mounted. Longhorn's snapshot controller
owns the pending purge. After the physical tree contains only the two protected
points and volume-head, the helper removes only its own ticket with a normal
detach request. It verifies the original volume and claim identities, healthy
copies, fresh recovery points and an empty attachment map in the detached state.
It never forces detachment, changes replica files, deletes volumes or claims,
or removes another controller's ticket.

The checker waits through native attaching and detaching transitions, including
the brief attached/unknown state before engine startup reports healthy. During
that wait it requires three retained healthy copies on distinct nodes, unchanged
engine identity and healthy unrelated volumes. It authorizes no further action
until normal attached health checks pass. A failed copy, degraded volume or
unexpected restore, migration or image change stops the check.

An error leaves the current state for inspection. If interrupted after attachment,
inspect the named `longhorn-idle-snapshot-maintenance` ticket and verify the same
volume and claims before removing that ticket through a normal native detach.
Never clear the whole attachment map to resolve this maintenance request.

Sources: [native attachment and detachment](https://github.com/longhorn/longhorn-manager/blob/v1.11.1/manager/volume.go),
[snapshot deletion controller](https://github.com/longhorn/longhorn-manager/blob/v1.11.1/controller/snapshot_controller.go),
and [engine-upgrade predicate](https://github.com/longhorn/longhorn-manager/blob/v1.11.1/controller/utils.go).
