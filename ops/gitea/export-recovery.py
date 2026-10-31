#!/usr/bin/env python3
"""Encrypt recovery credentials and the SOPS identity directly to GPG ciphertext.

GPG asks the operator for an independent archive passphrase. No plaintext key,
archive or passphrase is written to disk or printed. Keep that passphrase in a
password manager, separately from both this archive and the cluster.
"""
import argparse
import base64
import hashlib
import io
import json
import pathlib
import subprocess
import tarfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', type=pathlib.Path)
parser.add_argument('--verify', action='store_true')
args = parser.parse_args()
names = ['gitea-credentials', 'gitea-admin', 'gitea-entra', 'gitea-mirror',
         'gitea-backup', 'gitea-runner']
if args.verify:
    plaintext = subprocess.check_output(['gpg', '--decrypt', str(args.output)])
    with tarfile.open(fileobj=io.BytesIO(plaintext), mode='r:gz') as archive:
        expected = {'sops-age/age.agekey'} | {'secrets/' + name + '.json' for name in names}
        if not expected.issubset(set(archive.getnames())):
            raise SystemExit('Recovery archive is incomplete')
        age = archive.extractfile('sops-age/age.agekey').read()
        if b'AGE-SECRET-KEY-' not in age:
            raise SystemExit('Expected the recovery age identity')
        for name in names:
            if not json.load(archive.extractfile('secrets/' + name + '.json')).get('data'):
                raise SystemExit('Expected recovery Secret data')
    print('Decryption and all seven recovery entries verified without extraction')
else:
    if args.output.exists():
        raise SystemExit('Refusing to overwrite an existing recovery archive')
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        def add(name, data):
            member = tarfile.TarInfo(name)
            member.mode = 0o600
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        for name in names:
            namespace = 'gitea-runners' if name == 'gitea-runner' else 'gitea'
            secret = json.loads(subprocess.check_output([
                'kubectl', '-n', namespace, 'get', 'secret', name, '-o', 'json']))
            record = {'apiVersion': 'v1', 'kind': 'Secret', 'type': secret.get('type', 'Opaque'),
                      'metadata': {'name': name, 'namespace': namespace}, 'data': secret['data']}
            add('secrets/' + name + '.json', json.dumps(record).encode())
        secret = json.loads(subprocess.check_output([
            'kubectl', '-n', 'flux-system', 'get', 'secret', 'sops-age', '-o', 'json']))
        identities = [base64.b64decode(value) for value in secret['data'].values()]
        identities = [value for value in identities if b'AGE-SECRET-KEY-' in value]
        if len(identities) != 1:
            raise SystemExit('Expected exactly one existing SOPS age identity')
        add('sops-age/age.agekey', identities[0])
    temporary = args.output.with_suffix(args.output.suffix + '.partial')
    try:
        subprocess.run(['gpg', '--symmetric', '--cipher-algo', 'AES256', '--no-symkey-cache',
                        '--output', str(temporary)], input=buffer.getvalue(), check=True)
        temporary.replace(args.output)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_suffix(args.output.suffix + '.sha256').write_text(digest + '  ' + args.output.name + '\n')
    print('Independent encrypted recovery archive created:', args.output)
