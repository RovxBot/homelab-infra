#!/usr/bin/env python3
"""Collect one bounded, read-only Kyverno/Flux snapshot; emit allowlisted JSON only."""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
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
# Kubernetes v1.34.1 admission/metrics/metrics.go; no suffix or substring matching.
WEBHOOKS = {"validate-policy.kyverno.svc", "validate.kyverno.svc-fail"}
HISTOGRAM = "apiserver_admission_webhook_admission_duration_seconds"
REJECTIONS = "apiserver_admission_webhook_rejection_count"
REQUESTS = "apiserver_admission_webhook_request_total"
METRIC_LABELS = {
    HISTOGRAM + "_bucket": {"name", "type", "operation", "rejected", "le"},
    HISTOGRAM + "_count": {"name", "type", "operation", "rejected"},
    REJECTIONS: {"name", "type", "operation", "error_type", "rejection_code"},
    REQUESTS: {"name", "type", "operation", "code", "rejected"},
}
# These local configuration-controller metrics carry the serving API server ID.
# Inspect only its hash; never retain configuration hashes, statuses or counters.
IDENTITY_METRICS = {
    f"apiserver_{controller}_config_controller_{suffix}"
    for controller in ("authentication", "authorization", "encryption")
    for suffix in ("automatic_reloads_total", "automatic_reload_last_timestamp_seconds", "last_config_info")
}
BUCKETS = {"0.005", "0.025", "0.1", "0.5", "1", "2.5", "10", "25", "+Inf"}
METRICS_CAP = 2097152
# Only /metrics gets a larger *input* budget. Discarded lines count too; the
# retained buffer stays at 2 MiB. Allow one extra byte solely to detect overflow.
METRICS_INPUT_CAP = 32 * 1024 * 1024
MAX_METRIC_LINE_BYTES = 16384
MAX_RETAINED_METRIC_RECORDS = 4096
METRICS_STDERR_CAP = 16384
MAX_METRIC_ROWS = 2048
PROM_NAME = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
PROM_NAME_BYTES = re.compile(rb"[a-zA-Z_:][a-zA-Z0-9_:]*")
PROM_LABEL = re.compile(r'\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"((?:[^"\\\n]|\\[\\"n])*)"\s*')
PROM_VALUE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")


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


class MetricsStream:
    """Bound all input work and retain only exact allowlisted metric records.

    Buffers are bounded by 64 KiB transport chunks, a 16 KiB pending line,
    2 MiB of encoded retained records and at most 4096 record objects. No raw
    output is persisted. Neither a target sample nor EOF alone means success.
    """
    def __init__(self):
        self.input_bytes = 0
        self.retained_bytes = 0
        self.pending = bytearray()
        self.records = []

    def provenance(self, complete=False):
        return {"input_bytes_processed": self.input_bytes,
                "retained_bytes": self.retained_bytes,
                "retained_metric_records": len(self.records),
                "collection_complete": complete}

    def feed(self, chunk):
        self.input_bytes += len(chunk)
        if self.input_bytes > METRICS_INPUT_CAP:
            raise Unavailable("metrics-input-byte-limit-exceeded")
        parts = chunk.split(b"\n")
        for index, part in enumerate(parts):
            if len(self.pending) + len(part) > MAX_METRIC_LINE_BYTES:
                raise Unavailable("metrics-line-byte-limit-exceeded")
            self.pending.extend(part)
            if index == len(parts) - 1:
                break
            token = PROM_NAME_BYTES.match(self.pending)
            metric = token[0].decode("ascii") if token else None
            if metric in METRIC_LABELS or metric in IDENTITY_METRICS or metric == "process_start_time_seconds":
                size = len(self.pending) + 1
                if self.retained_bytes + size > METRICS_CAP:
                    raise Unavailable("metrics-retained-byte-limit-exceeded")
                if len(self.records) >= MAX_RETAINED_METRIC_RECORDS:
                    raise Unavailable("metrics-record-limit-exceeded")
                try:
                    line = self.pending.decode("utf-8")
                except UnicodeDecodeError:
                    raise Unavailable("invalid-metric-encoding") from None
                self.records.append(line)
                self.retained_bytes += size
            self.pending.clear()

    def finish(self):
        # Text exposition requires a terminating LF, even on discarded lines.
        if self.pending:
            raise Unavailable("metrics-truncated-final-line")
        return self.records


