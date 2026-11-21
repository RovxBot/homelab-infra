// Run through the same pinned Renovate package as the strict validator:
// npx --yes --package renovate@44.126.1 -c 'node --test .github/tests/renovate-policy.test.cjs'
// Uses Renovate's real preset resolver, matchers, config merger, extraction,
// versioning and branch generator. No registry or GitHub access is needed.
const { test, before } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync, existsSync, realpathSync } = require('node:fs');
const { resolve, dirname, join } = require('node:path');
const { pathToFileURL } = require('node:url');
const root = resolve(__dirname, '../..');
const raw = JSON.parse(readFileSync(join(root, 'renovate.json')));
let config, merge, applyRules, branchify, extractRegex, extractKubernetes, compile, scheduled, luxon, getVersioning;
let renovateDir;

before(async () => {
  const binary = process.env.PATH.split(':').map(p => join(p, 'renovate')).find(existsSync);
  assert.ok(binary, 'Run with npx --package renovate@<CI version> -c');
  renovateDir = dirname(dirname(realpathSync(binary)));
  const version = JSON.parse(readFileSync(join(renovateDir, 'package.json'))).version;
  const ci = readFileSync(join(root, '.github/workflows/ci.yml'), 'utf8');
  assert.equal(version, ci.match(/renovate@(\d+\.\d+\.\d+)/)[1]);
  const load = p => import(pathToFileURL(join(renovateDir, 'dist', p)));
  await (await load('logger/index.js')).init();
  ({ mergeChildConfig: merge } = await load('config/utils.js'));
  const { getConfig } = await load('config/defaults.js');
  const { resolveConfigPresets } = await load('config/presets/index.js');
  ({ applyPackageRules: applyRules } = await load('util/package-rules/index.js'));
  ({ branchifyUpgrades: branchify } = await load('workers/repository/updates/branchify.js'));
  ({ extractPackageFile: extractRegex } = await load('modules/manager/custom/regex/index.js'));
  ({ extractPackageFile: extractKubernetes } = await load('modules/manager/kubernetes/extract.js'));
  ({ compile } = await load('util/template/index.js'));
  ({ isScheduledNow: scheduled } = await load('workers/repository/update/branch/schedule.js'));
  ({ get: getVersioning } = await load('modules/versioning/index.js'));
  luxon = await import(pathToFileURL(join(renovateDir, '../luxon/build/es6/luxon.mjs')));
  config = merge(getConfig(), (await resolveConfigPresets(raw)).config);
  Object.assign(config, { repoIsOnboarded: true, errors: [], warnings: [], baseBranch: 'main' });
});

function update(depName, packageFile, updateType = 'patch', options = {}) {
  const manager = options.manager ?? (packageFile.startsWith('.github/') ? 'github-actions' : 'kubernetes');
  const datasource = options.datasource ?? (manager === 'github-actions' ? 'github-tags' : 'docker');
  const currentValue = options.currentValue ?? '1.2.3';
  const newValue = options.newValue ?? (updateType === 'major' ? '2.0.0' : updateType === 'minor' ? '1.3.0' : '1.2.4');
  const dep = {
    depName, packageName: depName, datasource, currentValue,
    currentDigest: 'sha256:' + 'a'.repeat(64),
    depType: options.depType ?? (manager === 'github-actions' ? 'action' : 'container'),
    updates: [{ updateType, newValue, newVersion: newValue, newMajor: Number(newValue.match(/\d+/)?.[0] ?? 2),
      newDigest: 'sha256:' + 'b'.repeat(64), ...options.upgrade }],
  };
  return { manager, packageFile, dep };
}

async function branches(fixtures) {
  const files = {};
  for (const { manager, packageFile, dep } of fixtures) {
    files[manager] ??= [];
    let file = files[manager].find(f => f.packageFile === packageFile);
    if (!file) files[manager].push(file = { packageFile, deps: [] });
    file.deps.push(dep);
  }
  return (await branchify(config, files)).branches;
}

async function effective(fixture) {
  const { manager, packageFile, dep } = fixture;
  return applyRules(merge(config, { manager, packageFile, ...dep, ...dep.updates[0] }));
}

