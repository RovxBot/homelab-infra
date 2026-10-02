# Refresh one Longhorn RWX export

A live V1 engine upgrade can leave a healthy share-manager pod running the prior
image. After all managers and all volume engines are on 1.13.0, refresh the four
RWX exports one at a time. This restarts each NFS server; clients can temporarily
wait for service recovery. Do not refresh another export until its clients and
replicas have passed the post-refresh checks.

The reviewed helper requires three healthy replicas on distinct nodes, fresh
Completed recovery backups for every original volume, matching current manager,
default engine and configured share-manager versions, fully converged engine and replica
processes, and a running export at its original endpoint. It records the original
PV, PVC, client pod, volume and ShareManager identities. It checks the client's
NFS filesystem before and after the restart using read-only `stat` probes.
The desired share-manager image comes from the ready manager DaemonSet's explicit
`--share-manager-image` command argument and must match its manager image version.
Longhorn does not expose that image as a `share-manager-image` Setting.

Run the dry check first, then apply the same selected volume:

```sh
image=docker.io/longhornio/longhorn-share-manager:v1.13.0
volume=pvc-acb570eb-435e-4435-bb89-6222588d551e
python3 scripts/longhorn-refresh-share-manager.py "$volume" --image "$image"
python3 scripts/longhorn-refresh-share-manager.py "$volume" --image "$image" --apply
```

The helper normally deletes only the existing share-manager pod, with exact UID
and resource-version preconditions. The native controller recreates it. It does
not delete or replace original volumes, replicas, PVCs, PVs or client pods, and
does not use forced deletion. It requires the replacement image, the same export
endpoint and original resource identities, healthy replicas, responsive NFS
mounts, ready clients and a fresh recovery backup before reporting completion.
Stop on any failed check and inspect the selected export before continuing.

The four exports are Skyfire storage, WotLK storage, wger media and wger static.
An older V1 instance-manager pod can still host an upgraded engine process;
that is expected native live-upgrade behavior. Do not delete a busy instance
manager to change its displayed image.

Reference: [V1 instance managers during upgrades](https://longhorn.io/docs/1.13.0/deploy/upgrade/instance-manager-pods-during-upgrade/).