def prometheus_sample(line):
    """Parse one text-format sample, including only the three legal escapes.

    Delimiters inside quoted labels are data, never additional labels. Reject
    duplicates, exemplars, trailing garbage and partial parses rather than guess.
    """
    match = PROM_NAME.match(line)
    if not match or len(line) > 16384:
        raise ValueError()
    metric, pos, labels = match[0], match.end(), {}
    if line[pos:pos + 1] == "{":
        pos += 1
        while line[pos:pos + 1] != "}":
            label = PROM_LABEL.match(line, pos)
            if not label or label[1] in labels or len(labels) >= 16 or len(label[2]) > 512:
                raise ValueError()
            labels[label[1]] = re.sub(r'\\([\\"n])', lambda m: {"n": "\n", '"': '"', "\\": "\\"}[m[1]], label[2])
            pos = label.end()
            if line[pos:pos + 1] == ",":
                pos += 1
            elif line[pos:pos + 1] != "}":
                raise ValueError()
        pos += 1
    tail = re.fullmatch(r"[ \t]+(\S+)(?:[ \t]+(-?\d+))?[ \t]*", line[pos:])
    if not tail or not PROM_VALUE.fullmatch(tail[1]):
        raise ValueError()
    try:
        value = Decimal(tail[1])
    except InvalidOperation:
        raise ValueError() from None
    if not value.is_finite() or value < 0:
        raise ValueError()
    sample_at = None
    if tail[2] is not None:
        milliseconds = int(tail[2])
        if not 0 <= milliseconds <= 4102444800000:
            raise ValueError()
        sample_at = iso(datetime.fromtimestamp(milliseconds / 1000, UTC))
    return metric, labels, value, sample_at


def admission_metrics(raw):
    rows, seen, hashes, identity_metrics, process_starts = [], set(), set(), set(), []
    exposed = set()
    invalid, identity_invalid, overflow = 0, False, False
    for line in raw.splitlines() if isinstance(raw, str) else raw:
        token = PROM_NAME.match(line)
        metric = token[0] if token else None
        is_identity = metric in IDENTITY_METRICS or metric == "process_start_time_seconds"
        if metric not in METRIC_LABELS and not is_identity:
            continue
        if metric in METRIC_LABELS:
            exposed.add(HISTOGRAM if metric.startswith(HISTOGRAM + "_") else metric)
        try:
            metric, labels, value, sample_at = prometheus_sample(line)
            if is_identity:
                if metric == "process_start_time_seconds":
                    if labels or not 0 < value <= 4102444800:
                        raise ValueError()
                    process_starts.append(float(value))
                else:
                    expected = {"apiserver_id_hash", "hash"} if metric.endswith("last_config_info") else {"apiserver_id_hash", "status"}
                    server_hash = labels.get("apiserver_id_hash", "")
                    if set(labels) != expected or not re.fullmatch(r"sha256:[0-9a-f]{64}", server_hash):
                        raise ValueError()
                    key = (metric, tuple(sorted(labels.items())))
                    if key in seen:
                        raise ValueError()
                    seen.add(key)
                    hashes.add(server_hash)
                    identity_metrics.add(metric)
                continue
            if labels.get("name") not in WEBHOOKS:
                continue
            if set(labels) != METRIC_LABELS[metric]:
                raise ValueError()
            if labels["operation"] not in {"CREATE", "UPDATE", "DELETE", "CONNECT"}:
                raise ValueError()
            if labels["type"] not in {"admit", "validate", "validating"}:
                raise ValueError()
            if "rejected" in labels and labels["rejected"] not in {"true", "false"}:
                raise ValueError()
            if "error_type" in labels and labels["error_type"] not in {"calling_webhook_error", "apiserver_internal_error", "no_error"}:
                raise ValueError()
            for key in ("code", "rejection_code"):
                if key in labels and not re.fullmatch(r"0|[1-9]\d{0,2}", labels[key]):
                    raise ValueError()
                if key in labels and int(labels[key]) > 600:
                    raise ValueError()
            if "le" in labels and labels["le"] not in BUCKETS:
                raise ValueError()
            if value != value.to_integral_value() or value > 2 ** 53:
                raise ValueError()
            key = (metric, tuple(sorted(labels.items())))
            if key in seen:
                raise ValueError()
            seen.add(key)
            if len(rows) >= MAX_METRIC_ROWS:
                overflow = True
                continue
            rows.append({"metric": metric, "labels": labels, "value": int(value), "sample_at": sample_at})
        except (ValueError, OverflowError):
            if is_identity:
                identity_invalid = True
            else:
                invalid += 1
    # A truncated or internally inconsistent histogram cannot support interval
    # evidence. Bucket counters are cumulative across le as well as over time.
    groups = {}
    for row in rows:
        if row["metric"] in (HISTOGRAM + "_count", HISTOGRAM + "_bucket"):
            group = tuple(sorted((k, v) for k, v in row["labels"].items() if k != "le"))
            groups.setdefault(group, {})[row["labels"].get("le", "count")] = row["value"]
    invalid_groups = set()
    for group, values in groups.items():
        buckets = [values[k] for k in sorted(BUCKETS, key=float) if k in values]
        if (set(values) != BUCKETS | {"count"} or buckets != sorted(buckets)
                or values.get("+Inf") != values.get("count")):
            invalid_groups.add(group)
            invalid += 1
    rows = [r for r in rows if r["metric"] not in (HISTOGRAM + "_count", HISTOGRAM + "_bucket")
            or tuple(sorted((k, v) for k, v in r["labels"].items() if k != "le")) not in invalid_groups]
    reason = ("invalid-source-identity" if identity_invalid else
              "multiple-apiserver-identities" if len(hashes) > 1 else
              "apiserver-identity-unavailable" if not hashes else None)
    source = {"status": "unavailable" if reason else "verified", "reason": reason,
              "instance": alias("apiserver", next(iter(hashes))) if not reason else None,
              "identity_metric_names": sorted(identity_metrics),
              "process_start_time_seconds": process_starts[0] if len(process_starts) == 1 else None}
    if len(process_starts) > 1:
        source.update(status="unavailable", reason="multiple-process-start-samples", instance=None)
    families = {}
    for family in (HISTOGRAM, REJECTIONS, REQUESTS):
        count = sum(r["metric"] == family or r["metric"] in (family + "_bucket", family + "_count") for r in rows)
        families[family] = {"status": "verified" if count else "unavailable", "matching_series": count,
                            "family_samples_present": family in exposed,
                            "reason": None if count else "no-matching-samples" if family in exposed else "family-not-exposed"}
    priority = {REJECTIONS: 0, REQUESTS: 1, HISTOGRAM + "_count": 2, HISTOGRAM + "_bucket": 3}
    return {"rows": sorted(rows, key=lambda r: (priority[r["metric"]], sorted(r["labels"].items()))),
            "source_identity": source, "families": families,
            "invalid_samples": invalid, "series_limit_exceeded": overflow,
            "series_before_report_limits": len(rows), "values": "cumulative; not observation-window changes"}


