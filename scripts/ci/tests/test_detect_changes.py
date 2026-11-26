from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "detect_changes.py"
spec = importlib.util.spec_from_file_location("detect_changes", SCRIPT)
assert spec and spec.loader
detect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(detect)


class CheckSelectionTests(unittest.TestCase):
    def selected(self, paths: list[str], *, full: bool = False) -> set[str]:
        return {
            name for name, value in detect.classify_changes(paths, full=full).items()
            if value == "true" and name not in {'unit_tests', 'ci_tests', 'gitea_tests'}
        }

    def test_unit_suites_are_selected_by_owner(self):
        result = detect.classify_changes(['apps/koito/lastfm-bridge.yaml'])
        self.assertEqual(result['unit_tests'], 'true')
        self.assertEqual(result['ci_tests'], 'false')
        self.assertEqual(result['gitea_tests'], 'false')
        self.assertEqual(json.loads(result['script_tests']), ['test_gatus_alert_config.py', 'test_koito*.py'])
        for path in ['apps/immich/server.yaml', 'apps/home-assistant/deployment.yaml', 'renovate.json', 'docs/report.md']:
            self.assertEqual(detect.classify_changes([path])['unit_tests'], 'false', path)
        for path in ['scripts/example.py', 'scripts/tests/test_example.py']:
            self.assertEqual(json.loads(detect.classify_changes([path])['script_tests']), ['test_*.py'])
        self.assertEqual(detect.classify_changes(['ops/gitea/images/Dockerfile'])['gitea_tests'], 'true')
        self.assertEqual(detect.classify_changes(['scripts/ci/check_immich_versions.py'])['ci_tests'], 'true')
        full = detect.classify_changes([], full=True)
        self.assertEqual(full['unit_tests'], 'true')
        self.assertEqual(full['ci_tests'], 'true')
        self.assertEqual(full['gitea_tests'], 'true')
        self.assertEqual(json.loads(full['script_tests']), ['test_*.py'])

    def test_owner_patterns_cover_existing_tests(self):
        # Every current script test remains in the weekly full audit; owner
        # mappings must also refer to real tests rather than silently empty globs.
        tests = SCRIPT.parents[1] / 'tests'
        for paths in [('apps/koito/x',), ('infra/observability/x',), ('infra/kubescape/x',),
                      ('infra/kyverno/x',), ('infra/longhorn/x',), ('clusters/home/x',)]:
            for pattern in detect.script_test_patterns(list(paths), full=False):
                self.assertTrue(list(tests.glob(pattern)), pattern)

    def test_kyverno_old_and_new_paths_keep_regression_and_manifest_checks(self):
        for path in ['migration/kyverno/policies/workloads.yaml',
                     'migration/kyverno/namespace-example/namespace/kustomization.yaml',
                     'infra/kyverno-policies/policies/workloads.yaml',
                     'infra/kyverno-policies/exceptions/exceptions.yaml',
                     'scripts/ci/kyverno/fixtures/namespace-example/namespace/kustomization.yaml',
                     'scripts/ci/kyverno/fixtures/freeze.yaml']:
            with self.subTest(path=path):
                checks = detect.classify_changes([path])
                for name in ['unit_tests', 'ci_tests', 'manifests']:
                    self.assertEqual(checks[name], 'true')
                self.assertIn('test_kyverno*.py', json.loads(checks['script_tests']))
        for path in ['scripts/ci/kyverno/validation.py', 'scripts/ci/kyverno/run.sh',
                     'migration/kyverno/harness/validation.py', 'migration/kyverno/harness/run.sh']:
            checks = detect.classify_changes([path])
            self.assertEqual(checks['ci_tests'], 'true')
            self.assertEqual(checks['unit_tests'], 'true')
        for path in ['scripts/ci/kyverno/run.sh', 'migration/kyverno/harness/run.sh']:
            self.assertEqual(detect.classify_changes([path])['shell'], 'true')

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

    def test_gitea_mounted_sources_select_manifest_and_guard_validation(self):
        for path in ["ops/gitea/protect.py", "ops/gitea/protected-repositories.json", "ops/gitea/tests/test_protect.py"]:
            with self.subTest(path=path):
                self.assertEqual(self.selected([path]), {"manifests"})
        self.assertEqual(self.selected(["ops/gitea/backup.sh"]), {"manifests", "shell"})

    def test_shell_changes_only_run_shell_validation(self):
        self.assertEqual(self.selected(["scripts/cilium-primary-health.sh"]), {"shell"})
        self.assertEqual(self.selected([detect.EDGE_ROOT + "refresh-public-ports.sh"]), {"shell"})

    def test_workflow_pin_changes_run_workflow_checks(self):
        self.assertEqual(self.selected([".github/workflows/azerothcore-wotlk-images.yml"]), {"workflows"})
        self.assertEqual(self.selected([".github/tests/renovate-policy.test.cjs"]), {"workflows", "renovate"})
        self.assertEqual(self.selected([".github/tests/fixtures/renovate-2026-11-23.json"]), {"workflows", "renovate"})
        self.assertEqual(self.selected([".yamllint.yml"]), {"workflows"})

    def test_renovate_config_has_its_own_validation(self):
        self.assertEqual(self.selected(["renovate.json"]), {"renovate"})

    def test_runner_allowance_changes_run_workflow_checks(self):
        for path in [".github/actionlint.yaml", ".github/actionlint.yml"]:
            with self.subTest(path=path):
                self.assertEqual(self.selected([path]), {"workflows"})

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
    def test_helper_additions_deletions_and_renames_force_full_checks(self):
        for change in ('add', 'delete', 'rename'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)

                def git(*args):
                    return subprocess.check_output(['git', *args], cwd=root, text=True).strip()

                git('init', '-b', 'main')
                git('config', 'user.name', 'CI selection test')
                git('config', 'user.email', 'ci-test@example.invalid')
                old = root / 'scripts/ci/run_selected_tests.py'
                old.parent.mkdir(parents=True)
                old.write_text('# baseline helper\n')
                git('add', '.')
                git('commit', '-m', 'baseline')
                base = git('rev-parse', 'HEAD')
                if change == 'add':
                    (root / 'scripts/ci/require_checks.py').write_text('# added helper\n')
                elif change == 'delete':
                    old.unlink()
                else:
                    old.rename(root / 'scripts/retired_runner.py')
                git('add', '-A')
                git('commit', '-m', change)
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), '--event', 'pull_request', '--base', base, '--head', 'HEAD'],
                    cwd=root, env={**os.environ, 'GITHUB_OUTPUT': str(root / 'outputs')},
                    capture_output=True, text=True, check=True,
                )
                self.assertEqual(json.loads(result.stdout)['checks'], detect.classify_changes([], full=True))

    def test_kyverno_rename_and_deletion_select_checks_from_both_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, text=True).strip()

            git('init', '-b', 'main')
            git('config', 'user.name', 'CI selection test')
            git('config', 'user.email', 'ci-test@example.invalid')
            old = 'migration/kyverno/namespace-example/namespace/kustomization.yaml'
            deleted = 'migration/kyverno/policies/workloads.yaml'
            new = 'scripts/ci/kyverno/fixtures/namespace-example/namespace/kustomization.yaml'
            for path in [old, deleted]:
                (root/path).parent.mkdir(parents=True, exist_ok=True)
                (root/path).write_text('kind: Kustomization\n')
            git('add', '.')
            git('commit', '-m', 'baseline')
            base = git('rev-parse', 'HEAD')
            (root/new).parent.mkdir(parents=True)
            git('mv', old, new)
            git('rm', deleted)
            git('commit', '-m', 'relocate fixture and delete old policy')
            with patch.object(detect.subprocess, 'run', wraps=subprocess.run) as invoke:
                # changed_paths normally runs in the repository selected by cwd.
                previous = Path.cwd()
                try:
                    os.chdir(root)
                    paths = detect.changed_paths(base, 'HEAD')
                finally:
                    os.chdir(previous)
                self.assertIn('--no-renames', invoke.call_args.args[0])
            self.assertEqual(set(paths), {old, deleted, new})
            for name in ['unit_tests', 'ci_tests', 'manifests']:
                self.assertEqual(detect.classify_changes(paths)[name], 'true')

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
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), "--event", event],
                    env={**os.environ, "GITHUB_OUTPUT": str(output)}, check=True, capture_output=True,
                )
                self.assertNotIn("=false\n", output.read_text())
                self.assertEqual(json.loads(result.stdout)['checks'], detect.classify_changes([], full=True))