const ordinary = [
  ['ghcr.io/immich-app/immich-server', 'apps/immich/server.yaml'],
  ['ghcr.io/immich-app/immich-machine-learning', 'apps/immich/machine-learning.yaml'],
  ['redis', 'apps/immich/redis.yaml'], ['ghcr.io/immich-app/postgres', 'apps/immich/postgres.yaml'],
  ['ghcr.io/home-assistant/home-assistant', 'apps/home-assistant/deployment.yaml'],
  ['alpine/git', 'apps/home-assistant/deployment.yaml'],
  ['invoiceninja/invoiceninja', 'apps/invoiceninja/deployment.yaml'], ['mysql', 'apps/invoiceninja/mysql.yaml'],
  ['nginxinc/nginx-unprivileged', 'apps/invoiceninja/deployment.yaml'],
  ['docker.gitea.com/gitea', 'apps/gitea/helmrelease.yaml', 'regex'],
  ['docker.gitea.com/gitea-mcp-server', 'apps/gitea/mcp.yaml'],
  ['docker.gitea.com/runner', 'apps/gitea-runners/runner.yaml'],
  ['postgres', 'apps/gitea/database.yaml'], ['restic/restic', 'ops/gitea/images/backup.Dockerfile', 'dockerfile'],
  ['wger/server', 'apps/wger/web.yaml'], ['wger/server', 'apps/wger/celery.yaml'],
  ['postgres', 'apps/wger/postgres.yaml'], ['journeyapps/powersync-service', 'apps/wger/powersync.yaml'],
  ['linuxserver/radarr', 'apps/radarr/deployment.yaml'], ['busybox', 'apps/jellyfin/deployment.yaml'],
  ['jellyfin/jellyfin', 'apps/jellyfin/deployment.yaml'], ['alpine', 'apps/wotlk/worldserver-deployment.yaml'],
  ['bitnami/kubectl', 'apps/wotlk/restart-cronjob.yaml'], ['restic/restic', 'infra/backups/restic-immich-photos.yaml'],
  ['postgres', 'terraform/oci-matrix-free-tier/variables.tf', 'regex'],
];

test('all ordinary application, helper and Terraform image patches use one exact maintenance branch and title', async () => {
  const fixtures = ordinary.map(([name, file, manager]) => update(name, file, 'patch', { manager }));
  fixtures.push(update('actions/checkout', '.github/workflows/ci.yml', 'digest'));
  fixtures.push(update('gitea', 'apps/gitea/helmrelease.yaml', 'patch', { manager: 'flux', datasource: 'helm' }));
  const result = await branches(fixtures);
  assert.equal(result.length, 1);
  const [batch] = result;
  assert.equal(batch.branchName, 'renovate/weekly-maintenance');
  assert.equal(batch.prTitle, 'Update weekly maintenance');
  assert.equal(batch.upgrades.length, fixtures.length);
  assert.equal(batch.dependencyDashboardApproval, false);
  assert.equal(batch.automerge, false);
  assert.equal(batch.rebaseWhen, 'conflicted');
  assert.equal(batch.updateNotScheduled, false);
  assert.equal(batch.prPriority, 10);
});

test('a single pin, pinDigest, digest or patch never waits for additional dependencies or gets a separate pin branch', async () => {
  for (const type of ['pin', 'pinDigest', 'digest', 'patch']) {
    const [batch] = await branches([update('alpine', 'apps/sabnzbd/deployment.yaml', type)]);
    assert.equal(batch.branchName, 'renovate/weekly-maintenance', type);
    assert.equal(batch.prTitle, 'Update weekly maintenance', type);
    assert.equal(batch.minimumGroupSize, 1);
    assert.equal(batch.rebaseWhen, 'conflicted');
  }
});

test('the explicit minor allowlist batches while an unknown minor stays outside', async () => {
  const rule = raw.packageRules.find(r => r.description.startsWith('Explicitly approved minor'));
  for (const name of rule.matchPackageNames) {
    const file = name === 'gitea' ? 'apps/gitea/helmrelease.yaml' : 'apps/example/deployment.yaml';
    const [branch] = await branches([update(name, file, 'minor', name === 'gitea' ? { manager: 'flux', datasource: 'helm' } : {})]);
    assert.equal(branch.branchName, 'renovate/weekly-maintenance', name);
    assert.equal(branch.dependencyDashboardApproval, false, name);
  }
  const [unknown] = await branches([update('example/unknown-app', 'apps/example/deployment.yaml', 'minor')]);
  assert.notEqual(unknown.branchName, 'renovate/weekly-maintenance');
});

