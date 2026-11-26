# Longhorn recovery backups for maintenance

The weekly system backup captures volume data and cluster configuration with
`volume-backup-policy: always`. It runs Sunday at 04:15 UTC (13:45 Adelaide
standard time or 14:45 daylight time). The guarded retention worker subsequently
keeps the newest completed remote backup per volume and the newest successful
system archive. See [remote retention](longhorn-latest-backup-retention.md).

Before engine upgrades, snapshot compaction or replica maintenance:

1. Confirm the intended cluster context, completed manager rollout, available
   backup target and reconciled credentials. Check that no rebuild, migration
   or competing storage operation is running, and that every attached volume
   has at least three healthy RW replicas on distinct
   nodes. Detached volumes must retain three healthy copies.
2. Prepare a new, manually reviewed native SystemBackup with
   `volumeBackupPolicy: always`, using the inline procedure below. Select an
   explicit phase for each capture:

   - **Pre-upgrade:** use `pre-upgrade-recovery` as the maintenance-purpose
     label and the currently running Longhorn version as the expected-version
     label. Finish backup and isolated restore verification before maintenance.
   - **Post-upgrade:** use `post-upgrade-recovery` and the reviewed target
     version. Only capture after managers, engine and replica processes, and
     all RWX exports have converged to that version and original applications
     and claims remain healthy. Verify actual running processes and exports;
     a HelmRelease version alone does not establish convergence.

   Save this YAML to a local file such as `/tmp/longhorn-system-backup.yaml`,
   outside Flux sources. Replace **every** `REPLACE` value before review.
   Give each capture a new DNS-safe name containing its phase, UTC timestamp
   and a random suffix: `pre-upgrade-<yyyymmddthhmmssz>-<random>` or
   `post-upgrade-<yyyymmddthhmmssz>-<random>`. Check existing SystemBackup names
   in the intended cluster; a collision requires a new name, never an update,
   deletion or reuse of the existing object. The expected-version label must
   contain the full version with a `v` prefix.

   ```yaml
   apiVersion: longhorn.io/v1beta2
   kind: SystemBackup
   metadata:
     name: REPLACE-WITH-UNIQUE-PHASE-TIMESTAMP-SUFFIX
     namespace: longhorn-system
     labels:
       homelab.cooked.beer/maintenance-purpose: REPLACE-WITH-PHASE-recovery
       homelab.cooked.beer/expected-longhorn-version: REPLACE-WITH-vMAJOR.MINOR.PATCH
   spec:
     volumeBackupPolicy: always
   ```

   Review context, namespace, unique name, phase, version, policy and the
   preceding health gates, then perform a server-side dry-run:

   ```sh
   kubectl create --dry-run=server -f /tmp/longhorn-system-backup.yaml
   ```

   Stop on any failed gate or dry-run. After a successful dry-run and deliberate
   approval of this capture, record its UTC start time and create exactly the
   reviewed file. Any edit requires another review and server-side dry-run.

   ```sh
   date -u +'%Y-%m-%dT%H:%M:%SZ'
   kubectl create -f /tmp/longhorn-system-backup.yaml
   ```

3. Wait for the SystemBackup to report `status.state: Ready` with the running
   Longhorn version matching the reviewed expected-version label.
   Independently check every current volume's `lastBackup`:
   its referenced Backup must be Completed at 100%, have no error, name the
   original volume, and have `snapshotCreatedAt` after this operation started.
   A Ready configuration-only archive is insufficient. Maintenance guards
   require the source snapshot capture to be no older than 24 hours.
4. Restore the new Gatus backup into a separate temporary three-replica volume
   and check SQLite integrity. Verify original PV/PVC UIDs and Bound status
   before and after. Remove only the verification resources after success;
   never restore over an original claim.
5. Keep current volume data, original claims, healthy replica copies and the
   newest verified remote recovery points. Retire older historical snapshots
   and backups only through the reviewed native retention helpers after their
   health, freshness, identity and CSI-reference checks pass. Local policy
   keeps two recent points without archiving older versions to B2.

Manual captures remain outside Flux reconciliation. Native backup controllers
can temporarily attach an unused detached volume to capture it, then release
the attachment.

The original dated examples are retained as immutable Git history:
[pre-upgrade `pre-upgrade-20261001`](https://github.com/RovxBot/homelab-infra/blob/844e457dec2c5929cc4b164916dd6794d2f15eaf/ops/longhorn/pre-upgrade-system-backup.yaml)
and [post-upgrade `post-upgrade-20261002`](https://github.com/RovxBot/homelab-infra/blob/844e457dec2c5929cc4b164916dd6794d2f15eaf/ops/longhorn/post-upgrade-system-backup.yaml).
The post-upgrade example required convergence to 1.13.0, including all four RWX
exports. Historical completion of these captures and their restore gates is
**unverified**; committed examples are not execution receipts. Restore the
source and instructions by reverting this consolidation if needed, without
replaying old names or modifying any live backup resource or retained data.

The separate [credential-verification manifest](../../ops/longhorn/credential-verification-system-backup.yaml)
retains `volumeBackupPolicy: disabled` for its metadata-only credential check.
It does not replace this data-inclusive maintenance capture or its restore gates.

These are live filesystem snapshots. Transactional applications may recover
from their logs after a restore, as they would after an unexpected shutdown.
The isolated SQLite check validates that selected backup; it does not establish
application-level consistency for every database.

References: [system backups](https://longhorn.io/docs/1.13.0/advanced-resources/system-backup-restore/backup-longhorn-system/),
[local retention](longhorn-snapshot-retention.md), and
[share-manager refresh](longhorn-share-manager-refresh.md).
