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
    return {'chart':str(doc['spec']['chart']['spec']['version']),
            'images':{k:v for k,v in values.items() if k in {'image','global','admissionController','backgroundController','cleanupController','reportsController','crds','upgrade'}}}


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
