#!/usr/bin/env python3
"""Select PR checks from the diff; scheduled and manual runs check everything."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess


TERRAFORM_STACKS = ("oci-free-tier", "oci-matrix-free-tier")
FULL_CHECK_PATHS = {
    ".github/workflows/ci.yml",
    "scripts/ci/detect_changes.py",
    "scripts/ci/tests/test_detect_changes.py",
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


def script_test_patterns(paths: list[str], *, full: bool) -> list[str]:
    if full or any(path.startswith('scripts/') and path.endswith(('.py', '.json', '.go')) for path in paths):
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
        ('infra/kyverno/', 'infra/kyverno-policies/', 'migration/kyverno/'):
            ['test_kyverno*.py', 'test_kubescape_status_guardrail.py'],
    }
    return sorted({pattern for prefixes, patterns in owners.items()
                   if any(path.startswith(prefixes) for path in paths) for pattern in patterns})


def classify_changes(paths: list[str], *, full: bool = False) -> dict[str, str]:
    full = full or any(path in FULL_CHECK_PATHS for path in paths)
    patterns = script_test_patterns(paths, full=full)
    ci_tests = full or any(path.startswith('scripts/ci/') for path in paths)
    gitea_tests = full or any(path.startswith(('ops/gitea/', 'apps/gitea/', 'apps/gitea-runners/')) for path in paths)
    flags = {
        "unit_tests": bool(patterns) or ci_tests or gitea_tests,
        "ci_tests": ci_tests,
        "gitea_tests": gitea_tests,
        "manifests": full or any(
            path.startswith(("apps/", "infra/", "clusters/", "secrets/", "ops/gitea/"))
            or path.startswith("migration/kyverno/namespace-example/")
            or path in MANIFEST_CHECK_PATHS
            for path in paths
        ),
        "public_edge": full or f"{EDGE_ROOT}Caddyfile" in paths,
        "shell": full or any(
            path.endswith(".sh") and path.startswith(("scripts/", "ops/gitea/", EDGE_ROOT))
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


def changed_paths(base: str, head: str) -> list[str]:
    # Compare the actual PR commits, not the synthetic merge commit checked out
    # by Actions. Base-only changes must not wake unrelated checks. Disable
    # rename detection so both the removed and added paths select their checks.
    result = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}", "--"],
        check=True,
        capture_output=True,
    )
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="")
    args = parser.parse_args()
    if args.event == "pull_request":
        if not args.base or not args.head:
            parser.error("pull_request requires --base and --head")
        paths = changed_paths(args.base, args.head)
        full = False
    elif args.event in {"schedule", "workflow_dispatch"}:
        paths = []
        full = True
    else:
        parser.error(f"unsupported event: {args.event}")

    outputs = classify_changes(paths, full=full)
    print(json.dumps({"changed_paths": paths, "checks": outputs}, indent=2))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as handle:
        for name, value in outputs.items():
            handle.write(f"{name}={value}\n")


if __name__ == "__main__":
    main()
