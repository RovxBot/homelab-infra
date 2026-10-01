from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "detect_changes.py"
spec = importlib.util.spec_from_file_location("detect_changes", SCRIPT)
assert spec and spec.loader
detect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(detect)


class CheckSelectionTests(unittest.TestCase):
    def selected(self, paths: list[str], *, full: bool = False) -> set[str]:
        return {
            name for name, value in detect.classify_changes(paths, full=full).items()
            if value == "true"
        }

    def test_documentation_does_not_start_expensive_checks(self):
        self.assertEqual(self.selected(["README.md", "docs/runbooks/renovate-operations.md"]), set())
        self.assertEqual(self.selected(["terraform/oci-free-tier/README.md"]), set())

    def test_application_image_only_needs_manifest_checks(self):
        self.assertEqual(self.selected(["apps/homepage/deployment.yaml"]), {"manifests"})

    def test_secret_and_policy_changes_need_manifest_checks(self):
        for path in ["secrets/app.enc.yaml", "infra/kyverno/policies/platform-flux-guardrails.yaml", ".github/kyverno-baseline.yaml"]:
            with self.subTest(path=path):
                self.assertEqual(self.selected([path]), {"manifests"})

    def test_changes_to_validators_run_their_checks(self):
        for path in detect.MANIFEST_CHECK_PATHS:
            with self.subTest(path=path):
                self.assertEqual(self.selected([path]), {"manifests"})

    def test_caddyfile_does_not_render_the_cluster(self):
        self.assertEqual(self.selected([detect.EDGE_ROOT + "Caddyfile"]), {"public_edge"})

    def test_shell_changes_only_run_shell_validation(self):
        self.assertEqual(self.selected(["scripts/cilium-primary-health.sh"]), {"shell"})
        self.assertEqual(self.selected([detect.EDGE_ROOT + "refresh-public-ports.sh"]), {"shell"})

    def test_workflow_pin_changes_run_workflow_checks(self):
        self.assertEqual(self.selected([".github/workflows/azerothcore-wotlk-images.yml"]), {"workflows"})
        self.assertEqual(self.selected([".yamllint.yml"]), {"workflows"})

    def test_renovate_config_has_its_own_validation(self):
        self.assertEqual(self.selected(["renovate.json"]), {"renovate"})

    def test_only_affected_terraform_roots_are_selected(self):
        for stack in detect.TERRAFORM_STACKS:
            for filename in ["main.tf", "provider.tf.json", ".terraform.lock.hcl", "templates/cloud-init.yaml.tftpl"]:
                with self.subTest(stack=stack, filename=filename):
                    result = detect.classify_changes([f"terraform/{stack}/{filename}"])
                    self.assertEqual(json.loads(result["terraform_matrix"]), {"stack": [stack]})
                    self.assertEqual(self.selected([f"terraform/{stack}/{filename}"]), {"terraform"})

    def test_combined_batch_selects_all_relevant_checks(self):
        self.assertEqual(
            self.selected(["apps/immich/deployment.yaml", ".github/workflows/skyfire-mop-images.yml"]),
            {"manifests", "workflows"},
        )

    def test_manual_scheduled_and_ci_changes_run_every_check(self):
        expected = {"manifests", "public_edge", "shell", "workflows", "renovate", "terraform"}
        self.assertEqual(self.selected([], full=True), expected)
        for path in detect.FULL_CHECK_PATHS:
            with self.subTest(path=path):
                self.assertEqual(self.selected([path]), expected)
        result = detect.classify_changes([], full=True)
        self.assertEqual(json.loads(result["terraform_matrix"]), {"stack": list(detect.TERRAFORM_STACKS)})


