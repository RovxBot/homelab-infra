# Recover a stale UUID on an empty Longhorn disk

Since 5 August, `metal0`, `metal2` and `metal3` have had stale disk UUIDs in
Longhorn Node status. Their existing
`longhorn-disk.cfg` files contain the new UUIDs, and their replica directories
are empty. Longhorn refuses to schedule these disks with
`DiskFilesystemChanged` until the recorded identity matches the filesystem.

Use this procedure only for an empty disk whose on-disk identity is known.
The [Longhorn node controller](https://github.com/longhorn/longhorn-manager/blob/v1.11.1/controller/node_controller.go)
compares the recorded UUID with the disk configuration before declaring the
disk ready. The repair updates that controller-owned status field; desired
disk configuration remains managed by Flux. It does not remove or recreate a
disk, write its configuration file, format a filesystem, or delete data.

The script requires all managers to finish their rollout, every active
volume to be healthy with at least its configured three RW replicas, no
replica or backing-image references to either UUID, zero scheduled data, and
an empty replica directory verified through Talos. A concurrent Node status
change causes the JSON patch to fail; repeat the read-only check before
retrying. The default invocation is read-only.

```bash
export KUBECONFIG="$HOME/.config/talos/cooked-k8s/kubeconfig-entra"
export TALOSCONFIG="$HOME/.config/talos/cooked-k8s/talosconfig-entra"

python3 scripts/longhorn-repair-empty-disk.py metal0 default-disk-081400000000
python3 scripts/longhorn-repair-empty-disk.py metal0 default-disk-081400000000 --apply
```

After the script verifies Ready, Schedulable and volume health, repeat for
`metal2` and then `metal3`, using `default-disk-1030800000000` on each. Stop
after any failed check. Never use this procedure to change the identity of a
disk with replica data or to bypass an unexplained mount problem.

Run the safety checks locally with:

```bash
python3 -m unittest discover -s scripts/tests -v
```
