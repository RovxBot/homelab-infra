"""Bounded private reporting of newly observed Kyverno rejection counter changes.

Internal HTTP observations are not authenticated forensic events. No payload,
actor, request body or policy attribution is inferred from counters.
Never send Kubernetes or issue-tracker credentials to a metrics endpoint.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import sqlite3
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPSHandler, ProxyHandler

from publisher import DurablePublisher, NoRedirect, PrivateGitea, marker, revision, require

SERVICE = "kyverno-svc-metrics"
METRIC = "kyverno_admission_requests_total"
LIMIT = 4 * 1024 * 1024
LABEL = re.compile(r'([a-zA-Z_][a-zA-Z_0-9]*)="((?:[^"\\]|\\["\\n])*)"')
SAMPLE = re.compile(r'^' + METRIC + r'\{([^}]*)\}\s+([^\s]+)(?:\s+[0-9]+)?$')
REQUIRED = {"request_allowed", "request_webhook", "resource_kind",
            "resource_namespace", "resource_request_operation"}


def targets(slices):
    require(isinstance(slices, list) and 0 < len(slices) <= 4)
    result = {}
    for obj in slices:
        meta = obj["metadata"]
        require(meta.get("namespace") == "kyverno"
                and meta.get("labels", {}).get("kubernetes.io/service-name") == SERVICE
                and obj.get("addressType") == "IPv4")
        require(obj.get("ports") == [{"name":"metrics-port","port":8000,"protocol":"TCP"}])
        for ep in obj["endpoints"]:
            if ep.get("conditions", {}).get("terminating") is True:
                continue
            require(ep.get("conditions", {}).get("ready") is True
                    and ep.get("conditions", {}).get("serving") is True)
            ref = ep["targetRef"]
            require(ref.get("kind") == "Pod" and ref.get("namespace") == "kyverno"
                    and re.fullmatch(r"kyverno-admission-controller-[a-z0-9]+-[a-z0-9]+", ref.get("name",""))
                    and re.fullmatch(r"[a-f0-9-]{36}", ref.get("uid","")))
            require(len(ep["addresses"]) == 1)
            address = ipaddress.IPv4Address(ep["addresses"][0])
            require(address in ipaddress.IPv4Network("10.0.0.0/8"))
            require(ref["uid"] not in result and str(address) not in result.values())
            result[ref["uid"]] = str(address)
    require(len(result) == 2)
    return result


def parse_labels(raw):
    labels = {}
    offset = 0
    while offset < len(raw):
        match = LABEL.match(raw, offset)
        require(match is not None)
        name, value = match.groups()
        require(name not in labels and len(value) <= 512)
        labels[name] = value
        offset = match.end()
        if offset < len(raw):
            require(raw[offset] == ",")
            offset += 1
    return labels


def sample(text, include_series=False):
    require(isinstance(text, str) and len(text) <= LIMIT)
    seen = set()
    counters = {}
    allowed = denied = 0
    for line in text.splitlines():
        if not line.startswith(METRIC + "{"):
            continue
        m = SAMPLE.fullmatch(line)
        require(m is not None)
        labels = parse_labels(m.group(1))
        require(REQUIRED <= labels.keys()
                and labels["request_allowed"] in {"true","false"}
                and labels["request_webhook"] in {"ValidatingWebhookConfiguration","MutatingWebhookConfiguration"}
                and labels["resource_request_operation"] in {"create","update","delete","connect"})
        key = tuple(sorted(labels.items()))
        require(key not in seen and len(seen) < 5000)
        seen.add(key)
        try:
            value = Decimal(m.group(2))
            require(value.is_finite() and 0 <= value <= 2**53 and value == value.to_integral_value())
        except (InvalidOperation, ArithmeticError):
            raise ValueError("counter_invalid") from None
        counters[hashlib.sha256(json.dumps(key,separators=(",",":")).encode()).hexdigest()] = [labels["request_allowed"] == "false", int(value)]
        if labels["request_allowed"] == "false":
            denied += int(value)
        else:
            allowed += int(value)
    require(bool(seen))
    result = {"series":len(seen),"allowed":allowed,"rejected":denied}
    if include_series:
        result["counters"] = counters
    return result


def kube_list(resource, query):
    root = Path("/var/run/secrets/kubernetes.io/serviceaccount")
    token = (root/"token").read_text().strip()
    opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(
        context=ssl.create_default_context(cafile=str(root/"ca.crt"))))
    require(resource in {"/apis/discovery.k8s.io/v1/namespaces/kyverno/endpointslices", "/api/v1/namespaces/kyverno/pods"})
    request = Request("https://kubernetes.default.svc"+resource+"?"+urlencode(query),
                      headers={"Authorization":"Bearer "+token})
    with opener.open(request, timeout=10) as response:
        body = response.read(256*1024+1)
        require(len(body) <= 256*1024)
        obj = json.loads(body)
    require(not obj.get("metadata",{}).get("continue"))
    require(isinstance(obj["items"],list) and len(obj["items"]) <= 10)
    return obj["items"]


def discover():
    endpoints = targets(kube_list("/apis/discovery.k8s.io/v1/namespaces/kyverno/endpointslices",
        {"labelSelector":"kubernetes.io/service-name="+SERVICE,"limit":10}))
    pods = kube_list("/api/v1/namespaces/kyverno/pods",
        {"labelSelector":"app.kubernetes.io/component=admission-controller,app.kubernetes.io/instance=kyverno","limit":10})
    result = {}
    for pod in pods:
        meta = pod["metadata"]
        uid = meta["uid"]
        if uid not in endpoints:
            continue
        status = pod["status"]
        require(meta["namespace"] == "kyverno" and not meta.get("deletionTimestamp")
                and status["phase"] == "Running" and status["podIP"] == endpoints[uid])
        containers = status["containerStatuses"]
        require(len(containers) == 1)
        container = containers[0]
        require(container["ready"] is True and type(container["restartCount"]) is int
                and isinstance(container["containerID"],str) and container["containerID"].startswith("containerd://"))
        started = container["state"]["running"]["startedAt"]
        require(isinstance(started,str) and len(started) <= 40)
        identity = hashlib.sha256(json.dumps([uid,container["containerID"],container["restartCount"],started],
                                               separators=(",",":")).encode()).hexdigest()
        require(identity not in result)
        result[identity] = endpoints[uid]
    require(len(result) == 2)
    return result


def scrape(address):
    # No bearer token, no proxy and no redirect on internal HTTP metrics.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    request = Request("http://"+str(ipaddress.IPv4Address(address))+":8000/metrics",
                      headers={"Accept":"text/plain","Accept-Encoding":"identity"})
    with opener.open(request, timeout=10) as response:
        require(response.status == 200 and response.headers.get("Content-Encoding","identity") == "identity")
        body = response.read(LIMIT+1)
    require(len(body) <= LIMIT)
    return sample(body.decode("utf-8"), include_series=True)


MAX_GAP = 25 * 60
MAX_PENDING_DAYS = 7


def delta(previous, current):
    """Only compare continuous, monotonic series in the same running container."""
    if previous is None or set(previous) != set(current):
        return None
    total = 0
    for identity, counters in current.items():
        old = previous[identity]
        # Missing series or a decrease invalidates the interval. A newly
        # exposed series is not assumed to have started at zero.
        if set(old) != set(counters):
            return None
        for key, value in counters.items():
            was_denied, count = old[key]
            denied, new = value
            if was_denied != denied or new < count:
                return None
            if denied:
                total += new - count
    return total


def observe(db, current, now):
    db.execute("CREATE TABLE IF NOT EXISTS baseline (id INTEGER PRIMARY KEY CHECK(id=1), at INTEGER NOT NULL, data TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS daily (day TEXT PRIMARY KEY, count INTEGER NOT NULL, last INTEGER NOT NULL, sent INTEGER NOT NULL DEFAULT 0, published_at INTEGER NOT NULL DEFAULT 0)")
    old = db.execute("SELECT at,data FROM baseline WHERE id=1").fetchone()
    change = None
    if old:
        require(now > old[0])
        if now-old[0] <= MAX_GAP:
            change = delta(json.loads(old[1]),current)
    with db:
        db.execute("INSERT OR REPLACE INTO baseline VALUES (1,?,?)",(now,json.dumps(current,separators=(",",":"))))
        if change:
            day = datetime.fromtimestamp(now,timezone.utc).date().isoformat()
            db.execute("INSERT INTO daily(day,count,last) VALUES (?,?,?) ON CONFLICT(day) DO UPDATE SET count=count+excluded.count,last=excluded.last",(day,change,now))
        # A gap or restart establishes a new baseline, not a clean observation.
        # Unsent data is never silently discarded.
        db.execute("DELETE FROM daily WHERE day < ? AND sent=last",
                   (datetime.fromtimestamp(now-7*86400,timezone.utc).date().isoformat(),))
        require(db.execute("SELECT count(*) FROM daily").fetchone()[0] <= MAX_PENDING_DAYS+2)
    return change


def render_admission(item):
    return ("Kyverno admission rejections — "+item["day"],
            marker(item["key"])+"\n\n"+
            "Observed rejected request counter increases (UTC observation day): "+str(item["count"])+"\n"+
            "These are aggregate webhook observations, not unique attacks or forensic events. "+
            "Inspect authorized local audit evidence and deployment activity if investigation is warranted. "+
            "Counter resets, replica replacements, missing series and sampling gaps cannot establish absence of rejection. "+
            "Never infer a policy bypass or automatically close on quiet counters.")


def publish(db, client, now):
    writer = DurablePublisher(db,client,now=now,renderer=render_admission,fingerprint=revision)
    rows = db.execute("SELECT day,count,last,sent,published_at FROM daily WHERE sent!=last ORDER BY day").fetchall()
    outcomes=[]
    for day,count,last,sent,published_at in rows:
        # First positive daily observation creates one issue; aggregate updates
        # at most hourly, with a final update after the observation day ends.
        if published_at and now-published_at < 3600:
            continue
        item={"key":hashlib.sha256(("kyverno-admission-day:"+day).encode()).hexdigest(),
              "day":day,"count":count}
        outcome=writer.sync(item)
        outcomes.append(outcome)
        if outcome in {"created","updated","unchanged","human_closed"}:
            with db:
                db.execute("UPDATE daily SET sent=last,published_at=? WHERE day=?",(now,day))
    pending=db.execute("SELECT count(*) FROM issues WHERE pending_create!=0 OR pending_update IS NOT NULL").fetchone()[0]
    return outcomes,pending


def main():
    phase = "discover"
    fd = db = None
    try:
        before = discover()
        phase = "sample"
        rows = {identity:scrape(address) for identity,address in before.items()}
        phase = "recheck"
        require(before == discover())
        phase = "state"
        path="/state/gitea-admission-observer.sqlite"
        fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        db=sqlite3.connect(path,timeout=5)
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=FULL")
        require(db.execute("PRAGMA quick_check").fetchone()[0]=="ok")
        size=db.execute("PRAGMA page_size").fetchone()[0]
        require(db.execute(f"PRAGMA max_page_count={16*1024**2//size}").fetchone()[0] <= 16*1024**2//size)
        now=int(datetime.now(timezone.utc).timestamp())
        change=observe(db,{identity:row["counters"] for identity,row in rows.items()},now)
        phase="destination"
        outcomes,pending=publish(db,PrivateGitea(os.environ.get("GITEA_TOKEN","")),now)
        print(json.dumps({"state":"observed" if change is not None else "baseline_only",
                          "replicas":len(rows),"intervalQualified":change is not None,
                          "created":outcomes.count("created"),"updated":outcomes.count("updated"),
                          "pending":pending}),flush=True)
        return int(bool(pending) or change is None)
    except Exception:
        print(json.dumps({"state":"unknown","phase":phase}),flush=True)
        return 1
    finally:
        if db is not None:
            db.close()
        if fd is not None:
            os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
