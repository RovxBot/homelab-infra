from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
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


class ComponentSelectionTests(unittest.TestCase):
    # Independent reviewed inventory: do not derive expected ownership from the
    # selector's map or compare only counts (equal counts can hide missing IDs).
    SOURCE_STEMS = {
        'koito': ('koito-audit-lastfm', 'koito-export-lastfm', 'koito-prepare-lastfm-import'),
        'longhorn': (
            'longhorn-b2-object-lifecycle', 'longhorn-prune-snapshots',
            'longhorn-refresh-share-manager', 'longhorn-remove-stale-attachment',
            'longhorn-repair-empty-disk', 'longhorn-resume-idle-snapshot-purge',
            'longhorn-retire-backup-history', 'longhorn-retire-retired-volumes',
            'longhorn-upgrade-engines',
        ),
    }
    TEST_MODULES = {
        'koito': {
            'test_koito_audit_lastfm', 'test_koito_bridge_manifest',
            'test_koito_export_lastfm', 'test_koito_prepare_lastfm_import',
        },
        'longhorn': {
            'test_longhorn_b2_object_lifecycle', 'test_longhorn_prune_snapshots',
            'test_longhorn_refresh_share_manager', 'test_longhorn_remove_stale_attachment',
            'test_longhorn_repair_empty_disk', 'test_longhorn_resume_idle_snapshot_purge',
            'test_longhorn_retire_backup_history', 'test_longhorn_retire_retired_volumes',
            'test_longhorn_upgrade_engines',
        },
    }

    @classmethod
    def setUpClass(cls):
        # Isolate script-test imports from CI discovery (both have helpers and
        # similarly named modules). Exercise Batch 1's real discovery entrypoint.
        code = '''
import json, sys
sys.path.insert(0, 'scripts/ci')
from run_selected_tests import discover_suite, test_cases
patterns = ['test_*.py', 'test_koito*.py', 'test_longhorn*.py',
            'test_gatus_alert_config.py', 'test_kyverno*.py',
            'test_kubescape_status_guardrail.py']
print(json.dumps({p: sorted(t.id() for t in test_cases(discover_suite('scripts/tests', [p])))
                  for p in patterns}))
'''
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=SCRIPT.parents[2],
                                check=True, capture_output=True, text=True)
        cls.discovered = {pattern: set(ids) for pattern, ids in json.loads(result.stdout).items()}
        cls.all_ids = cls.discovered['test_*.py']
        cls.owner_ids = {
            owner: {name for name in cls.all_ids if name.split('.')[0] in modules}
            for owner, modules in cls.TEST_MODULES.items()
        }

    @classmethod
    def reviewed_paths(cls, owner):
        paths = {f'scripts/{stem}.py' for stem in cls.SOURCE_STEMS[owner]}
        paths.update(f'scripts/tests/{module}.py' for module in cls.TEST_MODULES[owner])
        if owner == 'longhorn':
            paths.add('scripts/tests/helpers/longhorn_retention.py')
        return paths

    def assert_ids(self, checks, expected):
        patterns = json.loads(checks['script_tests'])
        self.assertTrue(patterns)
        self.assertEqual(set().union(*(self.discovered[p] for p in patterns)), expected)

    def test_map_contains_only_reviewed_sources_tests_and_helper(self):
        self.assertEqual(detect.COMPONENT_SCRIPT_PATHS, {
            f'test_{owner}*.py': self.reviewed_paths(owner) for owner in self.TEST_MODULES
        })
        for owner in self.TEST_MODULES:
            for path in self.reviewed_paths(owner):
                self.assertTrue((SCRIPT.parents[2] / path).is_file(), path)

    def test_every_reviewed_path_selects_exact_complete_owner_ids(self):
        manifest_inputs = {
            'scripts/longhorn-retire-backup-history.py', 'scripts/longhorn-upgrade-engines.py',
            'scripts/tests/test_longhorn_retire_backup_history.py',
            'scripts/tests/test_longhorn_upgrade_engines.py',
        }
        for owner in self.TEST_MODULES:
            for path in sorted(self.reviewed_paths(owner)):
                with self.subTest(path=path):
                    checks = detect.classify_changes([path])
                    self.assert_ids(checks, self.owner_ids[owner])
                    self.assertEqual(json.loads(checks['script_tests']), [f'test_{owner}*.py'])
                    self.assertEqual(checks['unit_tests'], 'true')
                    self.assertEqual(checks['manifests'], str(path in manifest_inputs).lower())
                    for flag in ('ci_tests', 'gitea_tests', 'shell', 'workflows', 'renovate',
                                 'terraform', 'public_edge'):
                        self.assertEqual(checks[flag], 'false', flag)
                    self.assertEqual(json.loads(checks['terraform_matrix']), {'stack': []})

    def test_mixed_components_union_exact_ids_once(self):
        for koito in self.reviewed_paths('koito'):
            for longhorn in self.reviewed_paths('longhorn'):
                with self.subTest(koito=koito, longhorn=longhorn):
                    self.assert_ids(detect.classify_changes([koito, longhorn, koito]),
                                    self.owner_ids['koito'] | self.owner_ids['longhorn'])

    def test_koito_application_and_configuration_retain_gatus_ids(self):
        for path in ('apps/koito/lastfm-bridge.yaml', 'apps/koito/lastfm-bridge-config.json',
                     'apps/koito/kustomization.yaml'):
            with self.subTest(path=path):
                checks = detect.classify_changes(['scripts/koito-export-lastfm.py', path])
                self.assert_ids(checks, self.owner_ids['koito'] |
                                self.discovered['test_gatus_alert_config.py'])
                self.assertEqual(checks['manifests'], 'true')
        checks = detect.classify_changes(['scripts/longhorn-upgrade-engines.py',
                                          'apps/koito/lastfm-bridge.yaml'])
        self.assert_ids(checks, self.owner_ids['koito'] | self.owner_ids['longhorn'] |
                        self.discovered['test_gatus_alert_config.py'])

    def test_longhorn_manifests_and_runtime_qualification_remain_selected(self):
        for path in ('infra/longhorn/weekly-backup-retention.yaml',
                     'infra/longhorn/kustomization.yaml', 'infra/longhorn/longhorn-helmrelease.yaml',
                     'infra/longhorn-node-disks/kustomization.yaml',
                     'ops/longhorn/reviewed-backup-retention-job.yaml'):
            with self.subTest(path=path):
                checks = detect.classify_changes([path])
                self.assert_ids(checks, self.owner_ids['longhorn'])
                self.assertEqual(checks['manifests'], 'true')
        # These are the existing mounted sources/tests that select the manifest
        # job, including its proposed-runtime-image qualification step.
        import yaml
        workflow = yaml.safe_load((SCRIPT.parents[2] / '.github/workflows/ci.yml').read_text())
        step = next(s for s in workflow['jobs']['manifest-validation']['steps']
                    if s.get('name') == 'Test Python helpers on their proposed images')
        self.assertIn('python3 scripts/ci/check_python_helpers.py', step['run'])

    def test_unknown_new_and_fixture_script_paths_keep_full_fallback(self):
        for path in (
            'scripts/koito-new-operation.py', 'scripts/longhorn-new-operation.py',
            'scripts/tests/test_koito_new.py', 'scripts/tests/test_longhorn_new.py',
            'scripts/tests/helpers/new_retention.py', 'scripts/tests/helpers/__init__.py',
            'scripts/shared.py', 'scripts/koito-export-lastfm.py.go',
            'scripts/longhorn-upgrade-engines.py.json',
            'scripts/tests/fixtures/koito.json', 'scripts/tests/fixtures/longhorn.json',
            'scripts/tests/fixtures/security-collector/assessment.json',
        ):
            for known in ([], ['scripts/koito-audit-lastfm.py'],
                          ['scripts/longhorn-upgrade-engines.py']):
                with self.subTest(path=path, known=known):
                    checks = detect.classify_changes([*known, path])
                    self.assertEqual(json.loads(checks['script_tests']), ['test_*.py'])
                    self.assert_ids(checks, self.all_ids)

    def test_shared_ci_security_json_and_go_inputs_keep_broad_coverage(self):
        for path in (
            'scripts/ci/image_reference.py', 'scripts/tests/test_image_reference.py',
            'scripts/ci/check_python_helpers.py', 'scripts/ci/kyverno/validation.py',
            'scripts/ci/security_native_exporter_preflight.py',
            'scripts/security_receiver/native.py', 'scripts/security_collector/engine.py',
            'scripts/tests/test_security_receiver_native.py',
            'scripts/tests/test_security_ci_scan.py', 'scripts/tests/test_kyverno_policy_assurance.py',
            'scripts/tests/test_kubescape_recurring.py',
            'scripts/tests/fixtures/gatus_recovery_test.go',
            *detect.FULL_CHECK_PATHS,
        ):
            with self.subTest(path=path):
                checks = detect.classify_changes(['scripts/koito-audit-lastfm.py', path])
                self.assert_ids(checks, self.all_ids)
                self.assertEqual(json.loads(checks['script_tests']), ['test_*.py'])
                if path.startswith('scripts/ci/') or path in detect.FULL_CHECK_PATHS:
                    self.assertEqual(checks['ci_tests'], 'true')

    def test_mixed_policy_inputs_retain_existing_suites_and_job_flags(self):
        for path in ('infra/kyverno-policies/policies/workloads.yaml',
                     'migration/kyverno/policies/workloads.yaml'):
            with self.subTest(path=path):
                checks = detect.classify_changes(['scripts/longhorn-prune-snapshots.py', path])
                self.assert_ids(checks, self.owner_ids['longhorn'] |
                                self.discovered['test_kyverno*.py'] |
                                self.discovered['test_kubescape_status_guardrail.py'])
                self.assertEqual(checks['ci_tests'], 'true')
                self.assertEqual(checks['manifests'], 'true')

    def test_full_mode_retains_every_script_id_even_for_known_paths(self):
        for paths in ([], ['scripts/koito-audit-lastfm.py'],
                      ['scripts/tests/helpers/longhorn_retention.py']):
            self.assert_ids(detect.classify_changes(paths, full=True), self.all_ids)

    def test_unrelated_paths_do_not_select_script_tests(self):
        for path in ('docs/koito.md', 'docs/longhorn.md', 'apps/immich/server.yaml',
                     'renovate.json', 'ops/unrelated.py', 'scripts/cilium-primary-health.sh'):
            with self.subTest(path=path):
                self.assertEqual(json.loads(detect.classify_changes([path])['script_tests']), [])

    def git_selection(self, removed, added, *, base_only=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, text=True,
                                               stderr=subprocess.DEVNULL).strip()

            def write(path):
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('# identical content exercises rename detection\n')

            git('init', '-b', 'main')
            git('config', 'user.name', 'CI selection test')
            git('config', 'user.email', 'ci-test@example.invalid')
            write('baseline.md')
            for path in removed:
                write(path)
            git('add', '.')
            git('commit', '-m', 'baseline')
            base = git('rev-parse', 'HEAD')
            git('checkout', '-b', 'change')
            for path in removed:
                (root / path).unlink()
            for path in added:
                write(path)
            git('add', '-A')
            git('commit', '-m', 'change')
            head = git('rev-parse', 'HEAD')
            if base_only:
                git('checkout', 'main')
                write(base_only)
                git('add', '.')
                git('commit', '-m', 'base-only change')
                base = git('rev-parse', 'HEAD')
            result = subprocess.run(
                [sys.executable, '-B', str(SCRIPT), '--event', 'pull_request',
                 '--base', base, '--head', head], cwd=root,
                env={**os.environ, 'GITHUB_OUTPUT': str(root / 'outputs')},
                check=True, capture_output=True, text=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(set(payload['changed_paths']), set(removed) | set(added))
            return payload['checks']

    def test_every_mapped_addition_and_deletion_selects_complete_owner(self):
        for owner in self.TEST_MODULES:
            for path in sorted(self.reviewed_paths(owner)):
                for removed, added in (([], [path]), ([path], [])):
                    with self.subTest(path=path, deletion=bool(removed)):
                        self.assert_ids(self.git_selection(removed, added), self.owner_ids[owner])

    def test_deleted_mapped_source_fails_real_discovery_before_execution(self):
        for owner, deleted in (('koito', 'scripts/koito-export-lastfm.py'),
                               ('longhorn', 'scripts/longhorn-upgrade-engines.py')):
            with self.subTest(deleted=deleted), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for path in self.reviewed_paths(owner) | {'scripts/ci/image_reference.py'}:
                    if path == deleted:
                        continue
                    target = root / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(SCRIPT.parents[2] / path, target)
                result = subprocess.run(
                    [sys.executable, '-B', str(SCRIPT.parent / 'run_selected_tests.py'),
                     '--suite', 'scripts'], cwd=root,
                    env={**os.environ, 'CHECK_SELECTION': json.dumps(detect.classify_changes([deleted]))},
                    capture_output=True, text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Invalid test selection or discovery', result.stderr)
                self.assertIn(Path(deleted).name, result.stderr)
                self.assertNotIn('Ran ', result.stderr)

    def test_known_renames_union_old_and_new_owners(self):
        for old, new, owners in (
            ('scripts/koito-export-lastfm.py', 'scripts/tests/test_koito_export_lastfm.py', ('koito',)),
            ('scripts/longhorn-upgrade-engines.py', 'scripts/tests/helpers/longhorn_retention.py', ('longhorn',)),
            ('scripts/koito-audit-lastfm.py', 'scripts/longhorn-retire-backup-history.py', ('koito', 'longhorn')),
            ('scripts/tests/test_longhorn_prune_snapshots.py', 'scripts/tests/test_koito_bridge_manifest.py', ('koito', 'longhorn')),
        ):
            with self.subTest(old=old, new=new):
                self.assert_ids(self.git_selection([old], [new]),
                                set().union(*(self.owner_ids[o] for o in owners)))

    def test_renames_with_unknown_script_side_fall_back_in_both_directions(self):
        for known in ('scripts/koito-export-lastfm.py', 'scripts/longhorn-upgrade-engines.py',
                      'scripts/tests/helpers/longhorn_retention.py'):
            for unknown in ('scripts/retired.py', 'scripts/tests/test_new_owner.py',
                            'scripts/tests/helpers/renamed_retention.py',
                            'scripts/retired.sh', 'scripts/retired',
                            'retired.py', 'archive/retired.py', 'archive/retired.go',
                            'archive/retired.sh'):
                for old, new in ((known, unknown), (unknown, known)):
                    with self.subTest(old=old, new=new):
                        self.assert_ids(self.git_selection([old], [new]), self.all_ids)

    def test_base_only_unknown_and_component_changes_do_not_wake_tests(self):
        for base_only in ('scripts/unknown.py', 'scripts/koito-export-lastfm.py',
                          'scripts/longhorn-upgrade-engines.py'):
            with self.subTest(base_only=base_only):
                checks = self.git_selection([], ['docs/report.md'], base_only=base_only)
                self.assertEqual(checks, detect.classify_changes(['docs/report.md']))
        checks = self.git_selection([], ['scripts/koito-export-lastfm.py'],
                                    base_only='scripts/unknown.py')
        self.assert_ids(checks, self.owner_ids['koito'])


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
