#!/usr/bin/env python3
"""Collect one bounded, read-only Kyverno/Flux snapshot; emit allowlisted JSON only."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import time
from urllib.parse import urlencode


UTC = timezone.utc
MAX_REPORT_BYTES = 49152
MAX_PODS = 8
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
IDENTIFIERS = {
    "admission_uid": ("admissionReviewUID", "requestUID", "requestUid"),
    "request_id": ("requestID", "requestId", "request_id"),
    "audit_id": ("auditID", "auditId"),
}


def stamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


def name(value):
    """Never echo free text, control characters, URLs, or oversized field values."""
    return value if isinstance(value, str) and NAME.fullmatch(value) else "unavailable"


def alias(prefix, value):
    if not value:
        return "unavailable"
    return prefix + "-" + hashlib.sha256(str(value).encode()).hexdigest()[:12]


def identity(obj):
    return {k: name(obj.get(k)) for k in ("kind", "namespace", "name")}


def identifiers(text):
    result = {}
    for category, keys in IDENTIFIERS.items():
        for key in keys:
            match = re.search(r'(?<![\w.])"?' + key + r'"?\s*[:=]\s*"?(' + UUID + r')(?![\w-])', text)
            if match:
                result[category] = alias("id", match[1].lower())
                break
    return result


def latency(text):
    # An unlabelled number is not a duration. These values describe only the log
    # record's operation, never API-server latency or a particular admission hop.
    match = re.search(r'\b(duration_ms|latency_ms)"?\s*[:=]\s*"?(\d+(?:\.\d+)?)(?![\w.])', text)
    if match:
        value = float(match[2])
        return {"milliseconds": value} if value <= 3600000 else None
    match = re.search(r'\b(?:duration|latency|elapsed)"?\s*[:=]\s*"?(\d+(?:\.\d+)?)(ns|us|µs|ms|s)(?!\w)', text)
    if match:
        value = float(match[1]) * {"ns": .000001, "us": .001, "µs": .001, "ms": 1, "s": 1000}[match[2]]
        return {"milliseconds": value} if value <= 3600000 else None
    return None


def timed_out(text):
    return bool(re.search(r"context deadline exceeded|i/o timeout|Client\.Timeout|timed out|timeout awaiting", text, re.I))


def timeout_rows(message, when, source, kustomization, fallback=None):
    rows = []
    # Keep each resource's message separate: a webhook named on a different line
    # must not be attributed to this line's timeout.
    for line in message.splitlines()[:200]:
        if not timed_out(line):
            continue
        hooks = re.findall(r'(?:calling webhook|webhook)\s+["\']([\w.-]{1,128})["\']', line)
        hooks = sorted(set(hooks))
        # Multiple webhook errors on one line have an ambiguous timeout owner.
        if len(hooks) != 1 or not re.search(r"\.kyverno\.svc(?:-fail)?$", hooks[0]):
            continue
        resources = list(re.finditer(r'(?:^|\s)([A-Z][A-Za-z0-9.]{0,80})/([\w.-]+)(?:/([\w.-]+))?(?=\s|:|$)', line))
        resource = resources[0] if len(resources) == 1 else None
        target = identity(fallback or {})
        if resource:
            target = identity({"kind": resource[1], "namespace": resource[2] if resource[3] else None,
                               "name": resource[3] or resource[2]})
        deadline = re.search(r'[?&]timeout=(\d{1,3})s\b', line)
        for hook in hooks:
            rows.append({"at": iso(when), "source": source, "webhook": hook,
                         "kustomization": kustomization, "resource": target,
                         "configured_deadline_seconds": int(deadline[1]) if deadline else None,
                         "identifiers": identifiers(line)})
    return rows


def flux_evidence(events, kustomizations, start, end):
    rows = []
    for event in events:
        reporter = event.get("reportingComponent") or event.get("source", {}).get("component", "")
        if reporter != "kustomize-controller":
            continue
        ref = event.get("involvedObject", {})
        if ref.get("kind") != "Kustomization":
            continue
        when = stamp(event.get("series", {}).get("lastObservedTime") or event.get("lastTimestamp")
                     or event.get("eventTime") or event.get("metadata", {}).get("creationTimestamp"))
        if when and start <= when <= end:
            rows.extend(timeout_rows(event.get("message", ""), when, "event:last-observed",
                                     identity(ref)))
    for obj in kustomizations:
        meta = obj.get("metadata", {})
        ref = identity({"kind": "Kustomization", **{k: meta.get(k) for k in ("namespace", "name")}})
        for condition in obj.get("status", {}).get("conditions", []):
            when = stamp(condition.get("lastTransitionTime"))
            if when and start <= when <= end:
                rows.extend(timeout_rows(condition.get("message", ""), when,
                                         "condition:last-transition (not request time)", ref))
    return sorted(rows, key=lambda r: r["at"], reverse=True)


def controller_evidence(text, pod, stream, start, end):
    rows = []
    for line in text.splitlines():
        timestamp, _, body = line.partition(" ")
        when = stamp(timestamp)
        if not when or not start <= when <= end:
            continue
        field_text = body
        if body.startswith("{"):
            try:
                record = json.loads(body)
            except ValueError:
                continue
            # Never mine nested request/object bodies for apparent identifiers.
            safe_keys = {"level", "msg", "message", "error", "duration", "latency", "elapsed", "duration_ms", "latency_ms"}
            safe_keys.update(k for keys in IDENTIFIERS.values() for k in keys)
            if not isinstance(record, dict):
                continue
            body = json.dumps({k: v for k, v in record.items() if k in safe_keys and isinstance(v, (str, int, float))})
            field_text = json.dumps({k: v for k, v in record.items() if k in safe_keys - {"level", "msg", "message", "error"}
                                     and isinstance(v, (str, int, float))})
        is_error = bool(re.search(r'\b(?:ERR|ERROR)\b|^E\d{4}|\blevel[" ]*[:=]\s*"?error\b|\berror[" ]*[:=]\s*"?[^"\s]', body))
        ids, duration = identifiers(field_text), latency(field_text)
        if not (is_error or ids or duration):
            continue
        category = "controller-error" if is_error else "request-or-duration"
        for pattern, label in ((r"context deadline exceeded|i/o timeout|timed out", "timeout"),
                               (r"discovery|server API group|requested resource", "api-discovery"),
                               (r"TLS|x509|handshake", "tls"),
                               (r"connection refused|no route to host", "connection"),
                               (r"failed to evaluate|variable substitution|quantity", "policy-evaluation")):
            if re.search(pattern, body, re.I):
                category = label
                break
        rows.append({"at": iso(when), "pod": name(pod), "stream": stream,
                     "category": category, "identifiers": ids, "operation_latency": duration})
    return rows


def correlations(timeouts, logs):
    rows = []
    for event in timeouts:
        when = stamp(event["at"])
        for log in logs:
            delta = (stamp(log["at"]) - when).total_seconds()
            if abs(delta) > 15:
                continue
            shared = sorted(k for k, v in event["identifiers"].items() if log["identifiers"].get(k) == v)
            conflicts = any(log["identifiers"].get(k) not in (None, v) for k, v in event["identifiers"].items())
            if conflicts or (not shared and event["source"].startswith("condition:")):
                continue
            rows.append({"timeout_at": event["at"], "webhook": event["webhook"],
                         "kustomization": event["kustomization"], "pod": log["pod"],
                         "controller_at": log["at"], "offset_seconds": delta,
                         "basis": "explicit identifier overlap" if shared else "time overlap only",
                         "shared_identifier_types": shared,
                         "interpretation": "Does not establish the failing boundary or causation."})
    return rows


class Unavailable(Exception):
    """Only fixed safe reason codes may cross the collection boundary."""


class Reader:
    def __init__(self, context, budget=90, request_timeout=8):
        self.context = context
        self.deadline = time.monotonic() + budget
        self.request_timeout = request_timeout
        self.sources = {}

    def command(self, args, cap):
        remaining = min(self.request_timeout + 2, self.deadline - time.monotonic())
        if remaining <= 0:
            raise Unavailable("snapshot-budget-exhausted")
        argv = ["kubectl", "--context", self.context, f"--request-timeout={self.request_timeout}s", *args]
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except OSError:
            raise Unavailable("kubectl-unavailable") from None
        output, errors = bytearray(), bytearray()
        deadline = time.monotonic() + remaining
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ, output)
                selector.register(proc.stderr, selectors.EVENT_READ, errors)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise Unavailable("request-time-budget-exhausted")
                    for key, _ in selector.select(min(remaining, .1)):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            key.data.extend(chunk)
                            if len(output) + len(errors) > cap:
                                raise Unavailable("response-byte-limit-exceeded")
                proc.wait(timeout=max(.01, deadline - time.monotonic()))
            if proc.returncode:
                # Never reproduce stderr, which may include credential-plugin data.
                reason = "forbidden" if b"Forbidden" in errors else "request-failed"
                raise Unavailable(reason)
            return output.decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            raise Unavailable("request-time-budget-exhausted") from None
        finally:
            # Also terminate credential helper descendants on timeout/overflow.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            proc.stdout.close()
            proc.stderr.close()

    def read(self, key, args, cap=2097152, json_data=True):
        at = iso(datetime.now(UTC))
        try:
            raw = self.command(args, cap)
            value = json.loads(raw) if json_data else raw
            if json_data and not isinstance(value, dict):
                raise ValueError()
            self.sources[key] = {"status": "verified", "at": at}
            return value
        except (Unavailable, ValueError) as exc:
            self.sources[key] = {"status": "unavailable", "at": at,
                                 "reason": str(exc) if isinstance(exc, Unavailable) else "invalid-json"}
            return {} if json_data else ""

    def listing(self, key, path, query=None):
        items, query = [], dict(query or {})
        query["limit"] = 200
        for page in range(3):
            page_key = f"{key}:page{page + 1}"
            obj = self.read(page_key, ["get", "--raw", path + "?" + urlencode(query)])
            page_items = obj.get("items", [])
            items.extend(page_items[:200])
            continuation = obj.get("metadata", {}).get("continue")
            if self.sources[page_key]["status"] != "verified" or not continuation:
                break
            query["continue"] = continuation
        self.sources[key] = {"status": "verified" if self.sources[page_key]["status"] == "verified" else "unavailable",
                             "items": len(items), "at": self.sources[page_key]["at"],
                             "truncated": bool(continuation) or len(page_items) > 200}
        return items


def pod_rows(pods):
    result = []
    for pod in pods[:MAX_PODS]:
        status, meta = pod.get("status", {}), pod.get("metadata", {})
        containers = status.get("containerStatuses", [])
        ready = next((c.get("status") for c in status.get("conditions", []) if c.get("type") == "Ready"), "Unknown")
        result.append({"pod": name(meta.get("name")), "instance": alias("pod", meta.get("uid")),
                       "ready": ready if ready in ("True", "False", "Unknown") else "Unknown",
                       "containers_ready": sum(c.get("ready") is True for c in containers),
                       "containers_observed": len(containers),
                       "restarts": sum(c["restartCount"] for c in containers)
                       if containers and all(isinstance(c.get("restartCount"), int) for c in containers) else None,
                       "node": alias("node", pod.get("spec", {}).get("nodeName"))})
    return result


def endpoint_rows(slices):
    result = []
    for obj in slices:
        for endpoint in obj.get("endpoints", []):
            conditions, target = endpoint.get("conditions", {}), endpoint.get("targetRef", {})
            result.append({"slice": name(obj.get("metadata", {}).get("name")),
                           "endpoint": alias("endpoint", json.dumps([target.get("uid"), endpoint.get("addresses")], sort_keys=True)),
                           "pod": name(target.get("name")), "instance": alias("pod", target.get("uid")),
                           "node": alias("node", endpoint.get("nodeName")),
                           **{k: conditions.get(k) if isinstance(conditions.get(k), bool) else None
                              for k in ("ready", "serving", "terminating")},
                           "ports": [p.get("port") for p in obj.get("ports", []) if isinstance(p.get("port"), int)][:8]})
    return result


def metric_rows(items, pods):
    allowed = {p.get("metadata", {}).get("name"): p.get("metadata", {}).get("creationTimestamp") for p in pods}
    result = []
    for obj in items:
        pod = obj.get("metadata", {}).get("name")
        when = stamp(obj.get("timestamp"))
        created = stamp(allowed.get(pod))
        if pod not in allowed or not when or (created and when < created):
            continue
        usage = []
        for container in obj.get("containers", [])[:4]:
            quantities = {}
            for key in ("cpu", "memory"):
                value = str(container.get("usage", {}).get(key, ""))
                if re.fullmatch(r"\d+(?:\.\d+)?(?:n|u|m|Ki|Mi|Gi|Ti|k|M|G)?", value):
                    quantities[key] = value
            usage.append({"container": name(container.get("name")), **quantities})
        result.append({"pod": name(pod), "at": iso(when), "usage": usage})
    return result


def collect(reader, since_minutes=30, namespace="kyverno", flux_namespace="flux-system", service="kyverno-svc", max_items=20):
    began = datetime.now(UTC)
    start = began - timedelta(minutes=since_minutes)
    selector = "app.kubernetes.io/component=admission-controller"
    pod_path = f"/api/v1/namespaces/{namespace}/pods"
    slice_path = f"/apis/discovery.k8s.io/v1/namespaces/{namespace}/endpointslices"
    slice_query = {"labelSelector": f"kubernetes.io/service-name={service}"}
    endpoints_before = endpoint_rows(reader.listing("endpoints-start", slice_path, slice_query))
    pods = reader.listing("pods", pod_path, {"labelSelector": selector})
    svc = reader.read("service", ["get", "--raw", f"/api/v1/namespaces/{namespace}/services/{service}"])
    events = reader.listing("flux-events", f"/api/v1/namespaces/{flux_namespace}/events",
                            {"fieldSelector": "involvedObject.kind=Kustomization"})
    ks = reader.listing("kustomizations", f"/apis/kustomize.toolkit.fluxcd.io/v1/namespaces/{flux_namespace}/kustomizations")
    metrics = reader.listing("pod-metrics", f"/apis/metrics.k8s.io/v1beta1/namespaces/{namespace}/pods",
                             {"labelSelector": selector})
    logs = []
    for pod in pods[:MAX_PODS]:
        pod_name = pod.get("metadata", {}).get("name", "")
        containers = pod.get("spec", {}).get("containers", [])
        container = next((c["name"] for c in containers if c.get("name") == "kyverno"), None)
        if not container or name(pod_name) == "unavailable":
            reader.sources[f"logs:{name(pod_name)}"] = {"status": "unavailable", "reason": "expected-kyverno-container-absent"}
            continue
        restarted = any(c.get("name") == container and c.get("restartCount", 0) > 0
                        for c in pod.get("status", {}).get("containerStatuses", []))
        for stream in (["current", "previous"] if restarted else ["current"]):
            args = ["-n", namespace, "logs", pod_name, "-c", container, "--timestamps=true",
                    f"--since-time={iso(start)}", "--tail=1000", "--limit-bytes=131072"]
            if stream == "previous":
                args.append("--previous=true")
            key = f"logs:{name(pod_name)}:{stream}"
            raw = reader.read(key, args, cap=147456, json_data=False)
            # Ignore a potentially partial first line after --limit-bytes truncation.
            logs.extend(controller_evidence(raw, pod_name, stream, start, datetime.now(UTC)))
    endpoints_after = endpoint_rows(reader.listing("endpoints-end", slice_path, slice_query))
    ended = datetime.now(UTC)
    timeouts = flux_evidence(events, ks, start, ended)
    logs.sort(key=lambda r: r["at"], reverse=True)
    possible = correlations(timeouts[:max_items], logs[:max_items])
    spec = svc.get("spec", {})
    ports = [{"port": p.get("port"), "target_port": p.get("targetPort") if isinstance(p.get("targetPort"), int)
              else name(p.get("targetPort")), "protocol": name(p.get("protocol"))} for p in spec.get("ports", [])[:8]]
    selected = [p for p in pods if spec.get("selector") and all(p.get("metadata", {}).get("labels", {}).get(k) == v
                                                              for k, v in spec["selector"].items())]
    endpoints_known = all(reader.sources[k]["status"] == "verified" and not reader.sources[k].get("truncated")
                          for k in ("endpoints-start", "endpoints-end"))
    limitations = [
        "API-server audit/trace data unavailable: no approved source is configured; not collected.",
        "Cilium flow evidence unavailable: no approved source is configured; not collected.",
        "Ready Service/endpoints do not prove API-server-to-webhook connectivity.",
        "Events are aggregated/retained briefly; condition transition times are not request timestamps.",
        "Current pod placement/endpoints/metrics do not establish their state at an earlier timeout.",
        "Logs are current/previous only, capped at 1000 lines and 128 KiB per stream; rotation, deletion, verbosity and permissions can hide requests.",
        "No corresponding request in this sample does not prove that Kyverno did not receive it.",
        "Request identifiers require explicit UUID fields; generic uid and unlabelled latency are excluded.",
        "Node addresses and request identifiers are hashed; free text, raw logs, bodies and complete objects are omitted.",
    ]
    if not any(r["identifiers"] for r in logs + timeouts):
        limitations.append("Request identifiers unavailable in sampled safe fields.")
    if not any(r["operation_latency"] for r in logs):
        limitations.append("Request/operation latency unavailable in sampled safe fields; configured deadlines are not measured latency.")
    metrics_rows = metric_rows(metrics, pods)
    if not metrics_rows:
        limitations.append("Pod resource usage unavailable from sampled metrics; no values substituted.")
    if len(pods) > MAX_PODS:
        limitations.append("Pod/log sample truncated to eight admission replicas.")
    report = {
        "schema": "kyverno-timeout-evidence/v1", "snapshot": {"started_at": iso(began), "ended_at": iso(ended),
            "window_start": iso(start), "context": alias("context", reader.context), "namespace": name(namespace),
            "flux_namespace": name(flux_namespace)},
        "confirmed_observations": {
            "flux_timeouts": timeouts[:max_items], "pods": pod_rows(pods), "pod_usage": metrics_rows[:MAX_PODS],
            "service": {"name": name(service), "type": name(spec.get("type")), "ports": ports,
                        "selected_observed_pods": len(selected) if spec else None},
            "endpoints_start": endpoints_before[:max_items], "endpoints_end": endpoints_after[:max_items],
            "endpoints_changed_during_collection": (sorted(json.dumps(r, sort_keys=True) for r in endpoints_before)
                != sorted(json.dumps(r, sort_keys=True) for r in endpoints_after)) if endpoints_known else None,
            "controller_evidence": logs[:max_items]},
        "possible_correlations": possible[:max_items],
        "evidence_unavailable_or_limited": limitations,
        "sources": reader.sources,
        "counts_before_report_limits": {"flux_timeouts": len(timeouts), "controller_evidence": len(logs),
            "possible_correlations": len(possible), "endpoints_start": len(endpoints_before), "endpoints_end": len(endpoints_after)},
    }
    return report


def encode_report(report):
    # A hard byte bound preserves every section and source status. Report omitted
    # row counts remain visible; no raw data is written to temporary files.
    report["report_rows_trimmed"] = False
    while True:
        encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if len(encoded.encode()) <= MAX_REPORT_BYTES:
            return encoded
        arrays = [v for v in report["confirmed_observations"].values() if isinstance(v, list)]
        arrays.append(report["possible_correlations"])
        largest = max(arrays, key=len)
        if not largest:
            raise Unavailable("report-byte-limit-exceeded")
        largest.pop()
        report["report_rows_trimmed"] = True


def bounded_int(low, high):
    def parse(value):
        result = int(value)
        if not low <= result <= high:
            raise argparse.ArgumentTypeError(f"must be between {low} and {high}")
        return result
    return parse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, help="Explicit existing kubectl context; no context switching")
    parser.add_argument("--since-minutes", type=bounded_int(1, 120), default=30)
    parser.add_argument("--budget-seconds", type=bounded_int(10, 180), default=90)
    parser.add_argument("--max-items", type=bounded_int(1, 40), default=20, help="Maximum rows per evidence section")
    parser.add_argument("--namespace", default="kyverno")
    parser.add_argument("--flux-namespace", default="flux-system")
    parser.add_argument("--service", default="kyverno-svc")
    args = parser.parse_args()
    for value in (args.namespace, args.flux_namespace, args.service):
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
            parser.error("namespace and service must be DNS labels")
    reader = Reader(args.context, args.budget_seconds)
    try:
        report = collect(reader, args.since_minutes, args.namespace, args.flux_namespace, args.service, args.max_items)
        sys.stdout.write(encode_report(report))
    except (BrokenPipeError, KeyboardInterrupt):
        return 130
    except Exception:
        # No traceback: malformed/untrusted responses must never leak raw data.
        sys.stderr.write("Snapshot failed; details suppressed to protect source data.\n")
        return 1
    return 2 if any(s["status"] == "unavailable" or s.get("truncated") for s in reader.sources.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
