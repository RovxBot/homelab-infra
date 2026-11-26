#!/usr/bin/env python3
"""Resolve the proposed chart/app pair. No version fallback or release ceiling."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
import urllib.request
import yaml

ROOT=Path(__file__).resolve().parents[3]


def fetch(url):
    headers={}
    if url.startswith('https://api.github.com/') and os.environ.get('GH_TOKEN'):
        headers['Authorization']='Bearer '+os.environ['GH_TOKEN']
    with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=30) as response:
        return response.read()


def supported_kubernetes(version):
    source='https://raw.githubusercontent.com/kyverno/website/main/src/content/docs/docs/installation/releases.md'
    raw=fetch(source)
    text=' '.join(raw.decode().replace('*','').replace('|',' ').split())
    release=re.search(r'Supported Release:\s*v(\d+\.\d+)',text)
    kube=re.search(r'Kubernetes Versions Supported:\s*v?1\.(\d+)\s*[-–—]\s*v?1\.(\d+)',text)
    assert release and kube, 'Vendor support matrix unavailable or changed; review required'
    assert release[1]=='.'.join(version.split('.')[:2]), 'Proposed release is not in the current vendor support matrix'
    assert int(kube[1]) <= 34 <= int(kube[2]), 'The production/rehearsal Kubernetes 1.34 minor is unsupported'
    return dict(source=source,document_sha256=hashlib.sha256(raw).hexdigest(),release=release[1],kubernetes_min_minor=int(kube[1]),kubernetes_max_minor=int(kube[2]),tested_minor=34)


def resolve(chart, folder):
    if not re.fullmatch(r'\d+\.\d+\.\d+', chart):
        raise ValueError('An exact stable chart is required')
    index=yaml.safe_load(fetch('https://kyverno.github.io/kyverno/index.yaml'))
    entry=next(e for e in index['entries']['kyverno'] if e['version']==chart)
    url=urllib.parse.urljoin('https://kyverno.github.io/kyverno/',entry['urls'][0])
    if urllib.parse.urlparse(url).hostname not in {'kyverno.github.io','github.com'}:
        raise ValueError('Untrusted chart URL')
    blob=fetch(url)
    digest=hashlib.sha256(blob).hexdigest()
    assert digest==entry['digest'], 'Chart digest differs from Helm index'
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        meta=yaml.safe_load(archive.extractfile('kyverno/Chart.yaml').read())
    version=str(meta['appVersion']).removeprefix('v')
    assert re.fullmatch(r'\d+\.\d+\.\d+',version), 'Stable application required'
    assert version.split('.')[0] == '1', 'Application major change requires a reviewed migration contract'
    assert version==str(entry['appVersion']).removeprefix('v') and str(meta['version'])==chart
    release=json.loads(fetch('https://api.github.com/repos/kyverno/kyverno/releases/tags/v'+version))
    assert not release['draft'] and not release['prerelease']
    assets={a['name']:a for a in release['assets']}
    cli=f'kyverno-cli_v{version}_linux_x86_64.tar.gz'
    assert cli in assets and cli+'.sigstore.json' in assets
    source=json.loads(fetch('https://api.github.com/repos/kyverno/kyverno/commits/v'+version))['sha']
    assert re.fullmatch('[0-9a-f]{40}',source)
    chart_name=f'kyverno-{chart}.tgz'
    # The immutable legacy baseline predates chart bundles. Every new chart
    # must supply a verified release bundle; absence never selects an older app.
    historical=chart=='3.9.2' and digest=='f6fe7184544d5e2ee33a77808c09e67aa603ce6560cc197038fde31d727c861e'
    support = None if historical else supported_kubernetes(version)
    if not historical:
        assert chart_name in assets and chart_name+'.sigstore.json' in assets, 'Signed chart release assets required'
        assert assets[chart_name]['digest']=='sha256:'+digest
    proposed=yaml.safe_load((ROOT/'infra/kyverno/helmrelease.yaml').read_text())['spec']['values']
    upgrade=proposed.get('upgrade',{})
    assert upgrade.get('legacyPolicyCheck',{}).get('enabled',True) is True
    assert upgrade.get('acknowledgeLegacyPoliciesNotEnforced',False) is False
    # A proposed image override must never be replaced by another release's
    # verified image while pretending to test the proposal.
    def check_images(obj):
        if not isinstance(obj,dict): return
        for key,value in obj.items():
            if key=='image' and isinstance(value,dict):
                assert not any(value.get(k) for k in ['tag','digest','repository']), 'Explicit image overrides require reviewed digest binding'
                assert value.get('registry','ghcr.io')=='ghcr.io', 'Unexpected image registry'
            check_images(value)
    check_images(proposed)
    folder.mkdir(parents=True,exist_ok=True)
    (folder/chart_name).write_bytes(blob)
    workflow='release.yaml' if historical else 'release-publish.yaml'
    result=dict(chart=chart,application=version,source_commit=source,chart_sha256=digest,
                chart_signed=not historical, kubernetes_support=support, cli_sha256=assets[cli]['digest'].removeprefix('sha256:'),
                identity=f'https://github.com/kyverno/kyverno/.github/workflows/{workflow}@refs/tags/v{version}')
    (folder/'target.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--chart')
    p.add_argument('--output',type=Path,default=ROOT/'.work/cel/trust')
    args=p.parse_args(argv)
    chart=args.chart or str(yaml.safe_load((ROOT/'infra/kyverno/helmrelease.yaml').read_text())['spec']['chart']['spec']['version'])
    print(json.dumps(resolve(chart,args.output),indent=2))


if __name__=='__main__':
    main()
