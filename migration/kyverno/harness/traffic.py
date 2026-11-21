"""Positive controls and sampled ingress/egress checks on Calico, not kindnet."""
import json
import threading
import time
from rehearse import EVIDENCE, apply, get, k, namespace, record, wait


class Traffic:
    def __init__(self,ns):
        self.ns=ns;self.control=ns+'-control';self.samples=[];self.done=threading.Event()

    def prepare(self):
        apply(namespace(self.control))
        for ns in [self.ns,self.control]:
            for name in ['server','client']:
                command=['sh','-c','mkdir -p /www; echo ok > /www/index.html; httpd -f -p 8080 -h /www'] if name=='server' else ['sleep','3600']
                apply(dict(apiVersion='v1',kind='Pod',metadata=dict(name=name,namespace=ns),spec=dict(containers=[dict(name=name,image='busybox:1.37.0',command=command)])))
                k('wait','--for=condition=Ready','pod/'+name,'-n',ns,'--timeout=90s',timeout=100)
        self.protected_ip=get('pod','server',self.ns)['status']['podIP']
        self.control_ip=get('pod','server',self.control)['status']['podIP']
        assert all(self.probe().values()),'Both flows must work before default-deny'
        record('traffic-positive-controls',status='verified',namespace=self.ns,cni='Calico v3.31.0')

    def probe(self):
        results={}
        for label,src,ip in [('ingress',self.control,self.protected_ip),('egress',self.ns,self.control_ip),('control',self.control,self.control_ip)]:
            p=k('exec','-n',src,'client','--','sh','-c',f'if wget -q -T 2 -O /dev/null http://{ip}:8080; then echo ALLOWED; else echo DENIED; fi',check=False,timeout=15)
            if p.returncode or p.stdout.strip() not in ['ALLOWED','DENIED']:
                raise RuntimeError('Traffic probe unavailable: '+p.stderr)
            results[label]=p.stdout.strip()=='ALLOWED'
        return results

    def start(self):
        wait('CNI enforces ingress AND egress',lambda:self.probe()==dict(ingress=False,egress=False,control=True))
        def watch():
            while not self.done.is_set():
                try: row=dict(observed_at=time.time(),**self.probe())
                except Exception as error: row=dict(observed_at=time.time(),error=str(error))
                self.samples.append(row)
                (EVIDENCE/(self.ns+'-traffic.json')).write_text(json.dumps(self.samples,indent=2)+'\n')
        self.thread=threading.Thread(target=watch,daemon=True);self.thread.start()

    def stop(self):
        self.done.set();self.thread.join(timeout=50)
        assert not self.thread.is_alive()
        good=bool(self.samples) and all(s.get('control') is True and s.get('ingress') is False and s.get('egress') is False for s in self.samples)
        record('traffic-upgrade-restart',status='verified' if good else 'failed',namespace=self.ns,samples=len(self.samples),
               scope='sampled TCP ingress/egress denial with a working positive control, across upgrade/restart')
