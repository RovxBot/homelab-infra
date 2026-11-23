"""Public allowlist projection and mocked issue delivery contract. No network client."""
from dataclasses import dataclass
from datetime import timedelta
import re
from typing import Protocol

from .model import Workload, fingerprint, require, stamp, timestamp

END = '<!-- security-collector:end -->'
REPO = 'https://github.com/RovxBot/homelab-infra'


@dataclass(frozen=True)
class Publication:
    # Constructed from reviewed configuration, never telemetry.
    public_workload: Workload
    related_issues: tuple[int, ...] = ()
    remediation_issue: int | None = None

    def __post_init__(self):
        for number in self.related_issues + ((self.remediation_issue,) if self.remediation_issue else ()):
            require(type(number) is int and 0 < number < 10**9, 'public_reference_invalid')


def start(key):
    require(re.fullmatch('[a-f0-9]{64}', key) is not None, 'incident_key_invalid')
    return f'<!-- security-collector:{key} -->'


def render(incident, publication):
    """Never interpolate free-form event text, image names, paths or package names."""
    w = publication.public_workload
    runtime = incident['category'] == 'runtime'
    title = f"Security: {'runtime detection' if runtime else 'image vulnerabilities'} in {w.namespace}/{w.name}"
    identifiers = ', '.join(incident['identities']) or 'None in latest assessment'
    investigation = ('Investigate the rule against private process evidence and expected workload behaviour; record a reviewed disposition.'
                     if runtime else 'Review the deployed image and private assessment; qualify an image update through Renovate/CI, deploy through Flux, then reassess.')
    fix = ('Scanner reports a fix; applicability needs review.' if incident['remediation'] == 'fix_available'
           else 'No reviewed fix recorded; investigation required.')
    reviewed = publication.remediation_issue
    if reviewed:
        fix += f' Reviewed remediation: {REPO}/issues/{reviewed}.'
    related = ', '.join(f'{REPO}/issues/{n}' for n in publication.related_issues) or 'None configured'
    disposition = incident.get('reviewed_issue')
    lines = [start(incident['key']),
        f"Detected: {'runtime rule violation' if runtime else 'vulnerabilities in an application image'}.",
        f'Affected: {w.namespace}/{w.kind}/{w.name}, container {w.container}.',
        f"Severity: {incident['severity']}. Rule/advisories: {identifiers}.",
        f"Findings in latest evidence: {incident['finding_count']} (up to 50 identifiers shown).",
        f"First observed: {stamp(timestamp(incident['first']))}. Last observed: {stamp(timestamp(incident['last']))}.",
        f"Occurrences / assessments: {incident['count']}.",
        f"State: {incident['status']}. Evidence: {incident['freshness']}; confidence: {incident['confidence']}.",
        f"Disposition: {incident['resolution'] or 'pending investigation'}.",
        f"Still confirmed: {'yes' if incident['freshness'] == 'confirmed' and incident['status'] == 'open' else 'no current confirmation; inspect disposition and coverage'}.",
        f"Image digest: {incident['digest'] or 'unavailable'}.",
        f"Provenance: {incident['provenance']}.",
        f'Investigation: {investigation}', f'Remediation: {fix}', f'Related issues: {related}.']
    if disposition:
        lines.append(f'Reviewed disposition: {REPO}/issues/{disposition}.')
    return title, '\n\n'.join(lines + [END])


def merge_section(body, key, section):
    marker = start(key)
    require(body.count(marker) == 1 and body.count(END) == 1, 'owned_section_invalid')
    left = body.index(marker)
    right = body.index(END, left) + len(END)
    return body[:left] + section + body[right:]


@dataclass
class Issue:
    number: int
    body: str
    state: str = 'open'


class GitHub(Protocol):
    """Stage B lookup must be complete, scoped to this writer and include closed issues.

    create() is never blindly retried after an ambiguous outcome. Updates must use
    optimistic concurrency against the read body to protect concurrent human notes.
    """
    def lookup(self, key: str) -> Issue | None: ...
    def create(self, key: str, title: str, body: str) -> Issue: ...
    def update(self, issue: Issue, title: str, body: str, state: str) -> None: ...