def metric_changes(observations):
    result = {"status": "unavailable", "reason": "pair-unavailable", "rows": [],
              "window_activity": "unknown", "series_before_report_limits": 0}

    def reject(reason):
        result["reason"] = reason
        return result

    if len(observations) != 2 or any(o["status"] != "verified" for o in observations):
        return result
    first, second = observations
    sources = [o["source_identity"] for o in observations]
    if any(s["status"] != "verified" for s in sources):
        return reject("apiserver-identity-unavailable")
    if sources[0]["instance"] != sources[1]["instance"]:
        return reject("different-apiserver-instances")
    if any(s["process_start_time_seconds"] is None for s in sources):
        return reject("process-start-unavailable")
    if sources[0]["process_start_time_seconds"] != sources[1]["process_start_time_seconds"]:
        return reject("process-restarted")
    if any(o["invalid_samples"] or o["series_limit_exceeded"] for o in observations):
        return reject("incomplete-or-invalid-samples")
    if stamp(second["started_at"]) <= stamp(first["finished_at"]):
        return reject("non-increasing-observation-times")
    by_key = [{(r["metric"], tuple(sorted(r["labels"].items()))): r for r in o["rows"]} for o in observations]
    if not by_key[0] or by_key[0].keys() != by_key[1].keys():
        return reject("missing-or-changed-series; absent-is-not-zero")
    changes = []
    for key, before in by_key[0].items():
        after = by_key[1][key]
        if after["value"] < before["value"]:
            return reject("counter-reset-or-negative-delta")
        times = (before["sample_at"], after["sample_at"])
        if any(times) and (not all(times) or stamp(times[1]) <= stamp(times[0])):
            return reject("non-increasing-or-missing-sample-timestamps")
        if all(times) and any(not stamp(o["started_at"]) <= stamp(at) <= stamp(o["finished_at"])
                              for o, at in zip(observations, times)):
            return reject("sample-timestamp-outside-observation")
        changes.append({"metric": before["metric"], "labels": before["labels"],
                        "before": before["value"], "after": after["value"], "delta": after["value"] - before["value"]})
    groups = {}
    for row in changes:
        if row["metric"] == HISTOGRAM + "_bucket":
            group = tuple(sorted((k, v) for k, v in row["labels"].items() if k != "le"))
            groups.setdefault(group, []).append((float(row["labels"]["le"]), row["delta"]))
    if any([delta for _, delta in sorted(buckets)] != sorted(delta for _, delta in buckets)
           for buckets in groups.values()):
        return reject("inconsistent-histogram-deltas")
    activity = [r for r in changes if r["metric"] in (REQUESTS, HISTOGRAM + "_count")]
    result.update(status="verified", reason=None, rows=changes, series_before_report_limits=len(changes),
                  window_activity=("matching-observations-increased" if any(r["delta"] for r in activity) else
                                   "no-increase-in-observed-request-or-histogram-counts" if activity else "unknown"),
                  from_observation=first["finished_at"], to_observation=second["finished_at"],
                  instance=sources[0]["instance"])
    return result


