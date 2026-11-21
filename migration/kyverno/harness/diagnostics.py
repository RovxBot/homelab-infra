"""Continuous API watches and independent snapshots; missing is never inferred."""
import json
import subprocess
import urllib.parse
from rehearse import CONTEXT, EVIDENCE, get, k, record
from generation import OUTPUTS


class Diagnostics:
    def __init__(self, namespace):
        self.ns = namespace
        self.streams = []

    def snapshot(self, stage):
        queries = [(kind, name, self.ns) for kind, name in OUTPUTS] + [
            ('namespaces', self.ns, ''), ('generatingpolicies', '', ''),
            ('clusterpolicies', '', ''), ('updaterequests.kyverno.io', '', ''),
            ('deployments', '', 'kyverno'), ('pods', '', 'kyverno')]
        rows = []
        for kind, name, ns in queries:
            args = ['get',kind]+([name] if name else [])+(['-n',ns] if ns else ['-A'])+['-o','json']
            p = k(*args, check=False)
            rows.append(dict(kind=kind, name=name, namespace=ns,
                status='present' if p.returncode == 0 else 'missing' if '(NotFound)' in p.stderr else 'unavailable',
                object=json.loads(p.stdout) if p.returncode == 0 else None, stderr=p.stderr))
        for component in ['background','admission','cleanup']:
            for previous in [False,True]:
                p=k('logs','-n','kyverno','-l',f'app.kubernetes.io/component={component}-controller','--all-containers','--tail=1000',*(['--previous'] if previous else []),check=False)
                (EVIDENCE/f'{self.ns}-{stage}-{component}-{"previous" if previous else "current"}.log').write_text(p.stdout+p.stderr)
        (EVIDENCE/f'{self.ns}-{stage}-snapshot.json').write_text(json.dumps(rows,indent=2)+'\n')
        return rows

    def start(self):
        # Namespace exists, but outputs need not. List RV permits a watch from
        # the empty set and retains the entire upgrade/restart interval.
        paths = [('/api/v1/namespaces/'+self.ns+'/'+kind, kind) for kind,_ in OUTPUTS if kind != 'networkpolicies']
        paths += [('/apis/networking.k8s.io/v1/namespaces/'+self.ns+'/networkpolicies','networkpolicies'),
                  ('/api/v1/namespaces','triggers'),
                  ('/apis/policies.kyverno.io/v1/generatingpolicies','policies'),
                  ('/apis/kyverno.io/v2/updaterequests','urs'),
                  ('/apis/apps/v1/namespaces/kyverno/deployments','controllers')]
        for path, label in paths:
            listed=json.loads(k('get','--raw',path).stdout)
            params=urllib.parse.urlencode(dict(watch='true',resourceVersion=listed['metadata']['resourceVersion'],allowWatchBookmarks='true'))
            out=(EVIDENCE/f'{self.ns}-watch-{label}.jsonl').open('w')
            err=(EVIDENCE/f'{self.ns}-watch-{label}.stderr').open('w')
            proc=subprocess.Popen(['kubectl','--context',CONTEXT,'get','--raw',path+'?'+params],stdout=out,stderr=err)
            self.streams.append((label,proc,out,err))

    def stop(self):
        exited=[]
        for label,proc,out,err in self.streams:
            if proc.poll() is not None:
                exited.append(dict(stream=label,exit_code=proc.returncode))
            else:
                proc.terminate()
            proc.wait(timeout=5)
            out.close();err.close()
        record('diagnostic-watches', status='failed' if exited else 'verified',namespace=self.ns, early_exits=exited,
               scope='API events only; traffic probes separately establish CNI enforcement')