class Delivery:
    def __init__(self, collector, github: GitHub, publications, *, enabled=False, allow_closure=False):
        self.collector, self.github, self.publications = collector, github, publications
        self.enabled, self.allow_closure = enabled, allow_closure

    def sync(self, now):
        if not self.enabled:
            return 'disabled'
        c = self.collector
        c.refresh(now)
        pending = False
        def reserve_call():
            with c.db:
                if c.db.execute("SELECT count(*) FROM api_calls").fetchone()[0] >= 60:
                    return False
                c.db.execute("INSERT INTO api_calls VALUES (?)", (stamp(now),))
            return True

        for incident in c.all():
            pub = self.publications.get(incident['workload_key'])
            if pub is None:
                continue
            key = incident['key']
            title, section = render(incident, pub)
            desired_state = 'closed' if self.allow_closure and incident['status'] == 'resolved' else 'open'
            revision = fingerprint((title, section, desired_state))
            row = c.db.execute('SELECT * FROM delivery WHERE key=?', (key,)).fetchone()
            if row is None and (incident['severity'] not in ('High', 'Critical')
                                or incident['freshness'] != 'confirmed'):
                continue
            if row and (row['published'] == revision or (row['next'] and now < timestamp(row['next']))):
                continue
            if c.db.execute('SELECT count(*) FROM writes WHERE at >= ?', (stamp(now - timedelta(hours=1)),)).fetchone()[0] >= 10:
                return 'write_rate_limit'
            if c.db.execute('SELECT count(*) FROM writes').fetchone()[0] >= 20:
                return 'daily_write_limit'
            try:
                if not reserve_call():
                    return 'api_rate_limit'
                issue = self.github.lookup(key)
                if issue is None:
                    if row and row['ambiguous']:
                        pending = True
                        continue  # Operator must reconcile an uncertain create; lookup absence is insufficient.
                    if c.db.execute('SELECT count(*) FROM writes WHERE creation=1').fetchone()[0] >= 3:
                        return 'create_rate_limit'
                    # Persist intent BEFORE calling an external API. A crash leaves a safe fence.
                    with c.db:
                        c.db.execute('INSERT INTO writes VALUES (?,1)', (stamp(now),))
                        c.db.execute('INSERT OR REPLACE INTO delivery VALUES (?,0,NULL,1,NULL)', (key,))
                    if not reserve_call():
                        # No create was attempted: safely release only this local fence.
                        with c.db:
                            c.db.execute('UPDATE delivery SET ambiguous=0 WHERE key=?', (key,))
                        return 'api_rate_limit'
                    issue = self.github.create(key, title, section)
                    # Creation is always open; a later pass can close if expressly enabled.
                    with c.db:
                        c.db.execute('UPDATE delivery SET ambiguous=0 WHERE key=?', (key,))
                    if desired_state == 'closed':
                        pending = True
                        continue
                else:
                    body = merge_section(issue.body, key, section)
                    if body != issue.body or desired_state != issue.state:
                        with c.db:
                            c.db.execute('INSERT INTO writes VALUES (?,0)', (stamp(now),))
                        if not reserve_call():
                            return 'api_rate_limit'
                        self.github.update(issue, title, body, desired_state)
                with c.db:
                    c.db.execute('INSERT OR REPLACE INTO delivery VALUES (?,0,NULL,0,?)', (key, revision))
            except Exception:
                pending = True
                # Includes outages, optimistic-concurrency failures and malformed owned sections.
                # Never persist exception messages, which can contain API tokens or bodies.
                attempts = min((row['attempts'] if row else 0) + 1, 10)
                with c.db:
                    c.db.execute('INSERT INTO delivery (key) VALUES (?) ON CONFLICT DO NOTHING', (key,))
                    c.db.execute('UPDATE delivery SET attempts=?, next=? WHERE key=?',
                                 (attempts, stamp(now + timedelta(seconds=min(3600, 30 * 2**attempts))), key))
        return 'pending' if pending else 'complete'
