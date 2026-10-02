# Refresh one Longhorn RWX export

A live V1 engine upgrade can leave a healthy share-manager pod running the prior
image. After all managers and all volume engines are on 1.13.0, refresh the four
RWX exports one at a time. This restarts each NFS server; Longhorn 1.13's native
remount recovery also recreates managed client pods after the server is serving.
Do not refresh another export until its replacement clients and
replicas have passed the post-refresh checks.

The reviewed helper requires three healthy replicas on distinct nodes, fresh
Completed recovery backups for every original volume, matching current manager,
default engine and configured share-manager versions, fully converged engine and replica
processes, and a running export at its original endpoint. It records the original
PV, PVC, client controller, volume and ShareManager identities. It records client
counts, images and claim specifications, and requires managed ReplicaSet or
StatefulSet clients with native unexpected-detach recovery enabled. It checks the client's
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
not delete original volumes, replicas, PVCs, PVs or workload pods, and does not
use forced deletion. It waits for the native remount timestamp and replacement
clients started at or after that request, with the same controllers, images,
claim specifications and client counts. Longhorn skips client recreation when
the replacement server did not start strictly after the remount request; both
timestamps can fall in the same second. In that case the helper preserves the
exact original Ready clients and waits at least 35 seconds after the request
before accepting responsive mounts. It verifies stable ready clients for at
least five seconds, the replacement server image, original endpoint and storage
identities, healthy replicas, responsive NFS mounts and fresh recovery backups
before reporting completion. The helper never deletes workload pods itself.
Stop on any failed check and inspect the selected export before continuing.

The four exports are Skyfire storage, WotLK storage, wger media and wger static.
An older V1 instance-manager pod can still host an upgraded engine process;
that is expected native live-upgrade behavior. Do not delete a busy instance
manager to change its displayed image.

Reference: [V1 instance managers during upgrades](https://longhorn.io/docs/1.13.0/deploy/upgrade/instance-manager-pods-during-upgrade/).
Native remount behavior: [Longhorn 1.13 pod controller](https://github.com/longhorn/longhorn-manager/blob/v1.13.0/controller/kubernetes_pod_controller.go).
