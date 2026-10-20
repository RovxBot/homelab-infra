const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = { module: { exports: {} }, HTMLElement: class {}, URL, URLSearchParams, Intl, Date };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../config/www/birdnet-explorer.js'), 'utf8'), context);
const helpers = context.module.exports;

test('names and image credits cannot inject markup or executable URLs', () => {
  assert.equal(helpers.esc('<img onerror="alert(1)">'), '&lt;img onerror=&quot;alert(1)&quot;&gt;');
  for (const value of ['javascript:alert(1)', 'data:image/svg+xml,evil', 'http://example.org/a.jpg', 'https://user:secret@example.org/']) assert.equal(helpers.https(value), '');
  assert.equal(helpers.https('https://example.org/bird.jpg'), 'https://example.org/bird.jpg');
});

test('dates follow the station timezone and calendar navigation', () => {
  assert.equal(helpers.localDay('Australia/Adelaide', new Date('2026-10-19T14:00:00Z')), '2026-10-20');
  assert.equal(helpers.shiftDay('2026-10-01', -1), '2026-09-30');
  assert.equal(helpers.shiftDay('2026-12-31', 1), '2027-01-01');
});

test('search filters survive punctuation without becoming query parameters', () => {
  const query = new URLSearchParams(helpers.filtersQuery({ date: '2026-10-20', source: '2', confidence: .95,
    search: 'A&B + "bird"', offset: 24, rejected: true }));
  assert.equal(query.get('search'), 'A&B + "bird"');
  assert.equal(query.get('confidence'), '0.95');
  assert.equal(query.get('offset'), '24');
  assert.equal(query.get('rejected'), '1');
  assert.equal([...query.keys()].length, 6);
});

test('unreviewed model identifications are never presented as verified', () => {
  assert.equal(helpers.reviewLabel('correct'), 'Reviewed correct');
  assert.equal(helpers.reviewLabel('false_positive'), 'Rejected');
  assert.equal(helpers.reviewLabel('anything-else'), 'Unverified');
});