def apiserver_evidence(reader, enabled=False, interval=None, max_items=20):
    evidence = {"status": "not run", "reason": "not-requested", "observations": [],
                "counter_changes": metric_changes([]), "requested_interval_seconds": interval,
                "load_balancer_uncertainty": "The selected endpoint may route each GET to a different API server; context/URL and start time alone are not instance identity.",
                "interpretation": "Aggregated API-server webhook metrics cannot identify a failed request or separate connection/TLS/network delay, API-server admission delay and Kyverno handler delay. calling_webhook_error includes multiple failure types; proximity is not causation."}
    if not enabled:
        return evidence

    def observe(number):
        key = f"apiserver-metrics:{number}"
        began = iso(datetime.now(UTC))
        parsed = reader.read_metrics(key)
        observation = {"started_at": began, "finished_at": iso(datetime.now(UTC)), **reader.sources[key]}
        # Reader's 'at' is request start, not a server-provided sample timestamp.
        observation.update(parsed)
        evidence["observations"].append(observation)
        return observation

    first = observe(1)
    evidence.update(status=first["status"], reason=first.get("reason"))
    if interval is not None:
        reason = None
        if first["status"] != "verified":
            reason = "first-observation-unavailable"
        elif first["source_identity"]["status"] != "verified":
            reason = first["source_identity"]["reason"]
        elif first["source_identity"]["process_start_time_seconds"] is None:
            reason = "process-start-unavailable"
        elif first["invalid_samples"] or first["series_limit_exceeded"] or not first["rows"]:
            reason = "first-observation-incomplete-or-empty"
        elif time.monotonic() + interval + reader.request_timeout + 2 >= reader.deadline:
            reason = "insufficient-snapshot-budget-for-pair"
        if reason:
            reader.sources["apiserver-metrics:2"] = {"status": "unavailable", "reason": reason}
            evidence["counter_changes"]["reason"] = reason
        else:
            time.sleep(interval)
            observe(2)
            evidence["counter_changes"] = metric_changes(evidence["observations"])
        reader.sources["apiserver-metrics:changes"] = {k: evidence["counter_changes"][k] for k in ("status", "reason")}
    for observation in evidence["observations"]:
        if observation["status"] == "verified":
            if observation["source_identity"]["status"] != "verified":
                evidence.update(status="unavailable", reason=observation["source_identity"]["reason"])
            incomplete = observation["invalid_samples"] or observation["series_limit_exceeded"]
            for state in observation["families"].values():
                if state["status"] == "unavailable" or incomplete:
                    evidence.update(status="unavailable", reason="missing-or-incomplete-metrics")
            observation["rows"] = observation["rows"][:max_items]
        else:
            evidence.update(status="unavailable", reason=observation.get("reason"))
    evidence["counter_changes"]["rows"] = evidence["counter_changes"]["rows"][:max_items]
    reader.sources["apiserver-metrics"] = {k: evidence[k] for k in ("status", "reason")}
    return evidence


