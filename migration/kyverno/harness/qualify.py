"""Required Flux lifecycle and integrated upgrade qualification."""
import os
from rehearse import RESULTS, install, record, apply, load, k


def report_access(version):
    apply(load('infra/kyverno/automation/github-issue-sync-rbac.yaml'))
    user='system:serviceaccount:kyverno:kyverno-github-issue-sync'
    for kind in ['policyreports.wgpolicyk8s.io','clusterpolicyreports.wgpolicyk8s.io']:
        k('get',kind,'-A','--as',user)
    for kind in ['secrets','policyexceptions.kyverno.io','policyexceptions.policies.kyverno.io']:
        result=k('get',kind,'-A','--as',user,check=False)
        assert result.returncode and 'Forbidden' in result.stderr, result.stderr
    record('report-rbac',status='verified',version=version)



def main():
    scenario=os.environ['REHEARSAL_SCENARIO']
    assert scenario in ['validation','mutation','namespace-lifecycle','production-sequence'], 'Abandoned generator scenarios are not runnable qualification paths'
    if scenario=='validation':
        from validation import main as validate
        return validate()
    if scenario=='mutation':
        from mutation import run as mutation
        install('1.19.2')
        mutation('1.19.2','legacy');mutation('1.19.2','cel')
        install(os.environ['KYVERNO_TARGET_VERSION'])
        mutation(os.environ['KYVERNO_TARGET_VERSION'],'cel')
    else:
        from flux import Flux
        from namespace_lifecycle import run as lifecycle
        install('1.19.2')
        with Flux() as flux:
            if scenario=='production-sequence':
                from production_sequence import during
                lifecycle(flux,during)
            else:
                lifecycle(flux)
    assert not any(r.get('status')=='failed' for r in RESULTS), 'Required qualification failed'
    record('scenario',status='verified',scenario=scenario)
