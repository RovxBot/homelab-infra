"""Candidate evidence is a build output; old evidence never blesses new inputs."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('scanner_evidence', ROOT / 'scripts/ci/scanner_build_evidence.py')
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


class ScannerEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / 'scanner'
        self.root.mkdir()
        # Synthetic inputs keep helper tests runnable on an approved candidate
        # ref whose production proof deliberately still describes the old build.
        (self.root / 'Dockerfile').write_text(
            'FROM golang:1.26-trixie@sha256:' + 'a' * 64 + ' AS build\n'
            'FROM quay.io/kubescape/kubevuln:v1.2.3@sha256:' + 'b' * 64 + '\n'
            'LABEL homelab.cooked.beer/upstream="' + 'c' * 40 + '"\n')
        (self.root / 'force-rescan.patch').write_text('synthetic reviewed patch\n')
        (self.root / 'force_rescan_test.go').write_text('synthetic service regression\n')
        self.proof = dict(status='verified', image='ghcr.io/rovxbot/kubevuln@sha256:' + 'd' * 64,
                          upstreamCommit='c' * 40, upstreamArchiveSHA256='e' * 64,
                          runtimeBase='quay.io/kubescape/kubevuln:v1.2.3@sha256:' + 'b' * 64,
                          inputs=evidence.input_hashes(self.root))
        (self.root / 'build-evidence.json').write_text(json.dumps(self.proof))

    def verify(self):
        return evidence.verify(self.root, self.proof['upstreamCommit'], self.proof['upstreamArchiveSHA256'])

    def test_matching_recorded_inputs_are_reusable(self):
        self.assertEqual(self.verify(), self.proof['image'])

    def test_each_changed_input_is_rejected_with_the_original_proof(self):
        for name in evidence.INPUTS:
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b'\n# candidate input change\n')
                with self.assertRaisesRegex(AssertionError, name):
                    self.verify()
                path.write_bytes(original)

    def test_missing_or_incomplete_proof_does_not_request_an_automatic_build(self):
        path = self.root / 'build-evidence.json'
        proof = {**self.proof, 'inputs': {'Dockerfile': self.proof['inputs']['Dockerfile']}}
        path.write_text(json.dumps(proof))
        with self.assertRaisesRegex(AssertionError, 'incomplete'):
            self.verify()
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.verify()

    def test_candidate_record_hashes_the_actual_inputs_and_preserves_old_proof(self):
        # Simulate only the post-build evidence writer, not a container build.
        original = (self.root / 'build-evidence.json').read_bytes()
        dockerfile = self.root / 'Dockerfile'
        dockerfile.write_text(dockerfile.read_text().replace('golang:1.26-', 'golang:1.27-'))
        candidate = evidence.record(self.root, 'ghcr.io/rovxbot/kubevuln@sha256:' + 'b' * 64,
                                    'a' * 40, self.proof['upstreamCommit'], self.proof['upstreamArchiveSHA256'],
                                    'https://github.com/RovxBot/homelab-infra/actions/runs/123')
        self.assertEqual((self.root / 'build-evidence.json').read_bytes(), original)
        self.assertNotEqual(candidate['inputs']['Dockerfile'], self.proof['inputs']['Dockerfile'])
        self.assertEqual(candidate['inputs'], evidence.input_hashes(self.root))
        (self.root / 'build-evidence.json').write_text(json.dumps(candidate))
        self.assertEqual(self.verify(), candidate['image'])

    def test_source_archive_and_runtime_contract_must_match(self):
        for key, value in [('upstreamCommit', '0' * 40), ('upstreamArchiveSHA256', '0' * 64),
                           ('runtimeBase', 'wrong'), ('image', 'ghcr.io/rovxbot/kubevuln:latest')]:
            with self.subTest(key=key), self.assertRaises(AssertionError):
                (self.root / 'build-evidence.json').write_text(json.dumps({**self.proof, key: value}))
                self.verify()

    def test_only_dispatch_can_build_and_evidence_follows_test_publish_and_retrieval(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/kubescape-scanner-image.yml').read_text())
        steps = workflow['jobs']['test-build']['steps']
        select = next(s for s in steps if s.get('id') == 'artifact')
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.build_candidate", select['env']['BUILD_CANDIDATE'])
        self.assertIn('scanner_build_evidence.py verify', select['run'])
        self.assertNotIn('continue-on-error', str(workflow))
        build = next(i for i,s in enumerate(steps) if 'docker build ' in s.get('run', ''))
        publish = next(i for i,s in enumerate(steps) if 'docker push ' in s.get('run', ''))
        retrieve = next(i for i,s in enumerate(steps) if 'docker manifest inspect' in s.get('run', ''))
        record = next(i for i,s in enumerate(steps) if 'scanner_build_evidence.py record' in s.get('run', ''))
        self.assertLess(build, publish)
        self.assertLess(publish, retrieve)
        self.assertLess(retrieve, record)
        self.assertEqual(steps[record]['if'], "steps.artifact.outputs.rebuild == 'true'")
        dockerfile = (ROOT / 'infra/kubescape/recurring/scanner/Dockerfile').read_text()
        self.assertIn('COPY force_rescan_test.go core/services/homelab_force_rescan_test.go', dockerfile)
        self.assertRegex(dockerfile, r"RUN go test ./core/services .*TestScanCVEHomelabForceRescan\|TestScanService_ScanCVE_ReportTimestamp.*-count=1")


if __name__ == '__main__':
    unittest.main()