class Reader:
    def __init__(self, context, budget=90, request_timeout=8):
        self.context = context
        self.deadline = time.monotonic() + budget
        self.request_timeout = request_timeout
        self.sources = {}

    def command(self, args, cap):
        return self._command(args, cap)

    def _command(self, args, cap, metrics=None):
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
                        size = 65536
                        if metrics is not None:
                            size = min(size, METRICS_INPUT_CAP - metrics.input_bytes + 1
                                       if key.data is output else METRICS_STDERR_CAP - len(errors) + 1)
                        chunk = os.read(key.fileobj.fileno(), size)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            if metrics is not None and key.data is output:
                                metrics.feed(chunk)
                            else:
                                key.data.extend(chunk)
                            if metrics is not None and len(errors) > METRICS_STDERR_CAP:
                                raise Unavailable("metrics-stderr-byte-limit-exceeded")
                            if metrics is None and len(output) + len(errors) > cap:
                                raise Unavailable("response-byte-limit-exceeded")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Unavailable("request-time-budget-exhausted")
                proc.wait(timeout=remaining)
            if proc.returncode:
                # Never reproduce stderr, which may include credential-plugin data.
                reason = "forbidden" if b"Forbidden" in errors else "request-failed"
                raise Unavailable(reason)
            return metrics.finish() if metrics is not None else output.decode("utf-8", errors="replace")
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

    def read_metrics(self, key):
        at = iso(datetime.now(UTC))
        stream = MetricsStream()
        parsed = {}
        try:
            lines = self._command(["get", "--raw", "/metrics"], METRICS_INPUT_CAP, metrics=stream)
            parsed = admission_metrics(lines)
            if time.monotonic() >= self.deadline:
                raise Unavailable("snapshot-budget-exhausted")
            if parsed["invalid_samples"] or parsed["series_limit_exceeded"]:
                raise Unavailable("invalid-or-incomplete-metrics")
            if parsed["source_identity"]["reason"] in ("invalid-source-identity", "multiple-process-start-samples"):
                raise Unavailable(parsed["source_identity"]["reason"])
            self.sources[key] = {"status": "verified", "at": at, **stream.provenance(complete=True)}
        except Unavailable as exc:
            # Never release valid-looking subsets of a failed observation.
            if parsed:
                parsed["rows"] = []
                for state in parsed["families"].values():
                    state.update(status="unavailable", reason="invalid-or-incomplete-metrics")
            self.sources[key] = {"status": "unavailable", "at": at, "reason": str(exc), **stream.provenance()}
        return parsed

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


def collect(reader, since_minutes=30, namespace="kyverno", flux_namespace="flux-system", service="kyverno-svc", max_items=20,
            apiserver_metrics=False, metrics_interval=None):
    began = datetime.now(UTC)
    start = began - timedelta(minutes=since_minutes)
    api_metrics = apiserver_evidence(reader, apiserver_metrics, metrics_interval, max_items)
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
        api_metrics["interpretation"],
        "API-server metric observations cover their own collection times, not the historical Flux event window; cumulative totals and histogram buckets are not per-request latency.",
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
        "apiserver_metrics": api_metrics,
        "evidence_classification": {
            "api_server_observations": "apiserver_metrics.observations: cumulative allowlisted samples only",
            "counter_changes": "apiserver_metrics.counter_changes: only verified same-instance, same-process pairs",
            "historical_flux_events": "confirmed_observations.flux_timeouts: event last-observed or condition last-transition times",
            "possible_correlations": "possible_correlations: overlap does not establish causation",
            "per_request_evidence": "No API-server request trace or transport/handler timing is collected; failure boundary remains unverified.",
        },
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
        telemetry = report.get("apiserver_metrics", {})
        arrays.extend(o["rows"] for o in telemetry.get("observations", []) if "rows" in o)
        if "counter_changes" in telemetry:
            arrays.append(telemetry["counter_changes"]["rows"])
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
    parser.add_argument("--apiserver-metrics", action="store_true", help="Opt in to one bounded /metrics GET")
    parser.add_argument("--metrics-interval-seconds", type=bounded_int(1, 30), default=None,
                        help="Request at most two /metrics observations, separated by 1–30 seconds; requires --apiserver-metrics")
    args = parser.parse_args()
    if args.metrics_interval_seconds is not None and not args.apiserver_metrics:
        parser.error("--metrics-interval-seconds requires --apiserver-metrics")
    for value in (args.namespace, args.flux_namespace, args.service):
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
            parser.error("namespace and service must be DNS labels")
    reader = Reader(args.context, args.budget_seconds)
    try:
        report = collect(reader, args.since_minutes, args.namespace, args.flux_namespace, args.service, args.max_items,
                         args.apiserver_metrics, args.metrics_interval_seconds)
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
