# Completed Kyverno activation — historical reference

The staged cutover is complete. [Task 21 acceptance](../evidence/task21/deployment.json)
records chart 3.10.0/application 1.20.0, successful isolated four-scenario
qualification and live upgrade acceptance at production revision
`b38ae3e3b6bbd132a448c5f43b70e210098892e5`. These are historical observations,
not fresh qualification or security-project acceptance. #2133 and #2139 remain
independent of this tooling retirement.

The accepted [Flux namespace decision](../../../docs/decisions/0003-flux-namespace-defaults.md)
replaced the abandoned CEL generator. The cutover protected runner Namespace UIDs,
overlapped validation/mutation/exceptions/reporting, froze enrollment, proved
complete zero inventories, observed orphan settings and retired the empty legacy
generator. Permanent Flux guards were qualified before thaw; legacy validators
were pruned separately before the original Helm safety hook and storage migration.
The Renovate cap was released only after upgrade acceptance. No enforcement
reduction or replacement generator was accepted.

Preserve the [exception decisions](exception-review.md),
[Task 17 failure record](../evidence/task17/findings.md),
[Task 18 qualification](../evidence/task18/qualification.json),
[historical render receipt](../evidence/task18/activation-renders.json) and
[Task 20 onboarding receipt](../evidence/task20/namespace-onboarding.json).
Patch names in these receipts are historical stage identifiers, not current
filesystem inputs; some receipts precede the final split of upgrade/cap removal.
The [original activation README](https://github.com/RovxBot/homelab-infra/blob/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/activation/README.md)
preserves the complete gates, rollback constraints and merge order.

The ten completed patches and their replay helpers are retired. Their immutable
source is retained below; do not regenerate or apply this completed sequence to
current main.

| Original patch at audit baseline 4468df7 |
| --- |
| [00-protect-runner-namespaces-and-admission-drain](https://github.com/RovxBot/homelab-infra/blob/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/activation/00-protect-runner-namespaces-and-admission-drain.patch) |
| [01-overlap-cel-and-reporting](https://github.com/RovxBot/homelab-infra/blob/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/activation/01-overlap-cel-and-reporting.patch) |
| [02a-freeze](https://github.com/RovxBot/homelab-infra/blob/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/activation/02a-freeze.patch) |
| [02b-orphan-legacy-before-retirement](https://github.com/RovxBot/homelab-infra/blob/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/activation/02b-orphan-legacy-before-retirement.patch) |
| [02c-retire-empty-legacy-generator](https://github.com/RovxBot/homelab-infra/blob/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/activation/02c-retire-empty-legacy-generator.patch) |
| [02d-qualify-flux-onboarding-contract](https://github.com/RovxBot/homelab-infra/blob/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/activation/02d-qualify-flux-onboarding-contract.patch) |
| [02e-thaw](https://github.com/RovxBot/homelab-infra/blob/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/activation/02e-thaw.patch) |
| [03-retire-legacy-after-acceptance](https://github.com/RovxBot/homelab-infra/blob/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/activation/03-retire-legacy-after-acceptance.patch) |
| [04-qualified-upgrade](https://github.com/RovxBot/homelab-infra/blob/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/activation/04-qualified-upgrade.patch) |
| [05-release-renovate-cap](https://github.com/RovxBot/homelab-infra/blob/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/activation/05-release-renovate-cap.patch) |

Original helpers:
[prepare.py](https://github.com/RovxBot/homelab-infra/blob/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/activation/prepare.py) and
[check_activation.py](https://github.com/RovxBot/homelab-infra/blob/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/harness/check_activation.py).

[freeze.yaml](freeze.yaml) remains an active isolated qualification input, along
with the legacy namespace bootstrap policy. Current Flux lifecycle, traffic,
upgrade/rollback, validation, mutation and exception tests remain in the
[harness](../harness). All four mandatory scenarios and migration inventory/policy
identity checks remain required; see the [qualification contract](../README.md).
Current namespace operations are maintained in the
[Wiki](https://github.com/RovxBot/homelab-infra/wiki/Namespace-Onboarding).
