#!/usr/bin/env python3
"""Verify restored guarded refs, all hosted repositories and historical bundles."""
import hashlib
import json
import pathlib
import subprocess
import zipfile

root = pathlib.Path('/restore/restored')
status = json.loads((root / 'protection/status.json').read_text())
results = {}
for name, record in status.items():
    repo = root / 'gitea/git/repositories/github-dr' / (name + '.git')
    guard = root / 'protection/git' / (name + '.git')
    subprocess.run(['git', '--git-dir=' + str(repo), 'fsck', '--full'], check=True)
    subprocess.run(['git', '--git-dir=' + str(guard), 'fsck', '--full'], check=True)
    rows = subprocess.check_output(['git', '--git-dir=' + str(repo), 'for-each-ref',
        '--format=%(refname) %(objectname)', 'refs/heads/', 'refs/tags/']).decode().splitlines()
    hosted = dict(row.split(' ', 1) for row in rows)
    lagging = []
    for ref, sha in record['refs'].items():
        actual = subprocess.check_output(['git', '--git-dir=' + str(guard), 'rev-parse', ref]).decode().strip()
        if actual != sha:
            raise RuntimeError('Restored protected source differs from accepted state: ' + name + ':' + ref)
        if hosted.get(ref) != sha:
            # A snapshot may legitimately fall between the source check and
            # destination schedule. The complete accepted state is preserved in
            # the independent guard repository; report destination lag honestly.
            lagging.append(ref)
    for ref, sha in hosted.items():
        accepted = record['refs'].get(ref)
        if accepted is None:
            raise RuntimeError('Hosted ref has no accepted provenance')
        if sha != accepted:
            if ref.startswith('refs/tags/'):
                raise RuntimeError('Hosted tag differs from the protected source')
            subprocess.run(['git', '--git-dir=' + str(guard), 'merge-base',
                            '--is-ancestor', sha, accepted], check=True)
    required = '959b0b85c5f55c565328d7c3ca33027a65e41327' if name == 'wowdecomp15595' else None
    if required:
        subprocess.run(['git', '--git-dir=' + str(repo), 'cat-file', '-e', required + '^{commit}'], check=True)
    results[name] = {'verified_protected_refs': len(record['refs']),
                     'verified_hosted_refs': len(hosted), 'hosted_lag_at_capture': lagging,
                     'required_commit': required}
for repo in (root / 'gitea/git/repositories').glob('*/*.git'):
    subprocess.run(['git', '--git-dir=' + str(repo), 'fsck', '--full'], check=True)
bundles = list((root / 'protection/bundles').glob('*/*.bundle'))
for bundle in bundles:
    expected = bundle.with_suffix('.sha256').read_text().split()[0]
    with bundle.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
            raise RuntimeError('Corrupt historical bundle')
    repo = root / 'protection/git' / (bundle.parent.name + '.git')
    subprocess.run(['git', '--git-dir=' + str(repo), 'bundle', 'verify', str(bundle)], check=True)
results['bundles_verified'] = len(bundles)
results['database'] = json.loads(pathlib.Path('/restore/database-evidence.json').read_text())
artifacts = []
proof_restored = False
for path in (root / 'gitea/gitea/actions_artifacts').rglob('*'):
    if path.is_file() and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise RuntimeError('Corrupt restored Actions artifact')
            if 'proof.txt' in archive.namelist():
                proof_restored |= archive.read('proof.txt') == b'Independent Gitea artifact recovery\n'
        artifacts.append(str(path.relative_to(root)))
if results['database']['artifacts'] and not artifacts:
    raise RuntimeError('Database references artifacts but no artifact archives were restored')
if not proof_restored:
    raise RuntimeError('The known artifact round-trip fixture was not restored')
results['artifact_archives_verified'] = len(artifacts)
results['known_artifact_payload_restored'] = proof_restored
pathlib.Path('/restore/restore-evidence.json').write_text(json.dumps(results, indent=2) + '\n')
print(json.dumps(results, indent=2))
