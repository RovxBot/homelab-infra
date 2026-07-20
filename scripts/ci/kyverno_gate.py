#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml


IGNORED_FLUX_RENDER_PATHS = {
    "clusters/home",
}

ALWAYS_INCLUDE_EXCLUDED_NAMESPACE_KINDS = {
    "PolicyException",
}


@dataclass(frozen=True, order=True)
class FailureKey:
    policy: str
    rule: str
    kind: str
    namespace: str
    name: str


@dataclass
class FailureRecord:
    policy: str
    rule: str
    kind: str
    namespace: str
    name: str
    message: str

    def key(self) -> FailureKey:
        return FailureKey(
            policy=self.policy,
            rule=self.rule,
            kind=self.kind,
            namespace=self.namespace,
            name=self.name,
        )

    def baseline_entry(self) -> dict[str, str]:
        return asdict(self.key())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate rendered Flux resources with Kyverno and compare to a baseline.")
    parser.add_argument("command", choices=["check", "generate-baseline"], help="Operation to perform")
    parser.add_argument("--repo-root", default=".", help="Repository root")
    parser.add_argument("--cluster-root", default="clusters/home", help="Flux cluster root")
    parser.add_argument("--baseline", required=True, help="Baseline YAML path")
    parser.add_argument("--kyverno-bin", required=True, help="Path to the kyverno CLI binary")
    parser.add_argument("--work-dir", required=True, help="Working directory for generated artifacts")
    return parser.parse_args()


def load_yaml_documents(path: Path) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    if not path.exists():
        return documents
    with path.open("r", encoding="utf-8") as handle:
        for document in yaml.safe_load_all(handle):
            if isinstance(document, dict):
                documents.append(document)
    return documents


def load_first_document(path: Path) -> dict[str, Any]:
    documents = load_yaml_documents(path)
    if not documents:
        raise RuntimeError(f"No YAML documents found in {path}")
    return documents[0]


def sanitize_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "resource"


