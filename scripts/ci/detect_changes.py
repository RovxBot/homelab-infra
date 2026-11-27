#!/usr/bin/env python3
"""Select PR checks from the diff; scheduled and manual runs check everything."""

from __future__ import annotations

import argparse
from collections.abc import Collection
import json
import os
from pathlib import Path
import subprocess


TERRAFORM_STACKS = ("oci-free-tier", "oci-matrix-free-tier")
# Kept equal to kustomize_inputs.FILENAMES by a regression test. Selection runs
# before PyYAML installation, so it cannot import the rendering helper.
KUSTOMIZE_FILENAMES = {"kustomization.yaml", "kustomization.yml", "Kustomization"}
FULL_CHECK_PATHS = {
    "scripts/ci/kustomize_inputs.py",
    "scripts/ci/check_production_graph.py",
    "scripts/ci/tests/test_kustomize_inputs.py",
    "scripts/ci/tests/test_production_graph.py",
    "scripts/ci/tests/test_secret_refs.py",
    ".github/workflows/ci.yml",
    "scripts/ci/detect_changes.py",
    "scripts/ci/tests/test_detect_changes.py",
    "scripts/ci/run_selected_tests.py",
    "scripts/ci/require_checks.py",
    "scripts/ci/tests/test_run_selected_tests.py",
    "scripts/ci/tests/test_require_checks.py",
}
MANIFEST_CHECK_PATHS = {
    ".github/kyverno-baseline.yaml",
    "scripts/ci/check_flux_bootstrap_digests.py",
    "scripts/ci/check_immich_versions.py",
    "scripts/ci/tests/test_immich_versions.py",
    "scripts/ci/check_secret_refs.py",
    "scripts/ci/check_python_helpers.py",
    "scripts/ci/check_namespace_defaults.py",
    "scripts/ci/tests/test_namespace_defaults.py",
    "scripts/ci/kyverno_gate.py",
    "scripts/longhorn-retire-backup-history.py",
    "scripts/longhorn-upgrade-engines.py",
    "scripts/tests/test_longhorn_retire_backup_history.py",
    "scripts/tests/test_longhorn_upgrade_engines.py",
    "ops/longhorn/reviewed-backup-retention-job.yaml",
}
EDGE_ROOT = "terraform/oci-free-tier/ops/wireguard/"
# Keep the retired prefix for PR diffs that delete or rename old source files.
KYVERNO_PATHS = ('infra/kyverno/', 'infra/kyverno-policies/',
                 'scripts/ci/kyverno/', 'migration/kyverno/')
# Reviewed sources, tests and helpers only. Prefix-based script ownership would
# silently narrow new inputs before their reverse consumers have been reviewed.
# Keep whole suites: Longhorn's engine, retention and snapshot guards have
# transitive consumers within the family; its retention test helper is shared.
COMPONENT_SCRIPT_PATHS = {
    'test_koito*.py': {
        'scripts/koito-audit-lastfm.py',
        'scripts/koito-export-lastfm.py',
        'scripts/koito-prepare-lastfm-import.py',
        'scripts/tests/test_koito_audit_lastfm.py',
        'scripts/tests/test_koito_bridge_manifest.py',
        'scripts/tests/test_koito_export_lastfm.py',
        'scripts/tests/test_koito_prepare_lastfm_import.py',
    },
    'test_longhorn*.py': {
        'scripts/longhorn-b2-object-lifecycle.py',
        'scripts/longhorn-prune-snapshots.py',
        'scripts/longhorn-refresh-share-manager.py',
        'scripts/longhorn-remove-stale-attachment.py',
        'scripts/longhorn-repair-empty-disk.py',
        'scripts/longhorn-resume-idle-snapshot-purge.py',
        'scripts/longhorn-retire-backup-history.py',
        'scripts/longhorn-retire-retired-volumes.py',
        'scripts/longhorn-upgrade-engines.py',
        'scripts/tests/test_longhorn_b2_object_lifecycle.py',
        'scripts/tests/test_longhorn_prune_snapshots.py',
        'scripts/tests/test_longhorn_refresh_share_manager.py',
        'scripts/tests/test_longhorn_remove_stale_attachment.py',
        'scripts/tests/test_longhorn_repair_empty_disk.py',
        'scripts/tests/test_longhorn_resume_idle_snapshot_purge.py',
        'scripts/tests/test_longhorn_retire_backup_history.py',
        'scripts/tests/test_longhorn_retire_retired_volumes.py',
        'scripts/tests/test_longhorn_upgrade_engines.py',
        'scripts/tests/helpers/longhorn_retention.py',
    },
}


