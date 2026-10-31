#!/usr/bin/env python3
"""Verify and unpack a Talos OCI cache export for image cache-serve.

cache-create's default OCI layout is a cache artifact, not a registry directory.
cache-serve requires the flat registry files inside its verified layer. Future
exports may use cache-create --layout=flat directly instead.
"""
import argparse
import hashlib
import json
import pathlib
import tarfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('source', type=pathlib.Path)
parser.add_argument('destination', type=pathlib.Path)
args = parser.parse_args()
if args.destination.exists():
    raise SystemExit('Use a new destination directory')
index = json.loads((args.source / 'index.json').read_text())
manifests = index['manifests']
if len(manifests) != 1 or manifests[0].get('platform') != {'architecture': 'amd64', 'os': 'linux'}:
    raise SystemExit('Expected the reviewed single-platform Linux amd64 cache')


def verified_blob(digest):
    algorithm, value = digest.split(':')
    if algorithm != 'sha256' or len(value) != 64:
        raise SystemExit('Unexpected cache digest')
    path = args.source / 'blobs/sha256' / value
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != value:
            raise SystemExit('Corrupt cache input: ' + path.name)
    return path


manifest = json.loads(verified_blob(manifests[0]['digest']).read_text())
args.destination.mkdir(mode=0o700)
for layer in manifest['layers']:
    with tarfile.open(verified_blob(layer['digest']), mode='r:*') as archive:
        archive.extractall(args.destination, filter='data')
blobs = list((args.destination / 'blob').glob('sha256-*'))
if not blobs:
    raise SystemExit('No registry blobs were unpacked')
for path in blobs:
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != path.name.removeprefix('sha256-'):
            raise SystemExit('Corrupt registry blob')
print('Verified and unpacked', len(blobs), 'registry blobs to', args.destination)
