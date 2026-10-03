const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const test = require('node:test');

// Exercise the actual inline Actions script with a stubbed GitHub API.
const workflow = readFileSync(resolve(__dirname, '../workflows/renovate-release-app-batches.yml'), 'utf8');
const source = workflow.split('          script: |\n')[1];
assert.ok(source, 'The workflow must contain its approval script.');
const lines = [];
for (const line of source.split('\n')) {
  if (line.trim() && !line.startsWith('            ')) break;
  lines.push(line.slice(12));
}
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const handler = new AsyncFunction('github', 'context', 'core', 'setTimeout', lines.join('\n'));

const primary = ' - [ ] <!-- approve-branch=renovate/jellyfin -->update jellyfin (`jellyfin/jellyfin`)';
const helper = ' - [ ] <!-- approve-branch=renovate/kyverno -->update python:3.14-alpine docker digest';
const heading = '## Pending Approval\n\n';

async function run({ body = '', event = 'issues', payloadBody = body, issues = [], getError } = {}) {
  const calls = { get: [], update: [], list: [], logs: [] };
  const github = { rest: { issues: {
    async get(params) {
      calls.get.push(params);
      if (getError) throw getError;
      return { data: { number: params.issue_number, body } };
    },
    async update(params) { calls.update.push(params); },
    async listForRepo(params) {
      calls.list.push(params);
      return { data: issues };
    },
  } } };
  const context = {
    repo: { owner: 'example', repo: 'homelab' },
    payload: event === 'issues' ? { issue: { number: 1, body: payloadBody } } : {},
  };
  await handler(github, context, { info: (message) => calls.logs.push(message) }, () => {
    assert.fail('The Dashboard workflow must not sleep or poll.');
  });
  return calls;
}

test('a Dashboard without pending approvals finishes after one read', async () => {
  const calls = await run({ body: '## Awaiting Schedule\n\nweekly maintenance\n' });
  assert.equal(calls.get.length, 1);
  assert.equal(calls.update.length, 0);
  assert.equal(calls.list.length, 0);
  assert.match(calls.logs.at(-1), /nothing to do/);
});

test('supporting-only batches finish successfully without polling or writing', async () => {
  const calls = await run({ body: heading + helper + '\n' });
  assert.equal(calls.get.length, 1);
  assert.equal(calls.update.length, 0);
  assert.match(calls.logs.at(-1), /nothing to do/);
});

test('a pending application release is approved with one update', async () => {
  const body = 'Dashboard introduction\n\n' + heading + primary + '\n';
  const calls = await run({ body });
  assert.equal(calls.get.length, 1);
  assert.deepEqual(calls.update, [{
    owner: 'example', repo: 'homelab', issue_number: 1,
    body: body.replace(primary, primary.replace('[ ]', '[x]')),
  }]);
});

test('multiple application releases share one write and preserve other approvals', async () => {
  const second = ' - [ ] <!-- approve-branch=renovate/home-assistant -->update ghcr.io/home-assistant/home-assistant';
  const checked = ' - [x] <!-- approve-branch=renovate/wger -->update wger/server';
  const body = heading + [helper, primary, second, checked].join('\n') + '\n';
  const calls = await run({ body });
  assert.equal(calls.update.length, 1);
  assert.equal(calls.update[0].body,
    body.replace(primary, primary.replace('[ ]', '[x]')).replace(second, second.replace('[ ]', '[x]')));
  assert.match(calls.logs.at(-1), /Approved 2/);
});

test('already approved application releases do not cause another write', async () => {
  const calls = await run({ body: heading + primary.replace('[ ]', '[x]') + '\n' });
  assert.equal(calls.get.length, 1);
  assert.equal(calls.update.length, 0);
});

test('primary images in scheduled, open or detected sections are ignored', async () => {
  const body = heading + helper + '\n\n## Awaiting Schedule\n' + primary +
    '\n\n## Open\n' + primary + '\n\n## Detected Dependencies\njellyfin/jellyfin\n';
  const calls = await run({ body });
  assert.equal(calls.get.length, 1);
  assert.equal(calls.update.length, 0);
});

test('approving a release preserves all surrounding sections', async () => {
  const body = '## Lookup Problems\nproblem\n\n' + heading + primary +
    '\n\n## Awaiting Schedule\n' + primary + '\n\n## Detected Dependencies\njellyfin/jellyfin\n';
  const calls = await run({ body });
  assert.equal(calls.update[0].body, body.replace(primary, primary.replace('[ ]', '[x]')));
});

test('CRLF line endings and indentation are preserved', async () => {
  const body = (heading + '\t' + primary + '\n\n## Open\nunchanged\n').replaceAll('\n', '\r\n');
  const calls = await run({ body });
  assert.equal(calls.update[0].body, body.replace(primary, primary.replace('[ ]', '[x]')));
});

test('an empty approval title cannot consume the next line', async () => {
  const body = heading + ' - [ ] <!-- approve-branch=renovate/kyverno -->\n' +
    'jellyfin/jellyfin\n' + helper + '\n';
  const calls = await run({ body });
  assert.equal(calls.update.length, 0);
});

test('a stale event payload cannot approve an application no longer pending', async () => {
  const calls = await run({ body: heading + helper, payloadBody: heading + primary });
  assert.equal(calls.get.length, 1);
  assert.equal(calls.update.length, 0);
});

test('a newer application release is read from current Dashboard state', async () => {
  const calls = await run({ body: heading + primary, payloadBody: heading + helper });
  assert.equal(calls.get.length, 1);
  assert.equal(calls.update.length, 1);
});

test('a config push discovers the Dashboard and reads it once', async () => {
  const calls = await run({ event: 'push', body: heading + primary, issues: [
    { number: 20, title: 'Other issue' },
    { number: 37, title: 'Renovate Dependency Dashboard' },
  ] });
  assert.equal(calls.list.length, 1);
  assert.equal(calls.get.length, 1);
  assert.equal(calls.get[0].issue_number, 37);
  assert.equal(calls.update[0].issue_number, 37);
});

test('a push with no Dashboard exits without reads or writes', async () => {
  const calls = await run({ event: 'push' });
  assert.equal(calls.list.length, 1);
  assert.equal(calls.get.length, 0);
  assert.equal(calls.update.length, 0);
});

test('a null Dashboard body is handled as having no approvals', async () => {
  const calls = await run({ body: null });
  assert.equal(calls.get.length, 1);
  assert.equal(calls.update.length, 0);
});

test('API failures propagate instead of reporting successful approval', async () => {
  await assert.rejects(() => run({ getError: new Error('GitHub unavailable') }), /GitHub unavailable/);
});
