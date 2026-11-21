#!/usr/bin/env python3
"""Select the exact proposed Helm pair; run compatibility on chart or image changes."""
import json
import os
from pathlib import Path
import subprocess
import yaml

PATH='infra/kyverno/helmrelease.yaml'


def release_inputs(doc):
    values=doc['spec'].get('values',{})
    images = {}
    def collect(obj, path=()):
        if not isinstance(obj, dict):
            return
        for key, value in obj.items():
            if key == 'image':
                images['.'.join((*path, key))] = value
            else:
                collect(value, (*path, key))
    collect(values)
    return {'chart':str(doc['spec']['chart']['spec']['version']),
            'images': images,
            'upgrade_safety': values.get('upgrade', {}),
            'crds': values.get('crds', {})}


def main():
    head=yaml.safe_load(Path(PATH).read_text())
    base_ref=os.environ.get('BASE_SHA')
    if base_ref:
        base=yaml.safe_load(subprocess.check_output(['git','show',base_ref+':'+PATH],text=True))
        changed=release_inputs(base)!=release_inputs(head)
    else:
        changed=False
    result={'changed':str(changed).lower(),'chart':str(head['spec']['chart']['spec']['version'])}
    with open(os.environ['GITHUB_OUTPUT'],'a') as output:
        for key,value in result.items(): output.write(f'{key}={value}\n')
    print(json.dumps(result))


if __name__=='__main__':main()