def script_test_patterns(paths: list[str], *, full: bool,
                         statuses: dict[str, str] | None = None) -> list[str]:
    reviewed_paths = set().union(*COMPONENT_SCRIPT_PATHS.values())
    reviewed_change = not reviewed_paths.isdisjoint(paths)
    added = {path for path in paths if (statuses or {}).get(path) == 'A'}
    deleted = {path for path in paths if (statuses or {}).get(path) == 'D'}
    # With --no-renames, opposite A/D sides may be a move even when their
    # content and suffix differ. Conservatively broaden if either side is
    # reviewed and the other is unfamiliar; M paths cannot be rename sides.
    unknown_rename = (bool(added & reviewed_paths) and bool(deleted - reviewed_paths)
                      or bool(deleted & reviewed_paths) and bool(added - reviewed_paths))
    # Also fail broad for an unfamiliar side alongside a reviewed script,
    # including scripts moved outside scripts/ or renamed to another suffix.
    # The no-renames diff supplies both sides; a familiar old path is not proof
    # of ownership of its new destination (or vice versa).
    if full or unknown_rename or any(path not in reviewed_paths and (
        path.startswith('scripts/') and (reviewed_change or path.endswith(('.py', '.json', '.go')))
        or reviewed_change and path.endswith(('.py', '.go', '.sh'))
    ) for path in paths):
        return ['test_*.py']
    owners = {
        ('apps/koito/',): ['test_koito*.py'],
        ('infra/observability/', 'infra/pod-report/'):
            ['test_gatus*.py', 'test_cluster_health.py', 'test_kubescape_health.py', 'test_security_policy_publisher.py', 'test_security_native_publisher.py', 'test_security_admission_observer.py'],
        ('apps/gitea/', 'apps/homepage/', 'apps/koito/', 'clusters/home/'):
            ['test_gatus_alert_config.py'],
        ('infra/kubescape/',): ['test_grype*.py', 'test_kubescape*.py', 'test_gatus_alert_config.py'],
        ('infra/longhorn/', 'infra/longhorn-node-disks/', 'ops/longhorn/'):
            ['test_longhorn*.py'],
        KYVERNO_PATHS:
            ['test_kyverno*.py', 'test_kubescape_status_guardrail.py'],
    }
    return sorted(
        {pattern for pattern, sources in COMPONENT_SCRIPT_PATHS.items() if sources.intersection(paths)}
        | {pattern for prefixes, patterns in owners.items()
           if any(path.startswith(prefixes) for path in paths) for pattern in patterns}
    )