test('unrelated application majors form separate release branches, with coupled containers together', async () => {
  const result = await branches([
    update('ghcr.io/immich-app/immich-server', 'apps/immich/server.yaml', 'major'),
    update('ghcr.io/immich-app/immich-machine-learning', 'apps/immich/machine-learning.yaml', 'major'),
    update('jellyfin/jellyfin', 'apps/jellyfin/deployment.yaml', 'major'),
    update('invoiceninja/invoiceninja', 'apps/invoiceninja/deployment.yaml', 'major'),
    update('nginxinc/nginx-unprivileged', 'apps/invoiceninja/deployment.yaml', 'major'),
    update('wger/server', 'apps/wger/web.yaml', 'major'), update('wger/server', 'apps/wger/celery.yaml', 'major'),
  ]);
  assert.equal(result.length, 4);
  assert.equal(result.find(b => b.branchName === 'renovate/immich-release').upgrades.length, 2);
  assert.equal(result.find(b => b.branchName === 'renovate/invoiceninja-release').upgrades.length, 2);
  assert.equal(result.find(b => b.branchName === 'renovate/wger-release').upgrades.length, 2);
  assert.ok(result.every(b => b.branchName !== 'renovate/weekly-maintenance' && !b.automerge));
});

test('runner majors update both deployments in one gated branch; runner minors remain maintenance', async () => {
  const files = ['apps/gitea-runners/runner.yaml', 'apps/gitea-runners/wowdecomp/resources.yaml'];
  const [major] = await branches(files.map(file => update('docker.gitea.com/runner', file, 'major')));
  assert.equal(major.upgrades.length, 2);
  assert.equal(major.branchName, 'renovate/gitea-runners');
  assert.equal(major.dependencyDashboardApproval, true);
  const [minor] = await branches(files.map(file => update('docker.gitea.com/runner', file, 'minor')));
  assert.equal(minor.branchName, 'renovate/weekly-maintenance');
});

test('CI Node and Python line upgrades and independent Action majors never share a branch', async () => {
  const result = await branches([
    update('node', '.github/workflows/ci.yml', 'major', { depType: 'uses-with', datasource: 'node-version' }),
    update('python', '.github/workflows/ci.yml', 'minor', { depType: 'uses-with', datasource: 'python-version' }),
    update('actions/checkout', '.github/workflows/ci.yml', 'major'),
    update('actions/setup-python', '.github/workflows/ci.yml', 'major'),
    update('ubuntu', '.github/workflows/gitea-toolchain-image.yml', 'major', { depType: 'github-runner', datasource: 'github-runners' }),
  ]);
  assert.equal(result.length, 5);
  assert.ok(result.every(b => b.branchName !== 'renovate/weekly-maintenance'));
  const runtimes = result.filter(b => b.upgrades[0].depType === 'uses-with');
  assert.equal(runtimes.length, 2);
  assert.ok(runtimes.every(b => b.dependencyDashboardApproval));
});

test('all critical controllers, identity images and Terraform providers override routine grouping', async () => {
  for (const type of ['patch', 'digest', 'pin', 'pinDigest', 'minor', 'major']) {
    for (const fixture of [
      update('longhorn', 'infra/longhorn/longhorn-helmrelease.yaml', type, { manager: 'flux', datasource: 'helm' }),
      update('kyverno', 'infra/kyverno/helmrelease.yaml', type, { manager: 'flux', datasource: 'helm' }),
      update('gpu-operator', 'infra/gpu-operator/helmrelease.yaml', type, { manager: 'flux', datasource: 'helm' }),
      update('registry.k8s.io/sig-storage/snapshot-controller', 'infra/snapshot-controller/deployment.yaml', type),
      update('vaultwarden/server', 'apps/vaultwarden/deployment.yaml', type),
      update('quay.io/oauth2-proxy/oauth2-proxy', 'apps/openbrew/deployment.yaml', type),
      update('ghcr.io/element-hq/synapse', 'terraform/oci-matrix-free-tier/variables.tf', type, { manager: 'regex' }),
      update('oracle/oci', 'terraform/oci-free-tier/main.tf', type, { manager: 'terraform', datasource: 'terraform-provider' }),
    ]) {
      const result = await branches([fixture]);
      assert.equal(result.length, 1, fixture.dep.depName);
      assert.notEqual(result[0].branchName, 'renovate/weekly-maintenance', type + fixture.dep.depName);
      assert.equal(result[0].dependencyDashboardApproval, true, type + fixture.dep.depName);
    }
  }
});

