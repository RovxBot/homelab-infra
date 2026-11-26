"""Run fresh admission and reporting contracts for the proposed release."""
import json
import os
from rehearse import WORK, RESULTS, install, smoke, governance, record, prepare_reporting_rbac


def main():
    if os.environ.get('REUSE_VALIDATION', '').lower() == 'true':
        raise ValueError('REUSE_VALIDATION=true is no longer supported; fresh validation is required')
    target=json.loads((WORK/'trust/target.json').read_text())
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
