#!/usr/bin/env python3
"""Upload an encrypted recovery archive outside the Restic repository it unlocks.

Uses the existing B2 application key in gitea-backup; never prints credentials.
Only writes gitea/bootstrap/*.gpg and matching checksum files in cooked-k8s.
Backblaze account access plus the separately stored GPG passphrase can recover
these objects without Kubernetes, SOPS or the Restic repository password.
"""
import argparse
import base64
import hashlib
import json
import pathlib
import subprocess
import urllib.error
import urllib.parse
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('archive', type=pathlib.Path)
args = parser.parse_args()
if args.archive.suffix != '.gpg' or not args.archive.is_file():
    parser.error('Supply the encrypted .gpg recovery archive')
secret = json.loads(subprocess.check_output([
    'kubectl', '-n', 'gitea', 'get', 'secret', 'gitea-backup', '-o', 'json']))
values = {key: base64.b64decode(value).decode() for key, value in secret['data'].items()}
credentials = base64.b64encode((values['AWS_ACCESS_KEY_ID'] + ':' + values['AWS_SECRET_ACCESS_KEY']).encode()).decode()


def request(url, authorization, data=None, headers=None):
    return urllib.request.urlopen(urllib.request.Request(url, data=data,
        headers={'Authorization': authorization} | (headers or {})), timeout=120)


auth = json.load(request('https://api.backblazeb2.com/b2api/v4/b2_authorize_account', 'Basic ' + credentials))
storage = auth['apiInfo']['storageApi']
token = auth['authorizationToken']


def api(method, payload):
    return json.load(request(storage['apiUrl'] + '/b2api/v4/' + method, token,
                             json.dumps(payload).encode(), {'Content-Type': 'application/json'}))


buckets = storage['allowed'].get('buckets') or []
bucket = next((b for b in buckets if b.get('name') == 'cooked-k8s'), None)
if not bucket:
    bucket = next(b for b in api('b2_list_buckets', {'accountId': auth['accountId'],
                                                   'bucketName': 'cooked-k8s'})['buckets']
                  if b['bucketName'] == 'cooked-k8s')
bucket_id = bucket.get('id', bucket.get('bucketId'))
for path in [args.archive, args.archive.with_suffix(args.archive.suffix + '.sha256')]:
    data = path.read_bytes()
    if len(data) > 20 * 1024 * 1024:
        raise SystemExit('Expected a small credential archive, not application data')
    name = 'gitea/bootstrap/' + path.name
    download = storage['downloadUrl'] + '/file/cooked-k8s/' + urllib.parse.quote(name, safe='/')
    try:
        previous = request(download, token).read()
        if previous != data:
            raise SystemExit('Refusing to replace a different recovery object: ' + name)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        upload = api('b2_get_upload_url', {'bucketId': bucket_id})
        json.load(request(upload['uploadUrl'], upload['authorizationToken'], data,
            {'X-Bz-File-Name': urllib.parse.quote(name, safe='/'),
             'Content-Type': 'application/octet-stream',
             'X-Bz-Content-Sha1': hashlib.sha1(data).hexdigest()}))
    restored = request(download, token).read()
    if hashlib.sha256(restored).digest() != hashlib.sha256(data).digest():
        raise SystemExit('Off-host recovery object verification failed')
    print('Uploaded/download-verified: cooked-k8s/' + name)