test('unrelated Terraform providers remain independent while the same provider in two roots is coordinated', async () => {
  const result = await branches([
    update('oracle/oci', 'terraform/oci-free-tier/main.tf', 'minor', { manager: 'terraform', datasource: 'terraform-provider' }),
    update('oracle/oci', 'terraform/oci-matrix-free-tier/main.tf', 'minor', { manager: 'terraform', datasource: 'terraform-provider' }),
    update('hashicorp/random', 'terraform/oci-free-tier/main.tf', 'minor', { manager: 'terraform', datasource: 'terraform-provider' }),
  ]);
  assert.equal(result.length, 2);
  assert.equal(result.find(b => b.upgrades[0].depName === 'oracle/oci').upgrades.length, 2);
});

test('only named Python helper images bypass controller gates; interpreter majors remain gated', async () => {
  const helper = raw.packageRules.find(r => r.description.startsWith('Only the tested Python'));
  for (const file of helper.matchFileNames) {
    for (const type of helper.matchUpdateTypes) {
      const [branch] = await branches([update('python', file, type)]);
      assert.equal(branch.branchName, 'renovate/weekly-maintenance', type + file);
      assert.equal(branch.dependencyDashboardApproval, false);
    }
    const [major] = await branches([update('python', file, 'major')]);
    assert.notEqual(major.branchName, 'renovate/weekly-maintenance');
    assert.equal(major.dependencyDashboardApproval, true);
    const [other] = await branches([update('controller/example', file, 'patch')]);
    if (file.startsWith('infra/kyverno/') || file.startsWith('infra/longhorn/')) {
      assert.notEqual(other.branchName, 'renovate/weekly-maintenance');
      assert.equal(other.dependencyDashboardApproval, true);
    }
  }
});

test('Home Assistant failing release remains gated, patches and supporting images batch, and CI Python stays constrained', async () => {
  for (const type of ['major', 'minor']) {
    const [release] = await branches([update('ghcr.io/home-assistant/home-assistant', 'apps/home-assistant/deployment.yaml', type)]);
    assert.equal(release.branchName, 'renovate/home-assistant');
    assert.equal(release.dependencyDashboardApproval, true);
  }
  const python = await effective(update('python', '.github/workflows/home-assistant-checks.yml', 'minor', { depType: 'uses-with' }));
  assert.equal(python.allowedVersions, '/^3\\.14(?:\\.|$)/');
});

test('pipeline-owned references and all database majors stay disabled after every grouping override', async () => {
  const owned = raw.packageRules.find(r => r.description.startsWith('Immutable pipeline outputs'));
  const databases = raw.packageRules.find(r => r.description.startsWith('Database major'));
  for (const name of owned.matchPackageNames) {
    for (const type of ['patch', 'minor', 'major', 'digest', 'pin', 'pinDigest']) {
      assert.equal((await branches([update(name, 'apps/gitea-runners/wowdecomp/job-security.yaml', type)])).length, 0, name + type);
    }
  }
  for (const name of databases.matchPackageNames) {
    for (const [file, manager] of [['apps/immich/postgres.yaml', 'kubernetes'], ['ops/gitea/images/backup.Dockerfile', 'dockerfile'], ['terraform/oci-matrix-free-tier/variables.tf', 'regex']]) {
      assert.equal((await branches([update(name, file, 'major', { manager })])).length, 0, name + file);
    }
  }
  assert.equal((await branches([update('cilium', 'infra/cilium/ocirepository.yaml', 'digest', { manager: 'flux' })])).length, 0);
  assert.equal((await branches([update('docker.gitea.com/gitea', 'apps/gitea/helmrelease.yaml', 'patch', { manager: 'flux' })])).length, 0);
  assert.ok(config.ignorePaths.includes('clusters/home/flux-system/gotk-components.yaml'));
});

test('real security-policy and MCP extraction is disabled while Dockerfile bases stay tracked', async () => {
  for (const file of ['apps/gitea-runners/job-security.yaml', 'apps/gitea-runners/wowdecomp/job-security.yaml', 'infra/kubernetes-mcp/deployment.yaml']) {
    const extracted = extractKubernetes(readFileSync(join(root, file), 'utf8'), file, {});
    const images = extracted.deps.filter(d => d.datasource === 'docker');
    assert.ok(images.length > 0, file);
    for (const dep of images) {
      assert.equal((await effective({ manager: 'kubernetes', packageFile: file, dep: { ...dep, updates: [{ updateType: 'digest' }] } })).enabled, false);
    }
  }
  const [base] = await branches([update('python', 'infra/kubernetes-mcp/Dockerfile', 'patch', { manager: 'dockerfile' })]);
  assert.equal(base.branchName, 'renovate/weekly-maintenance');
});

