"""Fail-closed root discovery, safe rendering and Flux reachability controls."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kustomize_inputs as inputs
import kyverno_gate as gate
import detect_changes as detect

ROOT = Path(__file__).resolve().parents[3]


def ks(name, path, **spec):
    return {'apiVersion': inputs.FLUX_API, 'kind': 'Kustomization',
            'metadata': {'namespace': 'flux-system', 'name': name},
            'spec': {'path': './' + path, 'sourceRef': {'kind': 'GitRepository', 'name': 'flux-system'}, **spec}}


MARKER = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'marker'}}


class RootDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        for path in inputs.REQUIRED_ROOTS:
            self.add_root(path)

    def add_root(self, path, filename='kustomization.yaml'):
        directory = self.root/path
        directory.mkdir(parents=True, exist_ok=True)
        (directory/filename).write_text('apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources: [missing.yaml]\n')
        return directory/filename

    def test_discovers_all_filename_conventions_and_retained_roots(self):
        for index, filename in enumerate(sorted(inputs.FILENAMES)):
            self.add_root(f'new/root-{index}', filename)
        roots = inputs.discover_roots(self.root)
        self.assertTrue(inputs.REQUIRED_ROOTS <= set(roots))
        self.assertEqual(len(roots), len(inputs.REQUIRED_ROOTS) + 3)
        self.assertEqual(roots, sorted(roots))

    def test_missing_required_root_and_missing_tracked_input_fail(self):
        path = self.add_root('apps/tracked')
        subprocess.run(['git', 'add', '.'], cwd=self.root, check=True)
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing or external'):
            inputs.discover_roots(self.root)
        subprocess.run(['git', 'rm', '--cached', 'apps/tracked/kustomization.yaml'], cwd=self.root, check=True, capture_output=True)
        (self.root/'secrets/kustomization.yaml').unlink()
        with self.assertRaises(ValueError):
            inputs.discover_roots(self.root)

    def test_empty_discovery_failed_git_and_ambiguous_filenames_fail(self):
        with patch.object(inputs.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'')):
            with self.assertRaisesRegex(ValueError, 'Missing retained'):
                inputs.discover_roots(self.root)
        with patch.object(inputs.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'git')):
            with self.assertRaises(subprocess.CalledProcessError):
                inputs.discover_roots(self.root)
        self.add_root('secrets', 'Kustomization')
        with self.assertRaisesRegex(ValueError, 'Multiple Kustomize'):
            inputs.discover_roots(self.root)

    def test_external_symlink_root_is_not_rendered(self):
        (self.root/'secrets/kustomization.yaml').unlink()
        (self.root/'secrets/kustomization.yaml').symlink_to('/etc/hosts')
        with self.assertRaisesRegex(ValueError, 'external'):
            inputs.discover_roots(self.root)

    def test_every_discovered_root_is_rendered_without_output_files(self):
        self.add_root('new/root', 'Kustomization')
        with patch.object(gate, 'render_kustomization', return_value=[MARKER]) as render:
            result = inputs.render_roots(self.root)
        self.assertEqual(set(result), inputs.REQUIRED_ROOTS | {'new/root'})
        self.assertEqual([call.args[1] for call in render.call_args_list], sorted(result))
        self.assertFalse((self.root/'.work').exists())

    def test_missing_resource_input_is_a_real_render_failure(self):
        # Uses the installed renderer, without any cluster API access.
        with self.assertRaisesRegex(RuntimeError, 'Failed to render'):
            gate.render_kustomization(self.root, 'secrets')

    def test_supported_filenames_render_and_invalid_source_fails(self):
        for index, filename in enumerate(sorted(inputs.FILENAMES)):
            directory = f'valid-{index}'
            path = self.add_root(directory, filename)
            (path.parent/'marker.yaml').write_text(yaml.safe_dump(MARKER))
            path.write_text('apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources: [marker.yaml]\n')
            self.assertEqual(gate.render_kustomization(self.root, directory), [MARKER])
            path.write_text('resources: [INVALID-SOURCE\n')
            with self.assertRaisesRegex(RuntimeError, 'Failed to render'):
                gate.render_kustomization(self.root, directory)


class RenderDocumentTests(unittest.TestCase):
    def test_empty_invalid_nonresource_and_duplicate_key_outputs_fail(self):
        for raw in ['', '\n---\n', 'PRIVATE-SENTINEL: [', '[]', 'scalar',
                    '{}', 'kind: Secret', 'apiVersion: v1\nkind: Secret\nmetadata: {name: x, namespace: []}',
                    'apiVersion: v1\nkind: Secret\nmetadata: {name: x}\n---\n- PRIVATE-SENTINEL',
                    'apiVersion: v1\nkind: Secret\nmetadata: {name: x, name: PRIVATE-SENTINEL}']:
            with self.subTest(raw=raw), self.assertRaises(ValueError) as error:
                inputs.resource_documents(raw, 'root')
            self.assertNotIn('PRIVATE-SENTINEL', str(error.exception))

    def test_valid_documents_and_empty_separators_are_supported(self):
        raw = yaml.safe_dump(MARKER)
        self.assertEqual(inputs.resource_documents('---\n' + raw + '---\n', 'root'), [MARKER])

    def test_failed_renderer_suppresses_both_streams(self):
        with patch.object(gate, 'run_command', return_value=subprocess.CompletedProcess([], 1, 'PRIVATE-SENTINEL', 'PRIVATE-SENTINEL')):
            with self.assertRaises(RuntimeError) as error:
                gate.render_kustomization(ROOT, 'secrets')
        self.assertNotIn('PRIVATE-SENTINEL', str(error.exception))

    def test_successful_renderer_with_invalid_output_still_fails(self):
        with patch.object(gate, 'run_command', return_value=subprocess.CompletedProcess([], 0, 'scalar', '')):
            with self.assertRaises(ValueError):
                gate.render_kustomization(ROOT, 'secrets')

    def test_workflow_uploads_only_payload_free_kyverno_receipts(self):
        workflow = yaml.safe_load((ROOT/'.github/workflows/ci.yml').read_text())
        steps = workflow['jobs']['manifest-validation']['steps']
        step = next(s for s in steps if s.get('name') == 'Upload Kyverno gate artifacts')
        self.assertEqual(step['with']['path'].splitlines(), ['.work/kyverno-gate/summary.json', '.work/kyverno-gate/render-paths.txt'])
        self.assertIn('python3 scripts/ci/check_secret_refs.py .work/rendered.yaml', '\n'.join(s.get('run', '') for s in steps))
        self.assertTrue(any('python3 scripts/ci/check_production_graph.py' in s.get('run', '') for s in steps))


class ProductionDiscoveryTests(unittest.TestCase):
    def graph(self):
        return {'clusters/home': [ks('flux-system', 'clusters/home'), ks('parent', 'production')],
                'production': [ks('child', 'child')], 'child': [MARKER],
                'ops/security-collector/acceptance': [{'kind': 'DeliberatelyInvalidFixture'}]}

    def test_bootstrap_and_recursive_owners_retained_acceptance_is_isolated(self):
        graph = self.graph()
        entries = inputs.production_entries(ROOT, graph)
        self.assertEqual([owner['metadata']['name'] for owner, _ in entries], ['flux-system', 'parent', 'child'])
        self.assertEqual(entries[0][1], graph['clusters/home'])
        self.assertEqual(entries[2][1], [MARKER])

    def test_one_in_memory_render_per_owner_path_is_equivalent_to_supplied_map(self):
        graph = self.graph()
        with patch.object(gate, 'render_kustomization', side_effect=lambda root, path: graph[path]) as render:
            independent = inputs.production_entries(ROOT)
        self.assertEqual(independent, inputs.production_entries(ROOT, graph))
        self.assertEqual([call.args[1] for call in render.call_args_list], ['clusters/home', 'production', 'child'])

    def test_missing_roots_and_missing_production_discovery_fail(self):
        for graph in [{}, {'clusters/home': [MARKER]}, {'clusters/home': [ks('missing', 'absent')]}]:
            with self.assertRaises(ValueError):
                inputs.production_entries(ROOT, graph)

    def test_conflicting_declarations_invalid_path_and_unsupported_transforms_fail(self):
        graph = self.graph(); graph['production'].append(ks('child', 'different'))
        with self.assertRaisesRegex(ValueError, 'Conflicting Flux'):
            inputs.production_entries(ROOT, graph)
        for change in [{'path': '../outside'}, {'path': ''}, {'kubeConfig': {'secretRef': {'name': 'remote'}}},
                       {'postBuild': {'substitute': {'x': 'y'}}}, {'sourceRef': {'kind': 'Bucket', 'name': 'remote'}}]:
            graph = self.graph(); graph['clusters/home'][1]['spec'].update(change)
            with self.assertRaises(ValueError):
                inputs.production_entries(ROOT, graph)

    def test_missing_dependencies_and_unknown_flux_api_fail(self):
        graph = self.graph(); graph['clusters/home'][1]['spec']['dependsOn'] = [{'name': 'absent'}]
        with self.assertRaisesRegex(ValueError, 'Missing Flux dependency'):
            inputs.production_entries(ROOT, graph)
        graph = self.graph(); graph['clusters/home'][1]['apiVersion'] = 'kustomize.toolkit.fluxcd.io/v99'
        with self.assertRaisesRegex(ValueError, 'Unsupported Flux'):
            inputs.production_entries(ROOT, graph)


class RetainedInputSelectionTests(unittest.TestCase):
    def test_retained_acceptance_paths_select_manifests_and_kubescape(self):
        workflow = yaml.safe_load((ROOT/'.github/workflows/kubescape-checks.yml').read_text())
        paths = workflow['on' if 'on' in workflow else True]['pull_request']['paths']
        self.assertIn('ops/security-collector/acceptance/**', paths)
        for path in ['ops/security-collector/acceptance/kustomization.yaml',
                     'ops/security-collector/acceptance/base/deployment.yaml',
                     'ops/security-collector/acceptance/trigger/Kustomization']:
            checks = detect.classify_changes([path])
            self.assertEqual(checks['manifests'], 'true')
        self.assertEqual(detect.classify_changes(['ops/security-collector/README.md'])['manifests'], 'false')

    def test_new_helpers_and_tests_force_full_selection_and_wake_kubescape(self):
        workflow = yaml.safe_load((ROOT/'.github/workflows/kubescape-checks.yml').read_text())
        paths = workflow[True]['pull_request']['paths']
        for path in ['scripts/ci/kustomize_inputs.py', 'scripts/ci/check_production_graph.py',
                     'scripts/ci/tests/test_kustomize_inputs.py', 'scripts/ci/tests/test_production_graph.py',
                     'scripts/ci/tests/test_secret_refs.py']:
            self.assertEqual(detect.classify_changes([path]), detect.classify_changes([], full=True))
            self.assertIn(path, paths)

    def test_acceptance_changed_only_on_base_does_not_select_manifests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, text=True).strip()

            git('init', '-b', 'main'); git('config', 'user.name', 'CI test'); git('config', 'user.email', 'ci@example.invalid')
            (root/'README.md').write_text('baseline\n')
            git('add', '.'); git('commit', '-m', 'baseline'); git('checkout', '-b', 'change')
            (root/'README.md').write_text('documentation change\n')
            git('add', '.'); git('commit', '-m', 'docs'); head = git('rev-parse', 'HEAD')
            git('checkout', 'main')
            fixture = root/'ops/security-collector/acceptance/base/input.yaml'
            fixture.parent.mkdir(parents=True); fixture.write_text('base-only fixture\n')
            git('add', '.'); git('commit', '-m', 'base-only'); base = git('rev-parse', 'HEAD')
            invoke = subprocess.run
            with patch.object(detect.subprocess, 'run', side_effect=lambda cmd, **kwargs: invoke(cmd, cwd=root, **kwargs)):
                paths = detect.changed_paths(base, head)
            self.assertEqual(paths, ['README.md'])
            self.assertEqual(detect.classify_changes(paths)['manifests'], 'false')

    def test_acceptance_add_delete_rename_and_base_only_diffs(self):
        for operation in ['add', 'delete', 'rename']:
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)

                def git(*args):
                    return subprocess.check_output(['git', *args], cwd=root, text=True).strip()

                git('init', '-b', 'main'); git('config', 'user.name', 'CI test'); git('config', 'user.email', 'ci@example.invalid')
                tracked = root/'ops/security-collector/acceptance/base/input.yaml'
                tracked.parent.mkdir(parents=True); tracked.write_text('fixture\n')
                git('add', '.'); git('commit', '-m', 'baseline'); git('checkout', '-b', 'change')
                if operation == 'add':
                    tracked.with_name('new.yaml').write_text('new fixture\n')
                elif operation == 'delete':
                    tracked.unlink()
                else:
                    tracked.rename(root/'retired-fixture.yaml')
                git('add', '-A'); git('commit', '-m', operation); head = git('rev-parse', 'HEAD')
                git('checkout', 'main'); (root/'renovate.json').write_text('{}\n')
                git('add', '.'); git('commit', '-m', 'base-only'); base = git('rev-parse', 'HEAD')
                invoke = subprocess.run
                with patch.object(detect.subprocess, 'run', side_effect=lambda cmd, **kwargs: invoke(cmd, cwd=root, **kwargs)):
                    paths = detect.changed_paths(base, head)
                self.assertNotIn('renovate.json', paths)
                if operation in ['delete', 'rename']:
                    self.assertIn('ops/security-collector/acceptance/base/input.yaml', paths)
                self.assertEqual(detect.classify_changes(paths)['manifests'], 'true')
                self.assertEqual(detect.classify_changes(paths)['renovate'], 'false')


if __name__ == '__main__':
    unittest.main()