class GitDiffTests(unittest.TestCase):
    def test_pr_diff_ignores_base_only_changes_and_includes_deletions_and_renames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args: str) -> str:
                return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()

            git("init", "-b", "main")
            git("config", "user.name", "CI selection test")
            git("config", "user.email", "ci-test@example.invalid")
            (root / "apps").mkdir()
            (root / "apps/old.yaml").write_text("kind: Deployment\n")
            (root / "apps/deleted.yaml").write_text("kind: Service\n")
            git("add", ".")
            git("commit", "-m", "base")
            git("checkout", "-b", "update")
            git("mv", "apps/old.yaml", "moved.yaml")
            git("rm", "apps/deleted.yaml")
            # A newline in a Git path must not become a second, spoofed path.
            (root / "name\nrenovate.json").write_text("example\n")
            git("add", ".")
            git("commit", "-m", "PR changes")
            head = git("rev-parse", "HEAD")
            git("checkout", "main")
            (root / "renovate.json").write_text("{}\n")
            git("add", ".")
            git("commit", "-m", "unrelated base change")
            base = git("rev-parse", "HEAD")
            output = root / "outputs"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--event", "pull_request", "--base", base, "--head", head],
                cwd=root, env={**os.environ, "GITHUB_OUTPUT": str(output)},
                check=True, capture_output=True, text=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(set(payload["changed_paths"]), {"apps/old.yaml", "apps/deleted.yaml", "moved.yaml", "name\nrenovate.json"})
            self.assertIn("manifests=true\n", output.read_text())
            self.assertIn("renovate=false\n", output.read_text())

    def test_invalid_diff_fails_without_emitting_skip_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "outputs"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--event", "pull_request", "--base", "invalid-base", "--head", "invalid-head"],
                env={**os.environ, "GITHUB_OUTPUT": str(output)}, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())

    def test_scheduled_and_manual_runs_emit_full_check_outputs(self):
        for event in ["schedule", "workflow_dispatch"]:
            with self.subTest(event=event), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "outputs"
                subprocess.run(
                    [sys.executable, str(SCRIPT), "--event", event],
                    env={**os.environ, "GITHUB_OUTPUT": str(output)}, check=True, capture_output=True,
                )
                self.assertNotIn("=false\n", output.read_text())


class RequiredGateTests(unittest.TestCase):
    @staticmethod
    def run_step(name: str, env: dict[str, str]) -> int:
        # Execute the actual gate's shell block so the tests cover its failure
        # behavior rather than a second implementation of the workflow logic.
        workflow = (SCRIPT.parents[2] / ".github/workflows/ci.yml").read_text()
        step = workflow.split(f"      - name: {name}\n", 1)[1]
        lines = step.split("        run: |\n", 1)[1].splitlines()
        block = []
        for line in lines:
            if line.strip() and not line.startswith("          "):
                break
            block.append(line)
        command = textwrap.dedent("\n".join(block))
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", command],
            env={**os.environ, **env}, capture_output=True,
        ).returncode

    def run_summary(self, results: dict) -> int:
        return self.run_step("Require successful checks", {"CHECK_RESULTS": json.dumps(results)})

    @staticmethod
    def successful_results() -> dict:
        names = ["changes", "manifest-validation", "public-edge", "shell-syntax", "actionlint", "yamllint", "gitleaks", "zizmor", "validate", "renovate-config"]
        results = {name: {"result": "success"} for name in names}
        results["changes"]["outputs"] = {selector: "true" for selector in ["manifests", "public_edge", "shell", "workflows", "renovate", "terraform"]}
        return results

    def test_full_success_passes(self):
        self.assertEqual(self.run_summary(self.successful_results()), 0)

    def test_unrelated_jobs_may_be_skipped(self):
        results = self.successful_results()
        results["changes"]["outputs"] = {selector: "false" for selector in results["changes"]["outputs"]}
        for name in results:
            if name not in {"changes", "gitleaks"}:
                results[name]["result"] = "skipped"
        self.assertEqual(self.run_summary(results), 0)

    def test_missing_or_invalid_selections_fail(self):
        for value in [None, ""]:
            with self.subTest(value=value):
                results = self.successful_results()
                results["changes"]["outputs"]["manifests"] = value
                self.assertNotEqual(self.run_summary(results), 0)

    def test_any_failure_or_cancellation_fails_the_required_gate(self):
        for name in self.successful_results():
            for status in ["failure", "cancelled"]:
                with self.subTest(name=name, status=status):
                    results = self.successful_results()
                    results[name]["result"] = status
                    self.assertNotEqual(self.run_summary(results), 0)

    def test_selected_checks_cannot_silently_skip(self):
        for name in self.successful_results():
            with self.subTest(name=name):
                results = self.successful_results()
                results[name]["result"] = "skipped"
                self.assertNotEqual(self.run_summary(results), 0)

    def test_terraform_gate_requires_the_selected_matrix_to_succeed(self):
        for result, succeeds in [("success", True), ("failure", False), ("cancelled", False), ("skipped", False)]:
            with self.subTest(result=result):
                code = self.run_step("Require successful Terraform validation when selected", {
                    "CHANGES_RESULT": "success", "TERRAFORM_RESULT": result,
                })
                self.assertEqual(code == 0, succeeds)

    def test_terraform_gate_fails_when_selection_fails(self):
        code = self.run_step("Require successful Terraform validation when selected", {
            "CHANGES_RESULT": "failure", "TERRAFORM_RESULT": "skipped",
        })
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