def run_command(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def kustomize_base_command() -> list[str]:
    if shutil.which("kustomize"):
        return ["kustomize", "build", "--load-restrictor", "LoadRestrictionsNone"]
    if shutil.which("kubectl"):
        return ["kubectl", "kustomize", "--load-restrictor", "LoadRestrictionsNone"]
    raise RuntimeError("Neither kustomize nor kubectl is available")


def render_kustomization(repo_root: Path, relative_path: str) -> list[dict[str, Any]]:
    command = kustomize_base_command() + [relative_path]
    result = run_command(command, cwd=repo_root)
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to render {relative_path}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    documents: list[dict[str, Any]] = []
    for document in yaml.safe_load_all(result.stdout):
        if isinstance(document, dict):
            documents.append(document)
    return documents


def find_flux_render_paths(cluster_root: Path) -> list[str]:
    render_paths: set[str] = set()
    for yaml_file in cluster_root.rglob("*.yaml"):
        for document in load_yaml_documents(yaml_file):
            if document.get("apiVersion") != "kustomize.toolkit.fluxcd.io/v1":
                continue
            if document.get("kind") != "Kustomization":
                continue
            spec = document.get("spec") or {}
            path = spec.get("path")
            if not isinstance(path, str) or not path.startswith("./"):
                continue
            normalized = path[2:]
            if normalized in IGNORED_FLUX_RENDER_PATHS:
                continue
            render_paths.add(normalized)
    return sorted(render_paths)


def load_excluded_namespaces(helmrelease_path: Path) -> set[str]:
    helmrelease = load_first_document(helmrelease_path)
    values = ((helmrelease.get("spec") or {}).get("values") or {})
    config = values.get("config") or {}
    raw_namespaces = config.get("resourceFiltersExcludeNamespaces") or []
    return {namespace for namespace in raw_namespaces if isinstance(namespace, str) and namespace}


def filter_rendered_documents(documents: list[dict[str, Any]], excluded_namespaces: set[str]) -> tuple[list[dict[str, Any]], int]:
    filtered: list[dict[str, Any]] = []
    skipped = 0
    for document in documents:
        kind = document.get("kind", "")
        metadata = document.get("metadata") or {}
        name = metadata.get("name", "")
        namespace = metadata.get("namespace", "")
        if kind in ALWAYS_INCLUDE_EXCLUDED_NAMESPACE_KINDS:
            filtered.append(document)
            continue
        if namespace in excluded_namespaces:
            skipped += 1
            continue
        if kind == "Namespace" and name in excluded_namespaces:
            skipped += 1
            continue
        filtered.append(document)
    return filtered, skipped


def write_documents(documents: list[dict[str, Any]], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    # The gate's work directory is intentionally reusable for local runs and CI
    # artifacts. Remove only its previous generated inputs before rebuilding;
    # otherwise an old manifest can be evaluated alongside the current render.
    for existing in output_dir.glob("*.yaml"):
        existing.unlink()

    written: list[Path] = []
    for index, document in enumerate(documents, start=1):
        metadata = document.get("metadata") or {}
        kind = sanitize_name(str(document.get("kind", "Unknown")))
        name = sanitize_name(str(metadata.get("name", f"resource-{index}")))
        namespace = sanitize_name(str(metadata.get("namespace", "cluster")))
        file_path = output_dir / f"{index:04d}-{namespace}-{kind}-{name}.yaml"
        with file_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(document, handle, sort_keys=False)
        written.append(file_path)
    return written


def load_policy_reports(raw_output: str) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for document in yaml.safe_load_all(raw_output):
        if isinstance(document, dict):
            reports.append(document)
    return reports


def extract_result_subjects(result: dict[str, Any]) -> list[dict[str, str]]:
    raw_subjects: list[dict[str, Any]] = []
    for key in ("subjects", "resources"):
        value = result.get(key)
        if isinstance(value, list):
            raw_subjects.extend(item for item in value if isinstance(item, dict))

    for key in ("subject", "resource", "scope"):
        value = result.get(key)
        if isinstance(value, dict):
            raw_subjects.append(value)

    subjects: list[dict[str, str]] = []
    for subject in raw_subjects:
        kind = str(subject.get("kind", "Unknown"))
        name = str(subject.get("name", "unknown"))
        namespace = str(subject.get("namespace") or "cluster")
        if kind == "Namespace":
            namespace = "cluster"
        subjects.append(
            {
                "kind": kind,
                "namespace": namespace,
                "name": name,
            }
        )
    return subjects


def evaluate_resources(
    *,
    kyverno_bin: Path,
    repo_root: Path,
    policy_dir: Path,
    exception_files: list[Path],
    resource_dir: Path,
) -> list[FailureRecord]:
    command = [
        str(kyverno_bin),
        "apply",
        str(policy_dir),
        "--resource",
        str(resource_dir),
        "--policy-report",
        "--output-format",
        "json",
        "--continue-on-fail",
    ]
    for exception_file in exception_files:
        command.extend(["-e", str(exception_file)])
    result = run_command(command, cwd=repo_root)
    if result.returncode not in {0, 1}:
        raise RuntimeError(
            f"kyverno apply failed for {resource_dir.name}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    if not result.stdout.strip():
        return []

    records: list[FailureRecord] = []
    reports = load_policy_reports(result.stdout)
    for report in reports:
        for item in report.get("results", []):
            if item.get("result") != "fail":
                continue

            subjects = extract_result_subjects(item)
            if not subjects:
                raise RuntimeError(
                    "Kyverno policy report failure result did not include a resource subject\n"
                    f"Result:\n{json.dumps(item, indent=2, sort_keys=True)}"
                )

            for subject in subjects:
                records.append(
                    FailureRecord(
                        policy=str(item.get("policy", "unknown-policy")),
                        rule=str(item.get("rule", "unknown-rule")),
                        kind=subject["kind"],
                        namespace=subject["namespace"],
                        name=subject["name"],
                        message=str(
                            item.get("message")
                            or item.get("description")
                            or "Kyverno reported a failure."
                        ),
                    )
                )
    return records


def collect_failures(repo_root: Path, cluster_root: Path, kyverno_bin: Path, work_dir: Path) -> list[FailureRecord]:
    work_dir.mkdir(parents=True, exist_ok=True)
    render_paths = find_flux_render_paths(cluster_root)
    (work_dir / "render-paths.txt").write_text("\n".join(render_paths) + "\n", encoding="utf-8")

    policy_documents = render_kustomization(repo_root, "infra/kyverno/policies")
    exception_documents = render_kustomization(repo_root, "infra/kyverno/exceptions")

    rendered_documents: list[dict[str, Any]] = []
    for render_path in render_paths:
        rendered_documents.extend(render_kustomization(repo_root, render_path))

    excluded_namespaces = load_excluded_namespaces(repo_root / "infra/kyverno/helmrelease.yaml")
    filtered_documents, skipped_count = filter_rendered_documents(rendered_documents, excluded_namespaces)

    (work_dir / "summary.json").write_text(
        json.dumps(
            {
                "renderPaths": render_paths,
                "renderedDocumentCount": len(rendered_documents),
                "filteredDocumentCount": len(filtered_documents),
                "skippedDocumentCount": skipped_count,
                "excludedNamespaces": sorted(excluded_namespaces),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    policy_dir = work_dir / "policies"
    exception_dir = work_dir / "exceptions"
    resource_dir = work_dir / "resources"
    write_documents(policy_documents, policy_dir)
    exception_files = write_documents(exception_documents, exception_dir)
    write_documents(filtered_documents, resource_dir)

    failures: dict[FailureKey, FailureRecord] = {}
    for record in evaluate_resources(
        kyverno_bin=kyverno_bin,
        repo_root=repo_root,
        policy_dir=policy_dir,
        exception_files=exception_files,
        resource_dir=resource_dir,
    ):
        failures[record.key()] = record

    sorted_failures = [failures[key] for key in sorted(failures)]
    (work_dir / "actual-failures.json").write_text(
        json.dumps(
            [
                {
                    "policy": failure.policy,
                    "rule": failure.rule,
                    "kind": failure.kind,
                    "namespace": failure.namespace,
                    "name": failure.name,
                    "message": failure.message,
                }
                for failure in sorted_failures
            ],
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return sorted_failures


def load_baseline(path: Path) -> list[FailureKey]:
    document = load_first_document(path)
    accepted = document.get("acceptedFailures") or []
    baseline: list[FailureKey] = []
    for item in accepted:
        if not isinstance(item, dict):
            continue
        baseline.append(
            FailureKey(
                policy=str(item.get("policy", "")),
                rule=str(item.get("rule", "")),
                kind=str(item.get("kind", "")),
                namespace=str(item.get("namespace", "")),
                name=str(item.get("name", "")),
            )
        )
    return sorted(baseline)


def write_baseline(path: Path, failures: list[FailureRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "version": 1,
        "acceptedFailures": [failure.baseline_entry() for failure in failures],
    }
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(document, handle, sort_keys=False)


def print_failure_list(title: str, failures: list[FailureKey], failure_messages: dict[FailureKey, str] | None = None) -> None:
    print(title)
    for failure in failures:
        line = f"- {failure.policy}/{failure.rule} on {failure.kind} {failure.namespace}/{failure.name}"
        if failure_messages and failure in failure_messages:
            line = f"{line}: {failure_messages[failure]}"
        print(line)


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    cluster_root = (repo_root / args.cluster_root).resolve()
    baseline_path = (repo_root / args.baseline).resolve()
    kyverno_bin = Path(args.kyverno_bin).resolve()
    work_dir = (repo_root / args.work_dir).resolve()

    failures = collect_failures(repo_root, cluster_root, kyverno_bin, work_dir)
    if args.command == "generate-baseline":
        write_baseline(baseline_path, failures)
        print(f"Wrote baseline with {len(failures)} accepted failure(s) to {baseline_path}")
        return 0

    baseline = load_baseline(baseline_path)
    actual_keys = [failure.key() for failure in failures]
    baseline_set = set(baseline)
    actual_set = set(actual_keys)
    failure_messages = {failure.key(): failure.message for failure in failures}

    new_failures = sorted(actual_set - baseline_set)
    stale_failures = sorted(baseline_set - actual_set)

    print(f"Evaluated {len(actual_keys)} current failure(s) against {len(baseline)} baseline entry(ies).")
    if new_failures:
        print_failure_list("New Kyverno failures detected:", new_failures, failure_messages)
    if stale_failures:
        print_failure_list("Resolved baseline entries must be removed:", stale_failures)

    if new_failures or stale_failures:
        return 1

    print("Kyverno baseline check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