test('custom Gitea extraction keeps the rootless tag and digest atomic; Terraform extracts all three Docker images', () => {
  const [gitea, terraform] = raw.customManagers;
  const giteaFile = 'apps/gitea/helmrelease.yaml';
  const extracted = extractRegex(readFileSync(join(root, giteaFile), 'utf8'), giteaFile, gitea);
  assert.equal(extracted.deps.length, 1);
  assert.match(extracted.deps[0].currentValue, /^\d+\.\d+\.\d+-rootless$/);
  assert.match(extracted.deps[0].currentDigest, /^sha256:[a-f0-9]{64}$/);
  const replaced = compile(gitea.autoReplaceStringTemplate, { newValue: '1.28.0-rootless', newDigest: 'sha256:' + 'c'.repeat(64) });
  assert.match(replaced, /tag: 1\.28\.0\n      digest: sha256:c{64}\n      rootless: true/);
  const file = 'terraform/oci-matrix-free-tier/variables.tf';
  const images = extractRegex(readFileSync(join(root, file), 'utf8'), file, terraform).deps;
  assert.deepEqual(images.map(d => d.depName).sort(), ['ghcr.io/element-hq/synapse', 'postgres', 'vectorim/element-web']);
  assert.ok(images.every(d => /^sha256:[a-f0-9]{64}$/.test(d.currentDigest)));
});

test('versioning exceptions and compatibility caps survive classification', async () => {
  const kyverno = await effective(update('kyverno', 'infra/kyverno/helmrelease.yaml', 'minor', { manager: 'flux', datasource: 'helm' }));
  assert.equal(kyverno.allowedVersions, undefined);
  assert.equal(kyverno.automerge, false);
  assert.equal(kyverno.dependencyDashboardApproval, true);
  assert.equal(kyverno.groupName, 'kyverno');
  const wger = await effective(update('wger/server', 'apps/wger/web.yaml'));
  assert.equal(wger.ignoreUnstable, false);
  assert.ok(getVersioning(wger.versioning).isVersion('2.7.0-beta1'));
  const linux = await effective(update('linuxserver/sonarr', 'apps/sonarr/deployment.yaml'));
  assert.ok(getVersioning(linux.versioning).isVersion('4.0.20.3014-ls329'));
  const jelly = await effective(update('jellyfin/jellyfin', 'apps/jellyfin/deployment.yaml'));
  assert.ok(getVersioning(jelly.versioning).isGreaterThan('12.4', '10.11.0'));
  const [bird] = await branches([update('ghcr.io/tphakala/birdnet-go', 'apps/birdnet-go/deployment.yaml', 'major', { currentValue: '20261031', newValue: '20261114' })]);
  assert.equal(bird.branchName, 'renovate/weekly-maintenance');
  const birdPolicy = await effective(update('ghcr.io/tphakala/birdnet-go', 'apps/birdnet-go/deployment.yaml', 'major'));
  assert.equal(birdPolicy.allowedVersions, '/^\\d{8}$/');
  const immich = await effective(update('ghcr.io/immich-app/immich-server', 'apps/immich/server.yaml'));
  assert.equal(immich.enabled, true);
  assert.equal(immich.allowedVersions, '/^v\\d+\\.\\d+\\.\\d+$/');
});

test('digest and Action SHA pinning remain enabled and maintenance cannot be starved by exceptional PRs', async () => {
  const docker = await effective(update('alpine', 'apps/sabnzbd/deployment.yaml'));
  const action = await effective(update('actions/checkout', '.github/workflows/ci.yml'));
  assert.equal(docker.pinDigests, true);
  assert.equal(action.pinDigests, true);
  assert.equal(config.prConcurrentLimit, 0);
  assert.equal(config.branchConcurrentLimit, 0);
  assert.equal(config.prHourlyLimit, 0);
  assert.equal(config.automerge, false);
});

