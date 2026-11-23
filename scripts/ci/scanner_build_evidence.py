#!/usr/bin/env python3
"""Verify an existing scanner proof or record a successfully tested candidate.

The record command belongs after build, push and anonymous retrieval in the
explicit workflow_dispatch path. It writes a candidate artifact, never GitOps.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re


INPUTS = ('Dockerfile', 'force-rescan.patch', 'force_rescan_test.go')


def input_hashes(root):
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in INPUTS}


def source_contract(root, commit):
    dockerfile = (root / 'Dockerfile').read_text()
    assert re.fullmatch(r'[a-f0-9]{40}', commit), 'invalid upstream commit'
    assert f'LABEL homelab.cooked.beer/upstream="{commit}"' in dockerfile, 'upstream source/label mismatch'
    bases = re.findall(r'^FROM (\S+)', dockerfile, re.M)
    assert len(bases) == 2 and all(re.fullmatch(r'[^@]+@sha256:[a-f0-9]{64}', b) for b in bases), 'unpinned build inputs'
    return bases[-1]


def verify(root, upstream_commit, upstream_sha):
    proof = json.loads((root / 'build-evidence.json').read_text())
    assert proof['status'] == 'verified', 'unverified scanner artifact'
    assert proof['upstreamCommit'] == upstream_commit, 'upstream source mismatch'
    assert proof['upstreamArchiveSHA256'] == upstream_sha, 'upstream archive mismatch'
    assert proof['runtimeBase'] == source_contract(root, upstream_commit), 'runtime base mismatch'
    assert set(proof['inputs']) == set(INPUTS), 'incomplete build inputs'
    for name, digest in input_hashes(root).items():
        assert proof['inputs'][name] == digest, name
    assert re.fullmatch(r'ghcr\.io/rovxbot/kubevuln@sha256:[a-f0-9]{64}', proof['image']), 'invalid scanner pin'
    return proof['image']


def record(root, image, source_commit, upstream_commit, upstream_sha, build_run):
    assert re.fullmatch(r'ghcr\.io/rovxbot/kubevuln@sha256:[a-f0-9]{64}', image), 'invalid scanner pin'
    assert re.fullmatch(r'[a-f0-9]{40}', source_commit), 'invalid build commit'
    assert re.fullmatch(r'[a-f0-9]{64}', upstream_sha), 'invalid archive checksum'
    assert re.fullmatch(r'https://github\.com/RovxBot/homelab-infra/actions/runs/[0-9]+', build_run), 'invalid build run'
    return dict(status='verified', image=image, sourceCommit=source_commit,
                upstreamCommit=upstream_commit, upstreamArchiveSHA256=upstream_sha,
                buildRun=build_run, inputs=input_hashes(root),
                runtimeBase=source_contract(root, upstream_commit),
                tests='Docker build executed the Go service regressions before compiling the scanner',
                anonymousRegistryRetrieval='verified by anonymous docker manifest inspect in the build workflow',
                independentSignature='not established',
                change='Reviewed candidate; deployment promotion and canary acceptance are separate gates')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('verify', 'record'))
    parser.add_argument('--root', type=Path, default=Path('infra/kubescape/recurring/scanner'))
    parser.add_argument('--upstream-commit', required=True)
    parser.add_argument('--upstream-sha', required=True)
    parser.add_argument('--image-file', type=Path, required=True)
    parser.add_argument('--source-commit')
    parser.add_argument('--build-run')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        if args.mode == 'verify':
            args.image_file.write_text(verify(args.root, args.upstream_commit, args.upstream_sha) + '\n')
        else:
            assert args.output and args.source_commit and args.build_run, 'candidate provenance is required'
            proof = record(args.root, args.image_file.read_text().strip(), args.source_commit,
                           args.upstream_commit, args.upstream_sha, args.build_run)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(proof, indent=2) + '\n')
    except (AssertionError, OSError, ValueError, KeyError) as error:
        parser.exit(1, f'Build qualification failed: {error}\n')


if __name__ == '__main__':
    main()
