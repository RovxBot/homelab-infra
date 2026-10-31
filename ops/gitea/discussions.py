"""Append-only preservation of GitHub PR discussions missing from the mirror app."""
import concurrent.futures
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request


def preserve_discussions(config, repository):
    source = repository['source']
    target = config['owner'] + '/' + repository['name']
    github = 'https://api.github.com/repos/' + source
    gitea = os.environ.get('GITEA_URL', 'http://gitea-protection.gitea.svc.cluster.local:8080') + '/api/v1/repos/' + target

    def api(base, path, data=None):
        token = os.environ['GITHUB_TOKEN' if base == github else 'GITEA_TOKEN']
        request = urllib.request.Request(base + path,
            headers={'Authorization': 'token ' + token, 'Content-Type': 'application/json',
                     'Accept': 'application/json', 'User-Agent': 'cooked-gitea-recovery'},
            data=json.dumps(data).encode() if data is not None else None)
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)

    def pages(base, path):
        rows, page = [], 1
        size = 100 if base == github else 50
        while True:
            query = urllib.parse.urlencode({'per_page' if base == github else 'limit': size, 'page': page})
            batch = api(base, path + ('&' if '?' in path else '?') + query)
            if not isinstance(batch, list):
                raise RuntimeError('Expected a discussion list')
            rows.extend(batch)
            if len(batch) < size:
                return rows
            page += 1
            if page > 1000:
                raise RuntimeError('Unexpected discussion pagination')

    issues = pages(gitea, '/issues?state=all&type=issues')
    destinations = {}
    for issue in issues:
        # Only the provenance header identifies an issue. Do not infer identity
        # from arbitrary links pasted elsewhere in an issue's discussion.
        body = issue.get('body') or ''
        match = re.search(r'(?:Original GitHub issue:|\*\*Original PR:\*\*|\*\*Original Pull Request:\*\*)\s*https://github\.com/'
                          + re.escape(source) + r'/(?:issues|pull)/(\d+)', body)
        if match:
            destinations[int(match[1])] = issue['number']
    existing = pages(gitea, '/issues/comments')
    markers = {marker for comment in existing for marker in re.findall(
        r'<!-- dr-discussion:([^ ]+) -->', comment.get('body') or '')}
    original_comments = {}
    for comment in existing:
        body = comment.get('body') or ''
        match = re.search(r'<!--\s*gh-comment-id:(\d+)\s*-->', body)
        if match and '\n\n' in body and 'dr-discussion:' not in body:
            original_comments[int(match[1])] = body.split('\n\n', 1)[1].strip()

    source_comments = pages(github, '/issues/comments')
    review_comments = pages(github, '/pulls/comments')
    pulls = pages(github, '/pulls?state=all')
    def reviews(pull):
        return [(pull['number'], review) for review in pages(github, '/pulls/' + str(pull['number']) + '/reviews')]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        review_records = [record for batch in pool.map(reviews, pulls) for record in batch]
    records = []
    for comment in source_comments:
        match = re.search(r'/issues/(\d+)$', comment['issue_url'])
        if not match:
            raise RuntimeError('Unexpected GitHub comment identity')
        records.append(('comment', int(match[1]), comment))
    for comment in review_comments:
        match = re.search(r'/pulls/(\d+)$', comment['pull_request_url'])
        if not match:
            raise RuntimeError('Unexpected GitHub review comment identity')
        records.append(('review-comment', int(match[1]), comment))
    records.extend(('review', number, review) for number, review in review_records)
    added = 0
    for kind, number, record in records:
        if number not in destinations:
            raise RuntimeError('Discussion has no locally preserved issue/PR: ' + str(number))
        payload = json.dumps(record, sort_keys=True, ensure_ascii=False, indent=2)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        marker = kind + ':' + str(record['id']) + ':' + digest
        if marker in markers:
            continue
        if kind == 'comment' and original_comments.get(record['id']) == (record.get('body') or '').strip():
            continue
        author = record.get('user', {}).get('login', 'unknown')
        body = ('<!-- dr-discussion:' + marker + ' -->\n'
                + '**Preserved GitHub ' + kind + '** by @' + author + '\n\n'
                + record.get('html_url', '') + '\n\n'
                + (record.get('body') or '_No text; review state is preserved below._')
                + '\n\n<details><summary>Original discussion metadata and inline context</summary>\n\n'
                + '```json\n' + payload + '\n```\n</details>')
        # This proxy permits only comments under the configured destinations;
        # it denies deletes and all writes outside the allowlist.
        api(gitea, '/issues/' + str(destinations[number]) + '/comments', {'body': body})
        markers.add(marker)
        added += 1
    result = {'repository': source, 'source_comments': len(source_comments),
              'review_comments': len(review_comments), 'reviews': len(review_records),
              'new_discussion_snapshots': added}
    print(json.dumps(result), flush=True)
    return result