test('Monday schedule honors Adelaide summer/winter boundaries; urgent fixes bypass schedule, approval, grouping and limits', () => {
  const { Settings, DateTime } = luxon;
  const priorNow = Settings.now;
  try {
    for (const date of ['2026-11-16', '2026-06-15']) {
      for (const [time, expected] of [['05:59', false], ['06:00', true], ['17:59', true], ['18:00', false]]) {
        const timestamp = DateTime.fromISO(`${date}T${time}`, { zone: 'Australia/Adelaide' }).toMillis();
        Settings.now = () => timestamp;
        assert.equal(scheduled(config), expected, date + time);
      }
    }
    const tuesday = DateTime.fromISO('2026-11-17T12:00', { zone: 'Australia/Adelaide' }).toMillis();
    Settings.now = () => tuesday;
    assert.equal(scheduled(config), false);
    // Renovate represents vulnerabilityAlerts as a force config. Exercise its real merger.
    const security = merge({ ...config, groupName: 'weekly maintenance', dependencyDashboardApproval: true }, { force: config.vulnerabilityAlerts });
    assert.equal(scheduled(security), true);
    assert.equal(security.groupName, null);
    assert.equal(security.dependencyDashboardApproval, false);
    assert.equal(security.updateNotScheduled, true);
    assert.equal(security.automerge, false);
    assert.equal(security.prConcurrentLimit, 0);
    assert.equal(security.branchConcurrentLimit, 0);
    assert.equal(security.prHourlyLimit, 0);
    assert.equal(security.rebaseWhen, 'behind-base-branch');
    assert.ok(security.addLabels.includes('security'));
  } finally {
    Settings.now = priorNow;
  }
});

test('unrelated application database and OS release lines are independently reviewable', async () => {
  const result = await branches([
    update('mysql', 'apps/wotlk/mariadb-statefulset.yaml', 'minor'),
    update('mysql', 'apps/invoiceninja/mysql.yaml', 'minor'),
    update('ubuntu', 'ops/gitea/images/Dockerfile', 'major', { manager: 'dockerfile' }),
    update('ubuntu', 'ops/wotlk-images/Dockerfile', 'major', { manager: 'dockerfile' }),
  ]);
  assert.equal(result.length, 4);
  assert.ok(result.every(b => b.dependencyDashboardApproval && b.branchName !== 'renovate/weekly-maintenance'));
});

// Optional integration evidence from the supported --platform=local --dry-run=lookup
// JSON log. Recompute effective branches: raw lookup output includes disabled upgrades.
if (process.env.RENOVATE_LOOKUP_LOG) {
  test('live lookups produce one maintenance branch and preserve critical exclusions', async () => {
    const rows = readFileSync(process.env.RENOVATE_LOOKUP_LOG, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line));
    const lookup = rows.find(row => row.msg === 'packageFiles with updates');
    assert.ok(lookup, 'The live lookup must finish and log its extracted package updates');
    // A previous lookup assigned literal branch names using that run's config.
    // Remove those derived names before evaluating the current configuration.
    for (const files of Object.values(lookup.config)) {
      for (const file of files) {
        for (const dep of file.deps) {
          for (const upgrade of dep.updates ?? []) delete upgrade.branchName;
        }
      }
    }
    const result = (await branchify(config, lookup.config)).branches;
    const maintenance = result.filter(b => b.branchName === 'renovate/weekly-maintenance');
    assert.equal(maintenance.length, 1);
    assert.equal(maintenance[0].prTitle, 'Update weekly maintenance');
    const immich = maintenance[0].upgrades.filter(u => /^ghcr.io\/immich-app\/immich-/.test(u.depName));
    assert.equal(immich.length, 2);
    assert.equal(immich[0].newValue, immich[1].newValue);
    const forbidden = raw.packageRules.find(r => r.description.startsWith('Immutable pipeline outputs')).matchPackageNames;
    const databases = raw.packageRules.find(r => r.description.startsWith('Database major')).matchPackageNames;
    for (const branch of result) {
      for (const u of branch.upgrades) {
        assert.ok(!forbidden.includes(u.depName), u.depName);
        assert.ok(!(databases.includes(u.depName) && u.updateType === 'major'), u.depName);
      }
    }
    console.log('Effective live branches:', JSON.stringify(result.map(b => ({
      branch: b.branchName, title: b.prTitle, updates: b.upgrades.length,
      approval: b.dependencyDashboardApproval,
      packages: [...new Set(b.upgrades.map(u => u.depName))],
    }))));
  });
}
