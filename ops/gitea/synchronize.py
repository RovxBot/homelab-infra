#!/usr/bin/env python3
"""Synchronize only the explicit repository allowlist through gitea-mirror."""
import http.cookiejar
import json
import os
import pathlib
import time
import urllib.error
import urllib.request

base = os.environ.get('MIRROR_URL', 'http://gitea-mirror.gitea.svc.cluster.local:4321')
origin = 'http://localhost:14321'
root = pathlib.Path(os.environ.get('STATE_DIR', '/data'))
config = json.loads(pathlib.Path(os.environ.get('REPOSITORY_CONFIG', '/scripts/protected-repositories.json')).read_text())
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def api(method, path, data=None, conflict=False):
    request = urllib.request.Request(base + path,
        headers={'Origin': origin, 'Content-Type': 'application/json'}, method=method,
        data=json.dumps(data).encode() if data is not None else None)
    try:
        with opener.open(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if conflict and error.code == 409:
            return json.load(error)
        raise RuntimeError('Mirror API failed: ' + path + ' HTTP ' + str(error.code)) from None


def wait(repository_id, previous):
    for _ in range(130):
        response = api('GET', '/api/repositories/' + repository_id)
        record = response.get('repository', response.get('data', response))
        status = record.get('status')
        if status in {'mirrored', 'synced'} and record.get('lastMirrored') != previous:
            return
        if status in {'failed', 'awaiting_approval'}:
            raise RuntimeError('Protected synchronization needs review: ' + repository_id)
        time.sleep(5)
    raise RuntimeError('Protected synchronization timed out')


try:
    api('POST', '/api/auth/sign-in/email', {'email': 'sam@cooked.beer',
                                          'password': os.environ['MIRROR_ADMIN_PASSWORD']})
    ids = []
    for repo in config['repositories']:
        owner, name = repo['source'].split('/')
        response = api('POST', '/api/sync/repository', {
            'owner': owner, 'repo': name, 'destinationOrg': config['owner']}, conflict=True)
        record = response.get('repository', response.get('data', {}).get('repository'))
        if not record or record['name'] != repo['name']:
            raise RuntimeError('Mirror API returned an unexpected repository')
        repository_id = record['id']
        if not record.get('mirroredLocation'):
            api('POST', '/api/job/mirror-repo', {'repositoryIds': [repository_id]})
            time.sleep(3)
            wait(repository_id, record.get('lastMirrored'))
        ids.append(repository_id)
    # Sync performs the actual Git refresh and issues/PR/release metadata sync;
    # re-running the initial mirror API alone only repairs its database status.
    for repository_id in ids:
        previous = api('GET', '/api/repositories/' + repository_id)['repository'].get('lastMirrored')
        api('POST', '/api/job/sync-repo', {'repositoryIds': [repository_id]})
        time.sleep(3)
        wait(repository_id, previous)
    temporary = root / 'metadata-sync-success.partial'
    temporary.write_text(str(int(time.time())))
    temporary.replace(root / 'metadata-sync-success.epoch')
    (root / 'metadata-sync-failed').unlink(missing_ok=True)
    print('Git and metadata synchronization completed for the explicit allowlist')
except Exception:
    (root / 'metadata-sync-failed').touch()
    raise
