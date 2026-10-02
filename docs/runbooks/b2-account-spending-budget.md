# Whole-account B2 spending budget

The target is under AUD 5 per month across the entire B2 account, including
Longhorn and photos. Bucket-specific limits are insufficient.

Keep one completed Longhorn recovery point per backup volume, one successful
system archive and one Restic photo snapshot. Preserve current shared blocks;
use native garbage collection and the one-day hidden-version lifecycle rather
than expiring current objects by upload age. Longhorn idle polling is disabled.
Scheduled retention waits on Kubernetes backup status and runs only after a
successful weekly capture; it does not inventory B2 independently.

After native cleanup, wait for provider lifecycle processing and usage counters.
Verify every bucket's actual billable usage and include the temporary storage
needed by the next backup before setting the account's daily caps. Finish the
required recovery upload and restore verification first. A storage cap stops
new uploads; it does not reduce charges for existing stored data or reverse
charges already accrued.

The preferred daily caps are USD 0.07 storage plus USD 0.01 paid downloads. At
USD 6.95 per TB per 30 days with the first 10 GB free, the storage allowance is
about 312 GB for the whole account. A USD 0.08 storage cap (about 355 GB) is an
alternative only when measured backup headroom requires it. Never increase a
cap automatically to resolve a failed backup.

For a 31-day month, the preferred combined USD 0.08/day is about AUD 4.04 using
USD 0.6949 per AUD, a 10% tax allowance and a 3% conversion-fee allowance. The
USD 0.09/day alternative is about AUD 4.55 using the same assumptions. These
are planning amounts, not an AUD-denominated hard cap: recheck pricing, the
actual payment conversion rate, taxes, billing-cycle accrual and any additional
paid services before claiming the monthly account target is met. Keep account
billing details and credentials out of this repository.

Backblaze's published pay-as-you-go Class A, B and C API operations are free.
Minimize calls anyway by avoiding idle refresh and redundant cleanup jobs.
Class D outbound event notifications are separately billed and cannot have a
data cap; include any enabled notifications in the whole-account calculation.
Confirm the saved caps and alert settings in the account console. No provider
cap is installed merely by committing this runbook.

Pricing and exchange-rate reference date: 2 October 2026; latest published RBA
rate was 1 October 2026. Use the main storage pricing page for the current
storage rate; the transaction page still contains an older storage-rate note.

References: [B2 storage pricing](https://www.backblaze.com/cloud-storage/pricing),
[API classes](https://www.backblaze.com/cloud-storage/transaction-pricing),
[data caps](https://www.backblaze.com/docs/cloud-storage-data-caps-and-alerts),
[RBA exchange rates](https://www.rba.gov.au/statistics/frequency/exchange-rates.html),
[remote retention](longhorn-latest-backup-retention.md), and
[photo retention](immich-photo-backup-retention.md).
