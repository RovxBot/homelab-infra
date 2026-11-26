# Kyverno CEL migration preflight — historical reference

The 21 November 2026 preflight assessed chart 3.9.2/application 1.19.2 against
chart 3.10.0/application 1.20.0. It stopped before deployable migration changes:
source/checksum tests were recorded, signature verification was unavailable in
that environment, and generator lifecycle transfer was not qualified. The
[original assessment](https://github.com/RovxBot/homelab-infra/blob/4468df762841f1341d1e1061f25f0e0b29da67e0/docs/kyverno/migration-plan.md)
retains the exact source commits, artifact digests, rule/exception matrix,
source-test results and the failed/incomplete gates. Those findings must not be
read as current deployment approval.

Task 18 superseded generator handoff with explicit Flux-owned namespace defaults.
The accepted [ADR 0003](../decisions/0003-flux-namespace-defaults.md) preserves why:
isolated generator restart recovery failed on both releases, including fresh and
adopted/upgraded resources. A same-name source test did not prove provenance,
watch reconstruction, cleanup safety or continuous protection. No CEL generator
or generator-specific write RBAC was selected. The
[Task 17 findings](../../migration/kyverno/evidence/task17/findings.md) retain the
failure evidence; the [migration inventory](migration-inventory.json) remains a
machine-consumed coverage contract.

The abandoned implementation and source diagnostic are retired, with their exact
original sources retained at the accepted audit baseline:

- [generation.py](https://github.com/RovxBot/homelab-infra/blob/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/harness/generation.py)
- [diagnostics.py](https://github.com/RovxBot/homelab-infra/blob/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/harness/diagnostics.py)
- [generator.yaml](https://github.com/RovxBot/homelab-infra/blob/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/policies/generator.yaml)
- [Go handoff probe](https://github.com/RovxBot/homelab-infra/blob/4468df762841f1341d1e1061f25f0e0b29da67e0/scripts/ci/fixtures/kyverno-generation-handoff_test.go)
- [original build.py](https://github.com/RovxBot/homelab-infra/blob/4468df762841f1341d1e1061f25f0e0b29da67e0/migration/kyverno/harness/build.py)

The old overlay commands and activation replay instructions are historical only.
See the [completed activation history](../../migration/kyverno/activation/README.md)
for immutable patches/helpers, retained rollback constraints and cutover receipts.
[Exception decisions](../../migration/kyverno/activation/exception-review.md)
remain unchanged. [Task 21 acceptance](../../migration/kyverno/evidence/task21/deployment.json)
records the successful upgrade and four mandatory scenarios; it does not settle
#2133 or imply acceptance of the independent security project (#2139).

Current namespace operations belong in the
[Wiki](https://github.com/RovxBot/homelab-infra/wiki/Namespace-Onboarding). The
[active qualification contract](../../migration/kyverno/README.md) retains legacy/CEL
validation, mutation, exceptions, Flux lifecycle/traffic/rollback and all four
mandatory scenarios. The historical validation hashes remain untouched; changes
to implementation inputs such as build.py require fresh qualification on the
exact proposed head. Historical validation reuse is no longer supported.
