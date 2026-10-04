# Whole-account B2 spending budget

Minimize the whole B2 account's cost, including Longhorn and photos, while
preserving a usable recovery point. AUD 5 per month is a guide, not a firm
limit. Successful backups and restores take priority over enforcing that amount.

Keep one completed Longhorn recovery point per backup volume, one successful
system archive and one Restic photo snapshot. Preserve current shared blocks;
use native garbage collection and the one-day hidden-version lifecycle rather
than expiring current objects by upload age. Longhorn idle polling is disabled.
Scheduled retention waits on Kubernetes backup status and runs only after a
successful weekly capture; it does not inventory B2 independently.

After native cleanup, wait for provider lifecycle processing and usage counters.
Verify every bucket's actual billable usage and allow temporary room for the
next backup while the previous recovery point is still retained. A storage cap
stops new uploads; it does not reduce charges for existing stored data or reverse
charges already accrued. Do not set a cap below the measured footprint or use
one to force the account under AUD 5. Leave storage uncapped during cleanup and
prefer spending alerts once usage settles. Any optional cap must allow backup
growth and must not prevent a working restore.

The saved USD 0.01/day paid-download cap is a spending guard. Review it if a
restore needs more paid egress; do not discard the last recovery point to meet
it. Estimate ongoing cost from measured stored bytes and actual downloads,
using current pricing, exchange rates, taxes and conversion fees. A lower cap
does not lower the cost of data already stored. Keep account billing details
and credentials out of this repository.

Backblaze's published pay-as-you-go Class A, B and C API operations are free.
Minimize calls anyway by avoiding idle refresh and redundant cleanup jobs.
Class D outbound event notifications are separately billed and cannot have a
data cap; include any enabled notifications in the whole-account calculation.
Confirm the saved caps and alert settings in the account console. No provider
cap is installed merely by committing this runbook.

Cost preference updated 4 October 2026. Use the main storage pricing page for
the current rate; the transaction page may contain an older storage-rate note.

References: [B2 storage pricing](https://www.backblaze.com/cloud-storage/pricing),
[API classes](https://www.backblaze.com/cloud-storage/transaction-pricing),
[data caps](https://www.backblaze.com/docs/cloud-storage-data-caps-and-alerts),
[RBA exchange rates](https://www.rba.gov.au/statistics/frequency/exchange-rates.html),
[remote retention](longhorn-latest-backup-retention.md), and
[photo retention](immich-photo-backup-retention.md).
