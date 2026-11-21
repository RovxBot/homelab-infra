"""Disposable Flux source/reconciliations; never imports a production source."""
import json
import subprocess
import time
import yaml
from rehearse import WORK,EVIDENCE,ROOT,run,apply,k,get,wait,record


class Flux:
    def __enter__(self):
        self.base = WORK / "git-server"
        self.base.mkdir(exist_ok=True)
        self.repo = WORK / "git-working"
        self.repo.mkdir(exist_ok=True)
        run(["git", "init", "--bare", str(self.base / "rehearsal.git")])
        run(
            [
                "git",
                "--git-dir",
                str(self.base / "rehearsal.git"),
                "symbolic-ref",
                "HEAD",
                "refs/heads/main",
            ]
        )
        run(["git", "init", "-b", "main", str(self.repo)])
        for key, value in [
            ("user.email", "rehearsal@example.invalid"),
            ("user.name", "Isolated rehearsal"),
        ]:
            run(["git", "-C", str(self.repo), "config", key, value])
        self.gateway = run(
            [
                "docker",
                "inspect",
                "kyverno-cel-rehearsal-control-plane",
                "--format",
                '{{(index .NetworkSettings.Networks "kind").Gateway}}',
            ]
        ).stdout.strip()
        self.http_log = (EVIDENCE / "git-http.log").open("w")
        self.server = subprocess.Popen(
            [
                "python3",
                str(ROOT / "migration/kyverno/harness/git_http.py"),
                "--port",
                "18080",
                "--bind",
                self.gateway,
                "--directory",
                str(self.base),
            ],
            stdout=self.http_log,
            stderr=self.http_log,
        )
        return self

    def publish(self, stage):
        for args in [['add','.'],['commit','--allow-empty','-m',stage],['push',str(self.base/'rehearsal.git'),'HEAD:main']]:
            run(['git','-C',str(self.repo),*args])
        run(['git','--git-dir',str(self.base/'rehearsal.git'),'update-server-info'])
        sha=run(['git','-C',str(self.repo),'rev-parse','HEAD']).stdout.strip()
        source = {
            "apiVersion": "source.toolkit.fluxcd.io/v1",
            "kind": "GitRepository",
            "metadata": {"name": "rehearsal", "namespace": "flux-system"},
            "spec": {
                "interval": "1h",
                "timeout": "30s",
                "url": f"http://{self.gateway}:18080/rehearsal.git",
                "ref": {"commit": sha},
            },
        }
        apply(source)
        k(
            "annotate",
            "gitrepository",
            "rehearsal",
            "-n",
            "flux-system",
            "--overwrite",
            f"reconcile.fluxcd.io/requestedAt={time.time_ns()}",
        )
        wait(
            "Flux source " + stage,
            lambda: get("gitrepository", "rehearsal", "flux-system")
            .get("status", {})
            .get("artifact", {})
            .get("revision", "")
            .endswith(sha),
        )
        return sha

    def reconcile(self, ks, stage, expect_ready=True):
        apply(ks)
        k('annotate','kustomization',ks['metadata']['name'],'-n','flux-system','--overwrite',f'reconcile.fluxcd.io/requestedAt={time.time_ns()}')
        if expect_ready:
            sha=get('gitrepository','rehearsal','flux-system')['status']['artifact']['revision']
            def healthy():
                live=get('kustomization',ks['metadata']['name'],'flux-system')
                return live.get('status',{}).get('lastAppliedRevision')==sha and any(c['type']=='Ready' and c['status']=='True' for c in live.get('status',{}).get('conditions',[]))
            wait('Flux '+stage,healthy)
        live=get('kustomization',ks['metadata']['name'],'flux-system')
        (EVIDENCE/('flux-'+stage+'.json')).write_text(json.dumps(live,indent=2)+'\n')
        record('flux-stage',status='verified',stage=stage,inventory=live.get('status',{}).get('inventory',{}))

    def commit(self, docs, stage):
        folder=self.repo/'policies';folder.mkdir(exist_ok=True)
        (folder/'resources.yaml').write_text(yaml.safe_dump_all(docs))
        (folder/'kustomization.yaml').write_text(yaml.safe_dump(dict(apiVersion='kustomize.config.k8s.io/v1beta1',kind='Kustomization',resources=['resources.yaml'] if docs else [])))
        self.publish(stage)
        ks=dict(apiVersion='kustomize.toolkit.fluxcd.io/v1',kind='Kustomization',metadata=dict(name='rehearsal',namespace='flux-system'),spec=dict(interval='10s',retryInterval='5s',timeout='45s',prune=True,path='./policies',sourceRef=dict(kind='GitRepository',name='rehearsal'),wait=False))
        self.reconcile(ks,stage)

    def __exit__(self, *args):
        self.server.terminate()
        self.server.wait(timeout=5)
        self.http_log.close()