class RequiredGateTests(unittest.TestCase):
    @staticmethod
    def run_step(name: str, env: dict[str, str], *, capture=False, cwd=None):
        # Execute the actual gate's shell block so the tests cover its failure
        # behavior rather than a second implementation of the workflow logic.
        import yaml
        workflow = yaml.safe_load((SCRIPT.parents[2] / '.github/workflows/ci.yml').read_text())
        command = next(step['run'] for job in workflow['jobs'].values()
                       for step in job.get('steps', []) if step.get('name') == name)
        result = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", command],
            env={**os.environ, **env}, cwd=cwd or SCRIPT.parents[2], capture_output=True,
        )
        return result if capture else result.returncode

    def run_summary(self, results: dict) -> int:
        return self.run_step("Require successful checks", {"CHECK_RESULTS": json.dumps(results)})

    @staticmethod
    def successful_results() -> dict:
        names = ["kyverno-release-change", "kyverno-compatibility", "changes", "unit-tests", "manifest-validation", "public-edge", "shell-syntax", "actionlint", "yamllint", "gitleaks", "zizmor", "validate", "renovate-config"]
        results = {name: {"result": "success"} for name in names}
        results["changes"]["outputs"] = detect.classify_changes([], full=True)
        results["kyverno-release-change"]["outputs"] = {"changed": "true", "chart": "3.10.0"}
        return results

    def test_full_success_passes(self):
        self.assertEqual(self.run_summary(self.successful_results()), 0)

    def test_selection_is_a_separate_job_and_survives_test_failure(self):
        import yaml
        workflow = yaml.safe_load((SCRIPT.parents[2] / '.github/workflows/ci.yml').read_text())
        jobs = workflow['jobs']
        self.assertEqual([s.get('id') for s in jobs['changes']['steps'] if 'run' in s], ['changes'])
        self.assertEqual(jobs['unit-tests']['needs'], 'changes')
        self.assertEqual(jobs['manifest-validation']['needs'], 'changes')
        self.assertIn('unit-tests', jobs['repo-checks']['needs'])
        results = self.successful_results()
        results['unit-tests']['result'] = 'failure'
        result = self.run_step('Require successful checks', {'CHECK_RESULTS': json.dumps(results)}, capture=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Check unit-tests: failure', result.stdout)
        self.assertNotIn(b'Missing or invalid check selection', result.stdout)

    def test_missing_metadata_does_not_hide_original_failure(self):
        results = self.successful_results()
        results['changes'] = {'result': 'failure', 'outputs': {}}
        results['manifest-validation']['result'] = 'skipped'
        result = self.run_step('Require successful checks', {'CHECK_RESULTS': json.dumps(results)}, capture=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Check changes: failure', result.stdout)
        self.assertIn(b'Missing or invalid check selection: manifests', result.stdout)

    def test_summary_identifies_selected_check_not_executed(self):
        results = self.successful_results()
        results['unit-tests']['result'] = 'skipped'
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / 'summary'
            result = self.run_step('Require successful checks', {
                'CHECK_RESULTS': json.dumps(results), 'GITHUB_STEP_SUMMARY': str(summary)}, capture=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Required check did not succeed: unit-tests (skipped)', summary.read_text())

    def test_unrelated_jobs_may_be_skipped(self):
        results = self.successful_results()
        results["changes"]["outputs"] = detect.classify_changes(['docs/unrelated.md'])
        results["kyverno-release-change"]["outputs"]["changed"] = "false"
        for name in results:
            if name not in {"changes", "gitleaks", "kyverno-release-change"}:
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
