# Kubernetes CSI snapshot API

Longhorn deploys its CSI snapshot sidecar, but the Kubernetes distribution must
provide the snapshot CRDs and common snapshot controller. Without those CRDs,
the sidecar repeatedly fails to list VolumeSnapshotClass and VolumeSnapshotContent
even while its Pod is Ready.

`infra/snapshot-controller` installs the three stable snapshot CRDs and two
leader-elected controller replicas. It uses upstream external-snapshotter v8.6.0,
matching the snapshot sidecar shipped by both Longhorn 1.12.1 and 1.13.0. These
regular-snapshot CRDs are identical to v8.5.0's and support the existing 1.11.1
sidecar during the staged upgrade. Kubernetes 1.34 meets the upstream minimum
version of 1.25. Group snapshots remain disabled, with no group-snapshot RBAC.

CRD files are vendored unchanged from the upstream tag. Kustomize adds
`kustomize.toolkit.fluxcd.io/prune: disabled` so removing this directory cannot
silently prune snapshot API objects. The controller image is pinned to its
verified multi-platform registry digest. Controller RBAC follows upstream with
unused group-snapshot permissions removed; its containers run without root or
Linux capabilities.

Installing these components creates no VolumeSnapshotClass, VolumeSnapshot,
Longhorn snapshot, recurring job, or B2 backup. Creating snapshots remains an
explicit workload action. Longhorn's native snapshot and backup controllers
continue to own their existing restore points.

After reconciliation, verify that all three CRDs are Established, both controller
replicas are available, and the Longhorn CSI snapshotter leader no longer reports
missing snapshot resources. Check that existing PV/PVC identities and workload
readiness are unchanged.

Sources:

- [Longhorn CSI snapshot prerequisites](https://longhorn.io/docs/1.13.0/snapshots-and-backups/csi-snapshot-support/enable-csi-snapshot-support/)
- [external-snapshotter v8.6.0](https://github.com/kubernetes-csi/external-snapshotter/releases/tag/v8.6.0)
- [Upstream CRDs](https://github.com/kubernetes-csi/external-snapshotter/tree/v8.6.0/client/config/crd)
- [Upstream controller manifests](https://github.com/kubernetes-csi/external-snapshotter/tree/v8.6.0/deploy/kubernetes/snapshot-controller)
- [Flux prune control](https://fluxcd.io/flux/components/kustomize/kustomizations/#prune)
