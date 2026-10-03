# Serialized Longhorn V1 engine upgrades

Updating the Helm chart updates managers and deploys the matching engine image.
Existing volume engines remain on their previous image when automatic engine
upgrades are disabled. Upgrade managers one supported minor version at a time,
then finish every volume engine before moving managers to the next minor.

Use `scripts/longhorn-upgrade-engines.py` for exactly one volume at a time.
The default is a dry run. It requires a completed manager rollout, a target
matching the current manager and default engine, the target image deployed on
the required nodes, and no other engine upgrade or backup in progress.
Every attached volume must retain its configured healthy replicas on distinct
nodes (at least three), without rebuilding replicas. Detached volumes must
retain at least three healthy copies. Restore, clone and migration operations
block maintenance.

The selected volume must have a completed, error-free remote backup whose
source snapshot was captured in the last 24 hours. A recently completed upload
of an old snapshot does not qualify. Before beginning a maintenance series,
also verify the pre-upgrade SystemBackup is Ready and all current volumes have
fresh completed backups. Keep existing backups and their credentials intact.

```sh
kubectl -n longhorn-system get settings.longhorn.io default-engine-image current-longhorn-version
kubectl -n longhorn-system get systembackups.longhorn.io,volumes.longhorn.io
python3 scripts/longhorn-upgrade-engines.py VOLUME --image docker.io/longhornio/longhorn-engine:v1.11.1
python3 scripts/longhorn-upgrade-engines.py VOLUME --image docker.io/longhornio/longhorn-engine:v1.11.1 --apply
```

The apply operation calls Longhorn's native `engineUpgrade` API. Attached V1
volumes use Longhorn's live upgrade; detached volumes use its offline upgrade.
The script waits for the volume and its active processes to use the target
image, then rechecks all volume replica health. Inspect application readiness
and recent Longhorn logs after each volume before proceeding to the next.
Resource lists may briefly straddle the replacement of old replica CRs. The
helper waits for a consistent inventory, three healthy copies and the expected
active process images within its existing timeout. It checks other volumes
strictly throughout and never proceeds on incomplete convergence.

If any guard or completion check fails, stop and inspect the existing operation.
Do not downgrade an engine or manager, delete replicas, change replica data
files, or start an upgrade on another volume to resolve an incomplete upgrade.

References: [supported upgrade sequence](https://longhorn.io/docs/1.13.0/deploy/upgrade/)
and [V1 live/offline engine upgrades](https://longhorn.io/docs/1.11.1/deploy/upgrade/auto-upgrade-engine/).
