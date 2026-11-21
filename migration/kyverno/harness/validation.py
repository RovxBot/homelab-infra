"""Reuse exact historical validation contracts or test the proposed release."""
import hashlib
import json
import os
from rehearse import ROOT, WORK, EVIDENCE, RESULTS, install, smoke, governance, record, prepare_reporting_rbac


def main():
    contract=json.loads((ROOT/'migration/kyverno/evidence/validation-contract.json').read_text())
    target=json.loads((WORK/'trust/target.json').read_text())
    if os.environ.get('REUSE_VALIDATION')=='true':
        assert contract.get('implementation_files'), 'Historical contract lacks complete implementation hashes: rerun validation'
        assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==digest for p,digest in contract['implementation_files'].items()), 'Validation implementation changed: rerun the contracts'
        assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==digest for p,digest in contract['files'].items()),'Validation inputs changed: rerun the contracts'
        assert all(target[k]==v for k,v in contract['target'].items()),'Release changed: actual proposed-version testing required'
        record('validation-evidence-reused',status='verified',source_run=contract['source_run'],files=len(contract['files']))
        return
    from equivalence import suite,whole_runners
    from reporting import run as reports
    install('1.19.2');smoke();governance('1.19.2');prepare_reporting_rbac()
    for engine in ['legacy','cel']:
        suite('1.19.2',engine);whole_runners('1.19.2',engine);reports('1.19.2',engine)
    version=target['application']
    install(version);smoke();governance(version)
    suite(version,'cel');whole_runners(version,'cel');reports(version,'cel')
    assert not any(r.get('status')=='failed' for r in RESULTS)
    record('proposed-validation-contracts',status='verified',version=version)
