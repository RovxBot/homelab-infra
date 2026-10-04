# Latest photo recovery point

The photo job runs on the first and fifteenth of each month. After a successful
`restic backup`, the same job keeps the newest snapshot for host `k8s`, tag
`immich` and path `/data`, then prunes data unused by retained snapshots.
The source NFS volume remains mounted read-only. A failed or incomplete backup
stops the shell before retention runs, preserving the previous recovery point.

There is no separate monthly maintenance schedule or periodic bucket inventory
job. The existing repository must be initialized; a connectivity or credentials
error must not trigger repository initialization. Restic's default grouping
preserves separate hosts and source paths, and the explicit filters protect
unrelated backup sets.

Before retiring existing history, inspect `restic snapshots --json`, run
`restic check`, and review `restic forget --host k8s --tag immich --path /data
--keep-last 1 --dry-run`. Verify a recent successful backup for this source.
Run native retention once without the dry-run flag and with `--prune`, then
check repository integrity and confirm the retained snapshot ID. Do not force
unlock a repository or delete pack files directly.

The bucket-side rule expires hidden object versions one day after deletion;
current pack files remain available regardless of upload age. Usage counters
and billing can take time to reflect cleanup. Minimize this bucket's cost and
the Longhorn bucket's cost together while preserving a usable restore point.
AUD 5 per month is a guide, not a firm limit; avoid caps that block new backups.

References: [Restic retention and pruning](https://restic.readthedocs.io/en/stable/060_forget.html),
[repository checks](https://restic.readthedocs.io/en/stable/045_working_with_repos.html),
and [B2 lifecycle](longhorn-b2-lifecycle.md).
