#!/usr/bin/env python3
"""Resolve the proposed chart/app pair. No version fallback or release ceiling."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
import urllib.request
import yaml

ROOT=Path(__file__).resolve().parents[3]


def fetch(url):
    with urllib.request.urlopen(url,timeout=30) as response:
        return response.read()


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
    if not historical:
        assert chart_name in assets and chart_name+'.sigstore.json' in assets, 'Signed chart release assets required'
        assert assets[chart_name]['digest']=='sha256:'+digest
    folder.mkdir(parents=True,exist_ok=True)
    (folder/chart_name).write_bytes(blob)
    result=dict(chart=chart,application=version,source_commit=source,chart_sha256=digest,
                chart_signed=not historical, cli_sha256=assets[cli]['digest'].removeprefix('sha256:'),
                identity=f'https://github.com/kyverno/kyverno/.github/workflows/release-publish.yaml@refs/tags/v{version}')
    (folder/'target.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--chart')
    p.add_argument('--output',type=Path,default=ROOT/'.work/cel/trust')
    args=p.parse_args()
    chart=args.chart or str(yaml.safe_load((ROOT/'infra/kyverno/helmrelease.yaml').read_text())['spec']['chart']['spec']['version'])
    print(json.dumps(resolve(chart,args.output),indent=2))