def classify_changes(paths: list[str], *, full: bool = False,
                     statuses: dict[str, str] | None = None,
                     kustomize_roots: Collection[str] = ()) -> dict[str, str]:
    full = full or any(path in FULL_CHECK_PATHS for path in paths)
    patterns = script_test_patterns(paths, full=full, statuses=statuses)
    ci_tests = full or any(path.startswith(('scripts/ci/', *KYVERNO_PATHS)) for path in paths)
    gitea_tests = full or any(path.startswith(('ops/gitea/', 'apps/gitea/', 'apps/gitea-runners/')) for path in paths)
    flags = {
        "unit_tests": bool(patterns) or ci_tests or gitea_tests,
        "ci_tests": ci_tests,
        "gitea_tests": gitea_tests,
        "manifests": full or any(
            path.startswith(("apps/", "infra/", "clusters/", "secrets/", "ops/gitea/", "ops/security-collector/acceptance/"))
            or path.startswith(("scripts/ci/kyverno/fixtures/", "migration/kyverno/"))
            or path in MANIFEST_CHECK_PATHS
            or Path(path).name in KUSTOMIZE_FILENAMES
            or any(Path(root) in Path(path).parents for root in kustomize_roots)
            for path in paths
        ),
        "public_edge": full or f"{EDGE_ROOT}Caddyfile" in paths,
        "shell": full or any(
            path.endswith(".sh") and path.startswith(("scripts/", "ops/gitea/", "migration/kyverno/", EDGE_ROOT))
            for path in paths
        ),
        "workflows": full or any(
            path.startswith((".github/workflows/", ".github/tests/"))
            or path in {".yamllint.yml", ".github/actionlint.yaml", ".github/actionlint.yml"}
            for path in paths
        ),
        "renovate": full or any(
            path == "renovate.json" or path.startswith((".github/tests/renovate-", ".github/tests/fixtures/renovate-"))
            for path in paths
        ),
    }
    stacks = [
        stack for stack in TERRAFORM_STACKS
        if full or any(
            path.startswith(f"terraform/{stack}/")
            and path.endswith((".tf", ".tf.json", ".hcl", ".tftpl"))
            for path in paths
        )
    ]
    return {
        **{name: str(value).lower() for name, value in flags.items()},
        "script_tests": json.dumps(patterns, separators=(",", ":")),
        "terraform_matrix": json.dumps({"stack": stacks}, separators=(",", ":")),
        "terraform": str(bool(stacks)).lower(),
    }


def changed_statuses(base: str, head: str) -> dict[str, str]:
    # Compare the actual PR commits, not the synthetic merge commit checked out
    # by Actions. Base-only changes must not wake unrelated checks. Disable
    # rename detection so both the removed and added paths select their checks.
    result = subprocess.run(
        ["git", "diff", "--name-status", "--no-renames", "-z", f"{base}...{head}", "--"],
        check=True,
        capture_output=True,
    )
    fields = result.stdout.split(b"\0")[:-1]
    return {os.fsdecode(fields[i + 1]): os.fsdecode(fields[i])
            for i in range(0, len(fields), 2)}


def changed_paths(base: str, head: str) -> list[str]:
    return list(changed_statuses(base, head))


def pr_kustomize_roots(base: str, head: str) -> set[str]:
    """Ancestor directories in the PR's before/after trees, not the checkout.

    Retain removed roots for deletions/renames and ignore roots added only on
    the target branch. Any changed input under these roots selects manifests;
    no dependency parsing or full-check fallback for unrelated paths.
    """
    merge_base = subprocess.run(
        ["git", "merge-base", base, head], check=True, capture_output=True, text=True,
    ).stdout.strip()
    if not merge_base:
        raise ValueError("Missing PR merge-base for Kustomize root selection")
    roots = set()
    for revision in (merge_base, head):
        result = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "-z", revision, "--"],
            check=True, capture_output=True,
        )
        for raw in result.stdout.split(b"\0"):
            path = Path(os.fsdecode(raw))
            if path.name in KUSTOMIZE_FILENAMES:
                roots.add(path.parent.as_posix())
    return roots


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="")
    args = parser.parse_args()
    statuses = {}
    roots = set()
    if args.event == "pull_request":
        if not args.base or not args.head:
            parser.error("pull_request requires --base and --head")
        statuses = changed_statuses(args.base, args.head)
        roots = pr_kustomize_roots(args.base, args.head)
        paths = list(statuses)
        full = False
    elif args.event in {"schedule", "workflow_dispatch"}:
        paths = []
        full = True
    else:
        parser.error(f"unsupported event: {args.event}")

    outputs = classify_changes(paths, full=full, statuses=statuses, kustomize_roots=roots)
    print(json.dumps({"changed_paths": paths, "checks": outputs}, indent=2))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as handle:
        for name, value in outputs.items():
            handle.write(f"{name}={value}\n")


if __name__ == "__main__":
    main()
