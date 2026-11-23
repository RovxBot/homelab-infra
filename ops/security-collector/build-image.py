#!/usr/bin/env python3
"""Deterministically layer the stdlib-only collector onto the pinned Python image.

Requires checksum-verified crane v0.22.1. No Docker daemon or build-time package
downloads. Produces a local image tarball; publication is a separate CI gate.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
BASE = 'docker.io/library/python@sha256:a943ed02f4e22d024d2b4fc5083619497dfb9bc1c64c193861f3bc6d3074f5b1'


def layer(output):
    hashes = {}
    with tarfile.open(output, 'w', format=tarfile.USTAR_FORMAT) as archive:
        for name in ('app', 'app/security_collector'):
            info = tarfile.TarInfo(name)
            info.type, info.mode = tarfile.DIRTYPE, 0o555
            archive.addfile(info)
        for path in sorted((ROOT / 'scripts/security_collector').glob('*.py')):
            value = path.read_bytes()
            info = tarfile.TarInfo('app/security_collector/' + path.name)
            info.size, info.mode = len(value), 0o444
            archive.addfile(info, io.BytesIO(value))
            hashes[str(path.relative_to(ROOT))] = hashlib.sha256(value).hexdigest()
    return hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--crane', default='crane')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = args.output / 'source.tar'
    hashes = layer(source)
    image = args.output / 'image.tar'
    subprocess.run([args.crane, 'mutate', '--platform=linux/amd64', BASE,
        '--append=' + str(source), '--entrypoint=python3,-B,-m,security_collector.service',
        '--cmd=', '--user=10001:65532', '--workdir=/app',
        '--env=PYTHONDONTWRITEBYTECODE=1', '--env=PYTHONUNBUFFERED=1',
        '--label=org.opencontainers.image.source=https://github.com/RovxBot/homelab-infra',
        '--tag=ghcr.io/rovxbot/security-collector:build',
        '--output=' + str(image)], check=True)
    subprocess.run([args.crane, 'validate', '--tarball=' + str(image)], check=True)
    digest = subprocess.check_output([args.crane, 'digest', '--tarball=' + str(image)], text=True).strip()
    proof = dict(platform='linux/amd64', base=BASE, digest=digest, files=hashes,
                 source_layer_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    (args.output / 'build.json').write_text(json.dumps(proof, indent=2) + '\n')
    print(digest)


if __name__ == '__main__':
    main()
