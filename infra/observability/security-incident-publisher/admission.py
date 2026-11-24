"""Read-only qualification of Kyverno admission rejection counters.

These metrics are internal HTTP observations, not authenticated forensic events.
Do not publish issue evidence until both replicas and metric schema are qualified.
Never send Kubernetes or issue-tracker credentials to a metrics endpoint.
"""
from decimal import Decimal, InvalidOperation
import ipaddress
import json
from pathlib import Path
import re
import ssl
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPSHandler, ProxyHandler

from publisher import NoRedirect, require

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


def sample(text):
    require(isinstance(text, str) and len(text) <= LIMIT)
    seen = set()
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
        if labels["request_allowed"] == "false":
            denied += int(value)
        else:
            allowed += int(value)
    require(bool(seen))
    return {"series":len(seen),"allowed":allowed,"rejected":denied}


def discover():
    root = Path("/var/run/secrets/kubernetes.io/serviceaccount")
    token = (root/"token").read_text().strip()
    opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(
        context=ssl.create_default_context(cafile=str(root/"ca.crt"))))
    query = urlencode({"labelSelector":"kubernetes.io/service-name="+SERVICE,"limit":10})
    request = Request("https://kubernetes.default.svc/apis/discovery.k8s.io/v1/namespaces/kyverno/endpointslices?"+query,
                      headers={"Authorization":"Bearer "+token})
    with opener.open(request, timeout=10) as response:
        body = response.read(256*1024+1)
        require(len(body) <= 256*1024)
        obj = json.loads(body)
    require(not obj.get("metadata",{}).get("continue"))
    return targets(obj["items"])


def scrape(address):
    # No bearer token, no proxy and no redirect on internal HTTP metrics.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    request = Request("http://"+str(ipaddress.IPv4Address(address))+":8000/metrics",
                      headers={"Accept":"text/plain","Accept-Encoding":"identity"})
    with opener.open(request, timeout=10) as response:
        require(response.status == 200 and response.headers.get("Content-Encoding","identity") == "identity")
        body = response.read(LIMIT+1)
    require(len(body) <= LIMIT)
    return sample(body.decode("utf-8"))


def main():
    phase = "discover"
    try:
        before = discover()
        phase = "sample"
        rows = [scrape(address) for address in before.values()]
        phase = "recheck"
        require(before == discover())
        print(json.dumps({"state":"observed","replicas":len(rows),
                          "series":sum(r["series"] for r in rows),
                          "allowedSinceProcessStart":sum(r["allowed"] for r in rows),
                          "rejectedSinceProcessStart":sum(r["rejected"] for r in rows),
                          "publication":"disabled_pending_qualification"}),flush=True)
        return 0
    except Exception:
        print(json.dumps({"state":"unknown","phase":phase}),flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
