# Retired: Cilium dual-overlay migration runbook

The dual-overlay rollout is complete. Cilium is now the primary CNI, Talos no
longer manages Flannel, and the temporary CiliumNodeConfig selector and
migration labels have been removed.

Do not follow the historical migration phases or recreate Flannel resources.
The detailed rollout remains available in Git history and its decision record
is [ADR 0002](../decisions/0002-cilium-dual-overlay-migration.md).

Use the [primary-Cilium operations runbook](cilium-primary-operations.md) for
all current validation, maintenance and node-IP recovery work.
