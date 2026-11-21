#!/usr/bin/env python3
"""Refresh the deployable consumer's identity aliases from the migration contract."""
import json
from pathlib import Path
import pprint
import re

root=Path(__file__).resolve().parents[3]
rows=json.loads((root/'migration/kyverno/policy-map.json').read_text())
identities={((r.get('namespace','')+'/') if r.get('namespace') else '')+r['name']:(r['source_policy'],r['source_rule']) for r in rows}
# Namespaced live reports use ns/name. Also retain bare names emitted by the CLI.
for row in rows:
    identities.setdefault(row['name'],(row['source_policy'],row['source_rule']))
identities['longhorn-csi-registrar-lifecycle']=('longhorn-csi-registrar-lifecycle','use-native-registrar-shutdown')
text='POLICY_IDENTITIES = '+pprint.pformat(identities,width=100,sort_dicts=True)
p=root/'infra/kyverno/automation/github-issue-sync-configmap.yaml'
s=p.read_text()
a=s.index('    POLICY_IDENTITIES = ');b=s.index('    # END GENERATED POLICY IDENTITIES',a)
p.write_text(s[:a]+''.join('    '+line+'\n' for line in text.splitlines())+s[b:])
