"""Isolated, stateful Kubescape publisher for private issue tracking.

Never import the receiver's private EventStore. Trust only a TokenReview-
protected, statically allowlisted projection, and never log credentials,
responses, private source data or failed HTTP bodies.
"""
import base64
from datetime import datetime, timezone, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler, ProxyHandler

REPO = "sam/homelab-security"
GITEA = "https://git.cooked.beer/api/v1"
REPO_ID = 11
ISSUE_PATH = "/repos/" + REPO + "/issues"
REPO_PATH = "/repos/" + REPO
RECEIVER = "https://security-receiver.kubescape.svc:8443/v1/private-incidents"
KUBERNETES = "https://kubernetes.default.svc/api/v1/namespaces/kubescape/secrets/security-receiver-ca"
ALLOWED = frozenset(("homepage/Deployment/homepage", "homepage/Deployment/homepage-assets",
                     "memos/Deployment/memos", "koito/Deployment/koito",
                     "koito/Deployment/koito-lastfm-bridge",
                     "monitoring/Deployment/gatus",
                     "openbrew/Deployment/openbrew",
                     "openbrew/Deployment/openbrew-db",
                     "stirling-pdf/Deployment/stirling-pdf",
                     "cloudflared/Deployment/cloudflared",
                     "mqtt/Deployment/mosquitto",
                     "security/Deployment/vaultwarden",
                     "wotlk/StatefulSet/wotlk-mariadb",
                     "mop/StatefulSet/skyfire-mariadb",
                     "kube-system/DaemonSet/kube-proxy"))
FIELDS = frozenset(("key", "workload", "rule", "severity", "first", "last", "count", "state"))
KEY = re.compile(r"[0-9a-f]{64}\Z")
RULE = re.compile(r"R[0-9]{4}\Z")
MAX_BODY = 128 * 1024
MAX_CVE_BODY = 256 * 1024  # Only the exact private CVE ConfigMap read.
IMAGE = "python:3.15-alpine@sha256:0f9552bb28711333da16915ec146f5e6bb44a7fdac9ced15dc17c2e0206346dd"


class PublisherFailure(Exception):
    """Static, fixed-namespace phase code; never include untrusted exception text."""
    def __init__(self, code):
        allowed = ("kubernetes_token", "kubernetes_api", "kubernetes_ca",
                   "receiver_token", "receiver_api", "projection",
                   "github_api", "publisher_state", "publisher_sync", "publisher_pending", "receiver_capacity",
                   "cve_api", "cve_projection", "cve_state", "cve_sync", "cve_coverage", "cve_pending")
        if not any(code == name or code.startswith(name + "_") for name in allowed):
            code = "publisher_state"
        self.code = code
        super().__init__(code)


def boundary_failure(phase, exc):
    # Never print stringified HTTP/TLS exceptions: URLs or headers may leak.
    if isinstance(exc, HTTPError):
        category = "http_" + str(exc.code) if exc.code in (400, 401, 403, 404, 408, 409, 422, 429, 500, 502, 503, 504) else "http_other"
    elif isinstance(exc, (TimeoutError,)):
        category = "timeout"
    elif isinstance(exc, URLError):
        if isinstance(exc.reason, TimeoutError):
            category = "timeout"
        elif isinstance(exc.reason, ssl.SSLError):
            category = "tls"
        else:
            category = "connection"
    elif isinstance(exc, ssl.SSLError):
        category = "tls"
    else:
        category = "invalid_response"
    return PublisherFailure(phase + "_" + category)


def require(condition):
    if not condition:
        raise ValueError("publication_contract_invalid")


def normalize_date(value):
    require(isinstance(value, str) and len(value) <= 40)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("publication_contract_invalid") from None
    require(dt.tzinfo is not None)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate(projection):
    require(isinstance(projection, dict) and set(projection) == {"schema", "source", "incidents"})
    require(projection["schema"] == 1 and projection["source"] == "kubescape-native")
    values = projection["incidents"]
    require(isinstance(values, list) and len(values) <= 80)
    known = set()
    for item in values:
        require(isinstance(item, dict) and set(item) == FIELDS)
        require(isinstance(item["key"], str) and KEY.fullmatch(item["key"]))
        require(item["key"] not in known)
        known.add(item["key"])
        require(item["workload"] in ALLOWED
                and isinstance(item["rule"], str) and RULE.fullmatch(item["rule"])
                and item["severity"] in ("High", "Critical") and item["state"] == "open")
        require(type(item["count"]) is int and 1 <= item["count"] <= 2**63 - 1)
        require(normalize_date(item["first"]) <= normalize_date(item["last"]))
    return sorted(values, key=lambda x: x["key"])


def marker(key):
    require(isinstance(key, str) and KEY.fullmatch(key))
    return "<!-- native-security:" + key + " -->"


def revision(item):
    return hashlib.sha256(json.dumps(item, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def render(item):
    # Fields are independently allowlisted and strictly typed by validate().
    title = "Security (Kubescape): " + item["rule"] + " in " + item["workload"]
    body = "\n".join((
        marker(item["key"]),
        "Native Kubescape runtime security finding (sanitized summary).",
        "Affected workload: " + item["workload"],
        "Detection rule: " + item["rule"],
        "Severity: " + item["severity"],
        "First observed: " + normalize_date(item["first"]),
        "Last observed: " + normalize_date(item["last"]),
        "Occurrences: " + str(item["count"]),
        "State: open — requires human investigation.",
        "Private process/event evidence remains inside Kubernetes.",
        "Do not close based on quiet telemetry, receiver restart or Gatus recovery.",
        "Resolution requires reviewed mitigation and fresh independent evidence.",
    ))
    return title, body


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("redirect_refused")


def request_json(opener, url, token, *, method="GET", payload=None, phase="github_api"):
    require(isinstance(url, str) and url.startswith("https://") and len(url) < 1800)
    require(isinstance(token, str) and 0 < len(token) <= 8192
            and "\r" not in token and "\n" not in token)
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    req = Request(url, data=body, method=method, headers={
        "Authorization": ("token " if url.startswith(GITEA + "/") else "Bearer ") + token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Accept-Encoding": "identity",
        "User-Agent": "security-native-publisher",
    })
    # The Kubernetes API and receiver are READ-ONLY sources: one bounded
    # retry is safe for transient timeouts/5xx. Issue POSTs are never retried
    # because the remote write may already have succeeded.
    attempts = 2 if method == "GET" and phase in ("kubernetes_api", "receiver_api", "cve_api") else 1
    for attempt in range(attempts):
        try:
            with opener.open(req, timeout=15) as response:
                require(response.status in (200, 201) and
                        response.headers.get("Content-Encoding", "identity") == "identity")
                limit = MAX_CVE_BODY if (url == CVE_CONFIG and method == "GET"
                                         and phase == "cve_api") else MAX_BODY
                data = response.read(limit + 1)
                require(len(data) <= limit)
                return json.loads(data)
        except (HTTPError, URLError, OSError, UnicodeError, ValueError) as exc:
            failure = boundary_failure(phase, exc)
            transient = failure.code.endswith(("_timeout", "_connection",
                                                 "_http_429", "_http_503", "_http_504"))
            if transient and attempt + 1 < attempts:
                time.sleep(1)
                continue
            raise failure from None


def receiver_connection():
    try:
        api_token = Path("/var/run/publisher-api/token").read_text().strip()
        api_ca = "/var/run/publisher-api/ca.crt"
        kube = build_opener(ProxyHandler({}), NoRedirect(),
                            HTTPSHandler(context=ssl.create_default_context(cafile=api_ca)))
    except Exception:
        raise PublisherFailure("kubernetes_token") from None
    secret = request_json(kube, KUBERNETES, api_token, phase="kubernetes_api")
    try:
        require(secret.get("kind") == "Secret"
                and secret.get("metadata", {}).get("namespace") == "kubescape"
                and secret["metadata"].get("name") == "security-receiver-ca")
        data = secret.get("data")
        # Strictly the public CA; never accept additional keys or a TLS leaf key.
        require(isinstance(data, dict) and set(data) == {"receiver-ca.crt"})
        pem = base64.b64decode(data["receiver-ca.crt"], validate=True).decode("ascii")
        require(pem.startswith("-----BEGIN CERTIFICATE-----\n") and
                pem.count("-----BEGIN CERTIFICATE-----") == 1 and len(pem) < 8192)
        context = ssl.create_default_context(cadata=pem)
        receiver = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=context))
    except Exception:
        raise PublisherFailure("kubernetes_ca") from None
    try:
        bearer = Path("/var/run/publisher-receiver/token").read_text().strip()
    except Exception:
        raise PublisherFailure("receiver_token") from None
    return receiver, bearer


def fetch_projection():
    receiver, bearer = receiver_connection()
    result = request_json(receiver, RECEIVER, bearer, phase="receiver_api")
    try:
        return validate(result)
    except Exception:
        raise PublisherFailure("projection") from None


class PrivateGitea:
    """Only the reviewed, private repository; no fallback or advisory API."""
    def __init__(self, token, requester=request_json):
        require(isinstance(token, str) and 0 < len(token) <= 8192
                and not any(ch.isspace() for ch in token))
        self.token = token
        self.requester = requester
        self.opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(
            context=ssl.create_default_context()))
        self.verify_private()

    def call(self, path, method="GET", body=None):
        if method == "GET":
            require(body is None and (path == REPO_PATH or
                    re.fullmatch(re.escape(ISSUE_PATH) +
                        r"(?:/[1-9][0-9]*(?:/comments)?|\?state=all&type=issues&limit=50&page=[1-9][0-9]*|/[1-9][0-9]*/comments\?limit=50&page=[1-9][0-9]*)", path)))
        elif method == "POST":
            require((path == ISSUE_PATH and isinstance(body, dict) and set(body) == {"title", "body"})
                    or (re.fullmatch(re.escape(ISSUE_PATH) + r"/[1-9][0-9]*/comments", path)
                        and isinstance(body, dict) and set(body) == {"body"}))
            self.verify_private()
        else:
            require(False)
        return self.requester(self.opener, GITEA + path, self.token,
                              method=method, payload=body)

    def verify_private(self):
        repo = self.call(REPO_PATH)
        require(isinstance(repo, dict) and type(repo.get("id")) is int
                and repo["id"] == REPO_ID and repo.get("full_name") == REPO
                and repo.get("private") is True and repo.get("has_issues") is True
                and repo.get("archived") is False)

    def search(self, key):
        # Enumerate open and closed issues; never depend on a search index.
        candidates = []
        for page in range(1, 21):
            part = self.call(ISSUE_PATH + "?state=all&type=issues&limit=50&page=" + str(page))
            require(isinstance(part, list) and len(part) <= 50)
            for item in part:
                require(isinstance(item, dict) and type(item.get("number")) is int
                        and item["number"] > 0 and isinstance(item.get("body"), str))
                if not item.get("pull_request") and marker(key) in item["body"]:
                    require(item["body"].count(marker(key)) == 1)
                    candidates.append(item)
            if len(part) < 50:
                require(len(candidates) <= 1)
                return candidates[0] if candidates else None
        require(False)

    def issue(self, number):
        require(type(number) is int and 0 < number < 10**9)
        return self.call(ISSUE_PATH + "/" + str(number))

    def create(self, title, body):
        return self.call(ISSUE_PATH, "POST", {"title": title, "body": body})

    def comments(self, number):
        require(type(number) is int and 0 < number < 10**9)
        comments = []
        for page in range(1, 8):
            part = self.call(ISSUE_PATH + "/" + str(number) + "/comments?limit=50&page=" + str(page))
            require(isinstance(part, list) and len(part) <= 50)
            comments.extend(part)
            require(len(comments) <= 300)
            if len(part) < 50:
                return comments
        require(False)

    def comment(self, number, body):
        require(type(number) is int and 0 < number < 10**9)
        return self.call(ISSUE_PATH + "/" + str(number) + "/comments", "POST", {"body": body})


class WriteBudgetDeferred(ValueError):
    """A local budget was exhausted before any write intent or remote POST."""


class DurablePublisher:
    def __init__(self, connection, github, now=None, renderer=render, fingerprint=revision, owns=None, create_limit=3):
        require(type(create_limit) is int and 1 <= create_limit <= 9)
        self.create_limit = create_limit
        self.renderer, self.fingerprint = renderer, fingerprint
        self.owns = owns or (lambda issue, key: issue["body"].count(marker(key)) == 1)
        self.db = connection
        self.github = github
        self.now = int(time.time()) if now is None else now
        self.db.execute("""CREATE TABLE IF NOT EXISTS issues
            (key TEXT PRIMARY KEY, number INTEGER, revision TEXT,
             pending_create INTEGER NOT NULL DEFAULT 0, pending_update TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS writes
            (at INTEGER NOT NULL, action TEXT NOT NULL)""")

    def reserve(self, action):
        with self.db:
            self.db.execute("DELETE FROM writes WHERE at < ?", (self.now - 86400,))
            hourly = self.db.execute("SELECT count(*) FROM writes WHERE at >= ?",
                                     (self.now-3600,)).fetchone()[0]
            creates = self.db.execute("SELECT count(*) FROM writes WHERE action='create'").fetchone()[0]
            if hourly >= 10 or (action == "create" and creates >= self.create_limit):
                raise WriteBudgetDeferred("write_budget_deferred")
            self.db.execute("INSERT INTO writes VALUES (?,?)", (self.now, action))

    def sync(self, item):
        title, body = self.renderer(item)
        fp = self.fingerprint(item)
        key = item["key"]
        row = self.db.execute(
            "SELECT number,revision,pending_create,pending_update FROM issues WHERE key=?",
            (key,)).fetchone()
        if row is None:
            found = self.github.search(key)
            if found is not None:
                number = found.get("number")
                require(type(number) is int and number > 0)
                with self.db:
                    self.db.execute("INSERT INTO issues VALUES (?,?,?,0,NULL)",
                                    (key, number, fp if body == found.get("body") else None))
                row = (number, fp if body == found.get("body") else None, 0, None)
            else:
                # Intent DURABLE before a potentially ambiguous external POST.
                self.reserve("create")
                with self.db:
                    self.db.execute("INSERT INTO issues VALUES (?,NULL,NULL,1,NULL)", (key,))
                created = self.github.create(title, body)
                number = created.get("number")
                require(type(number) is int and 0 < number < 10**9 and
                        created.get("body") == body)
                with self.db:
                    self.db.execute("UPDATE issues SET number=?,revision=?,pending_create=0 WHERE key=?",
                                    (number, fp, key))
                return "created"
        number, published, pending_create, pending_update = row
        if pending_create:
            # A crash/timeout during create: search can reconcile, but never
            # send a second create on absence; human review is then required.
            found = self.github.search(key)
            if found is None:
                return "pending_manual_create"
            number = found.get("number")
            require(type(number) is int and number > 0)
            with self.db:
                self.db.execute("UPDATE issues SET number=?,pending_create=0 WHERE key=?",
                                (number,key))
            published = None
        issue = self.github.issue(number)
        require(issue.get("number") == number and not issue.get("pull_request")
                and isinstance(issue.get("body"), str)
                and self.owns(issue, key))
        if issue.get("state") != "open":
            return "human_closed"
        if pending_update:
            code = "<!-- native-security-update:" + key + ":" + pending_update + " -->"
            found = any(code in (c.get("body") or "") for c in self.github.comments(number))
            if not found:
                return "pending_manual_update"
            with self.db:
                self.db.execute("UPDATE issues SET revision=?,pending_update=NULL WHERE key=?",
                                (pending_update, key))
            published = pending_update
        if published == fp:
            return "unchanged"
        # Preserve human edits and notes: never replace a remote issue body.
        # A pending comment intent is also never blindly replayed after failure.
        self.reserve("comment")
        with self.db:
            self.db.execute("UPDATE issues SET pending_update=? WHERE key=?", (fp,key))
        comment = "<!-- native-security-update:" + key + ":" + fp + " -->\n" + body.replace(
            marker(key) + "\n", "", 1)
        self.github.comment(number, comment)
        with self.db:
            self.db.execute("UPDATE issues SET revision=?,pending_update=NULL WHERE key=?",
                            (fp,key))
        return "updated"


CVE_ID = re.compile(r"CVE-[0-9]{4}-[0-9]{4,10}\Z")
CVE_ALLOWED = frozenset((
    "gitea/Deployment/gitea-mcp",
    "homepage/Deployment/homepage-assets",
    "homepage/Deployment/homepage",
    "koito/Deployment/koito-lastfm-bridge",
    "koito/Deployment/koito",
    "memos/Deployment/memos",
    "monitoring/Deployment/gatus",
    "openbrew/Deployment/openbrew",
    "stirling-pdf/Deployment/stirling-pdf",
    "wotlk/StatefulSet/wotlk-mariadb",
    "kube-system/DaemonSet/kube-proxy",
    "cloudflared/Deployment/cloudflared",
))
CVE_TITLE = "Security (Kubescape CVE)"
CVE_CONFIG = "https://kubernetes.default.svc/api/v1/namespaces/kubescape/configmaps/kubescape-private-cve"


# Reviewed owning GitOps manifests; kube-proxy's Talos-managed source is not
# established in this repository. A path is not a verified remediation.
CVE_SOURCES = {
    "gitea/Deployment/gitea-mcp": ("apps/gitea/mcp.yaml", ("mcp",)),
    "homepage/Deployment/homepage-assets": ("apps/homepage/assets-deployment.yaml", ("nginx",)),
    "homepage/Deployment/homepage": ("apps/homepage/deployment.yaml", ("homepage",)),
    "koito/Deployment/koito-lastfm-bridge": ("apps/koito/lastfm-bridge.yaml", ("multi-scrobbler",)),
    "koito/Deployment/koito": ("apps/koito/deployment.yaml", ("koito",)),
    "memos/Deployment/memos": ("apps/memos/deployment.yaml", ("memos",)),
    "monitoring/Deployment/gatus": ("infra/observability/gatus-helmrelease.yaml", ("gatus",)),
    "openbrew/Deployment/openbrew": ("apps/openbrew/deployment.yaml", ("openbrew",)),
    "stirling-pdf/Deployment/stirling-pdf": ("apps/stirling-pdf/deployment.yaml", ("stirling-pdf",)),
    "wotlk/StatefulSet/wotlk-mariadb": ("apps/wotlk/mariadb-statefulset.yaml", ("mysql",)),
    "kube-system/DaemonSet/kube-proxy": (None, ("kube-proxy",)),
    "cloudflared/Deployment/cloudflared": ("infra/cloudflared/deployment.yaml", ("cloudflared",)),
}
# Strict display-safe fields: no markdown delimiters, URLs with schemes,
# paths from report locations, arbitrary annotations or free-form prose.
PACKAGE_FIELD = re.compile(r"[A-Za-z0-9@][A-Za-z0-9._+:/@~=-]{0,127}\Z")
IMAGE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,383}@sha256:[a-f0-9]{64}\Z")
MAX_DETAIL_MATCHES = 12


def validate_cve_details(entry, generated):
    containers = entry["containers"]
    require(isinstance(containers, list))
    if entry["state"] == "unknown":
        require(not containers)
        return []
    expected = set(CVE_SOURCES[entry["workload"]][1])
    require(len(containers) == len(expected))
    seen, result, ids = set(), [], set()
    summary = {f["id"]: f for f in entry["findings"]}
    for item in containers:
        require(isinstance(item, dict) and set(item) == {
            "name", "image", "assessedAt", "detailState", "matches", "omittedMatches"})
        name = item["name"]
        require(name in expected and name not in seen)
        seen.add(name)
        require(isinstance(item["image"], str) and IMAGE_REF.fullmatch(item["image"]))
        at = datetime.fromisoformat(normalize_date(item["assessedAt"]).replace("Z", "+00:00"))
        require(timedelta(0) <= generated-at <= timedelta(hours=72))
        require(normalize_date(item["assessedAt"]) >= normalize_date(entry["assessedAt"]))
        state, matches, omitted = item["detailState"], item["matches"], item["omittedMatches"]
        require(state in ("complete", "partial", "unavailable"))
        require(isinstance(matches, list) and len(matches) <= MAX_DETAIL_MATCHES)
        if state == "unavailable":
            require(not matches and omitted is None)
        else:
            require(type(omitted) is int and 0 <= omitted <= 1000000)
            require((state == "complete") == (omitted == 0))
        unique = set()
        for match in matches:
            require(isinstance(match, dict) and set(match) == {
                "id", "severity", "package", "ecosystem", "installedVersion", "fixedVersions", "fixState"})
            identifier = match["id"]
            require(isinstance(identifier, str) and identifier in summary)
            require(match["severity"] in ("High", "Critical")
                    and (match["severity"] != "Critical" or summary[identifier]["severity"] == "Critical"))
            for field in ("package", "ecosystem", "installedVersion"):
                value = match[field]
                require(isinstance(value, str) and PACKAGE_FIELD.fullmatch(value)
                        and "://" not in value)
            require(match["fixState"] in ("fixed", "not-fixed", "wont-fix", "unknown"))
            versions = match["fixedVersions"]
            require(isinstance(versions, list) and len(versions) <= 4
                    and all(isinstance(v, str) and PACKAGE_FIELD.fullmatch(v) and "://" not in v for v in versions)
                    and len(set(versions)) == len(versions))
            require(not versions or match["fixState"] == "fixed")
            require(match["fixState"] != "fixed" or summary[identifier]["fixAvailable"])
            key = json.dumps(match, sort_keys=True, separators=(",", ":"))
            require(key not in unique)
            unique.add(key)
            ids.add(identifier)
        result.append(dict(item, matches=sorted(matches, key=lambda m: (
            m["severity"] != "Critical", m["id"], m["package"], m["ecosystem"], m["installedVersion"],
            m["fixState"], tuple(m["fixedVersions"])))))
    require(seen == expected)
    require(min(normalize_date(c["assessedAt"]) for c in result) == normalize_date(entry["assessedAt"]))
    if all(c["detailState"] == "complete" for c in result):
        require(ids == set(summary))
    return sorted(result, key=lambda c: c["name"])


def cve_key(workload):
    return hashlib.sha256(("cve:" + workload).encode()).hexdigest()


def validate_cve(projection, now=None):
    now = now or datetime.now(timezone.utc)
    require(isinstance(projection, dict) and set(projection) == {"schema", "source", "generatedAt", "entries"})
    schema = projection["schema"]
    require(type(schema) is int and schema in (1, 2) and projection["source"] == "kubescape-cve")
    generated = datetime.fromisoformat(normalize_date(projection["generatedAt"]).replace("Z", "+00:00"))
    require(timedelta(0) <= now-generated <= timedelta(minutes=65))
    entries = projection["entries"]
    require(isinstance(entries, list) and len(entries) == len(CVE_ALLOWED))
    seen, ready = set(), []
    for entry in entries:
        fields = {"workload", "state", "assessedAt", "findings", "otherHighCriticalMatches"}
        require(isinstance(entry, dict) and set(entry) == (fields | {"containers"} if schema == 2 else fields))
        workload = entry["workload"]
        require(workload in CVE_ALLOWED and workload not in seen)
        seen.add(workload)
        require(entry["state"] in ("unknown", "complete"))
        findings = entry["findings"]
        other = entry["otherHighCriticalMatches"]
        require(type(other) is int and 0 <= other <= 1000000)
        require(isinstance(findings, list) and len(findings) <= 100)
        if entry["state"] == "unknown":
            require(entry["assessedAt"] is None and not findings and other == 0)
            if schema == 2:
                validate_cve_details(entry, generated)
            continue
        assessed = datetime.fromisoformat(normalize_date(entry["assessedAt"]).replace("Z", "+00:00"))
        require(timedelta(0) <= generated-assessed <= timedelta(hours=72))
        # Recheck expiry on the publisher's own clock.
        if now-assessed > timedelta(hours=72):
            continue
        ids = set()
        for finding in findings:
            require(isinstance(finding, dict) and set(finding) == {"id", "severity", "fixAvailable"})
            identifier = finding["id"]
            require(isinstance(identifier, str) and CVE_ID.fullmatch(identifier) and identifier not in ids)
            ids.add(identifier)
            require(finding["severity"] in ("High", "Critical") and type(finding["fixAvailable"]) is bool)
        value = dict(entry, key=cve_key(workload), findings=sorted(findings, key=lambda x: x["id"]))
        if schema == 2:
            value["containers"] = validate_cve_details(entry, generated)
        ready.append(value)
    return ready


def render_cve(entry):
    title = CVE_TITLE + ": " + entry["workload"]
    findings = entry["findings"]
    critical = sum(f["severity"] == "Critical" for f in findings)
    fixable = sum(f["fixAvailable"] for f in findings)
    path, names = CVE_SOURCES[entry["workload"]]
    lines = [marker(entry["key"]), "## Summary",
             f"{len(findings)} distinct High/Critical CVEs ({critical} Critical); "
             f"{fixable} have a reported package-level fix for at least one match.",
             "A scanner rating is not proof of exploitability; a package fix is not proof of a fixed application image.",
             "", "## Affected resources", "- Workload: " + entry["workload"],
             "- Reviewed containers: " + ", ".join(names),
             "- Last qualified assessment (oldest container): " + normalize_date(entry["assessedAt"]),
             "- Owning GitOps source: " + ("`" + path + "`" if path else
                 "not established in this repository; verify Talos-managed component source"),
             "- Verified replacement image / matching remediation PR: not established by this report.",
             "", "## Required action"]
    if fixable:
        lines += ["1. Review the reported package fixes and applicability. Check for a supported image release or rebuild containing the fixes."]
    else:
        lines += ["1. No package fix is established by this assessment. Review vendor advisories and applicability; document risk or a supported mitigation if no fix exists."]
    lines += ["2. Check the owning image dependency and existing Renovate proposals; verify candidate immutable digest and fix evidence before proposing a reviewed GitOps change.",
              "3. Merge only after review and CI, verify the Flux rollout and assess the actual running replacement digest.",
              "", "## Container and package evidence"]
    containers = entry.get("containers")
    if not containers:
        lines += ["Package names, installed/fixed versions and running image references are not available in the current summary-only evidence. Inspect the qualified private assessment; do not infer a fixed image."]
    else:
        for c in containers:
            lines += ["", "### " + c["name"], "- Running image: `" + c["image"] + "`",
                      "- Assessed: " + normalize_date(c["assessedAt"]),
                      "- Package-detail coverage: " + c["detailState"]]
            if c["detailState"] == "partial":
                lines.append("- Additional package matches not displayed or not safely representable: " + str(c["omittedMatches"]))
            if c["detailState"] == "unavailable":
                lines.append("- Package details unavailable; inspect the private qualified report.")
            if c["matches"]:
                lines += ["| Advisory | Severity | Package (ecosystem) | Installed | Fixed versions | Fix state |",
                          "| --- | --- | --- | --- | --- | --- |"]
                for m in c["matches"]:
                    lines.append("| " + m["id"] + " | " + m["severity"] + " | `" + m["package"] + "` (" +
                        m["ecosystem"] + ") | `" + m["installedVersion"] + "` | " +
                        (", ".join("`" + v + "`" for v in m["fixedVersions"]) or "Not established") +
                        " | " + m["fixState"] + " |")
    lines += ["", "## Advisory summary (all qualified High/Critical CVE IDs)",
              "| Advisory | Scanner severity | Fix reported for at least one match |",
              "| --- | --- | --- |"]
    lines += ["| " + f["id"] + " | " + f["severity"] + " | " + ("Yes" if f["fixAvailable"] else "Not established") + " |"
              for f in sorted(findings, key=lambda f: (f["severity"] != "Critical", f["id"]))]
    lines += ["", "Additional High/Critical non-CVE matches retained privately: " + str(entry["otherHighCriticalMatches"]),
              "", "## Resolution checklist",
              "- [ ] Applicability and supported fix or approved risk disposition reviewed.",
              "- [ ] Remediation PR reviewed and merged, if a change is required.",
              "- [ ] Flux and workload rollout verified against the actual running digest.",
              "- [ ] Fresh complete assessment verifies tracked findings resolved or explicitly dispositioned.",
              "", "Automatic closure is not enabled yet. Absent, incomplete or stale reports never establish recovery."]
    return title, "\n".join(lines)


def cve_revision(entry):
    # Refresh the existing issue once when the presentation contract changes.
    # A daily repeat with identical evidence must not generate a new comment.
    value = {k: v for k, v in entry.items() if k != "assessedAt"}
    if "containers" in value:
        value["containers"] = [{k: v for k, v in c.items() if k != "assessedAt"} for c in value["containers"]]
    return revision({"formatVersion": 2, "evidence": value})


def owns_cve(issue, key):
    # Public issue numbers belong to another repository and are never adopted.
    return issue["body"].count(marker(key)) == 1


def fetch_cve_projection():
    try:
        token = Path("/var/run/publisher-api/token").read_text().strip()
        kube = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(
            context=ssl.create_default_context(cafile="/var/run/publisher-api/ca.crt")))
    except Exception:
        raise PublisherFailure("cve_api_configuration") from None
    obj = request_json(kube, CVE_CONFIG, token, phase="cve_api")
    try:
        require(obj.get("kind") == "ConfigMap" and obj["metadata"]["name"] == "kubescape-private-cve"
                and obj["metadata"]["namespace"] == "kubescape")
        # Empty initial object is an explicit not-yet-assessed condition.
        if not obj.get("data"):
            raise PublisherFailure("cve_coverage_missing")
        require(set(obj["data"]) == {"projection.json"})
        return validate_cve(json.loads(obj["data"]["projection.json"]))
    except PublisherFailure:
        raise
    except Exception:
        raise PublisherFailure("cve_projection_invalid") from None


def sync_cve_entries(publisher, ready):
    outcomes = []
    for entry in ready:
        if not (entry["findings"] or entry["otherHighCriticalMatches"]):
            continue
        try:
            outcomes.append(publisher.sync(entry))
        except WriteBudgetDeferred:
            # No intent or POST occurred. Continue reconciling other keys;
            # a later scheduled run may safely retry this bounded backlog.
            outcomes.append("deferred")
    return outcomes


def cve_main(token):
    ready = fetch_cve_projection()
    fd = None
    conn = None
    phase = "cve_state"
    try:
        fd = os.open("/state/gitea-cve-publisher.sqlite", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        conn = sqlite3.connect("/state/gitea-cve-publisher.sqlite", timeout=5)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=FULL")
        require(conn.execute("PRAGMA quick_check").fetchone()[0] == "ok")
        publisher = DurablePublisher(conn, PrivateGitea(token), renderer=render_cve,
                                     fingerprint=cve_revision, owns=owns_cve, create_limit=min(9, len(CVE_ALLOWED)))
        # Zero findings is not a resolution signal. Only positive findings
        # cause writes, and no issue is ever closed or reopened by this source.
        phase = "cve_sync"
        outcomes = sync_cve_entries(publisher, ready)
        pending = conn.execute("SELECT count(*) FROM issues WHERE pending_create != 0 OR pending_update IS NOT NULL").fetchone()[0]
        print(json.dumps({"source": "kubescape-cve", "qualifiedWorkloads": len(ready),
                          "expectedWorkloads": len(CVE_ALLOWED), "created": outcomes.count("created"),
                          "updated": outcomes.count("updated"), "pending": pending,
                          "deferred": outcomes.count("deferred")}), flush=True)
        if pending:
            raise PublisherFailure("cve_pending")
        if "deferred" in outcomes:
            raise PublisherFailure("cve_sync_budget_deferred")
        if len(ready) != len(CVE_ALLOWED):
            raise PublisherFailure("cve_coverage_incomplete")
    except PublisherFailure:
        raise
    except Exception:
        raise PublisherFailure(phase) from None
    finally:
        if conn is not None:
            conn.close()
        if fd is not None:
            os.close(fd)


def sync_native_entries(publisher, incidents):
    """Defer only pre-write budget exhaustion; never retry uncertain POSTs."""
    outcomes = []
    # Prioritize Critical incidents, then oldest first, with stable key ties.
    for item in sorted(incidents, key=lambda x: (
            x["severity"] != "Critical", normalize_date(x["first"]), x["key"])):
        try:
            outcomes.append(publisher.sync(item))
        except WriteBudgetDeferred:
            # Other keys may need an update rather than a create. No pending
            # intent or remote write was attempted for this deferred key.
            outcomes.append("deferred")
    return outcomes


def main():
    token = os.environ.get("GITEA_TOKEN", "")
    require(0 < len(token) <= 8192)
    incidents = fetch_projection()  # Fail before any issue API call.
    fd = None
    db = None
    try:
        try:
            Path("/state").mkdir(exist_ok=True)
            fd = os.open("/state/gitea-native-publisher.sqlite", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            db = sqlite3.connect("/state/gitea-native-publisher.sqlite", timeout=5)
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            require(db.execute("PRAGMA quick_check").fetchone()[0] == "ok")
            publisher = DurablePublisher(db, PrivateGitea(token))
        except Exception:
            raise PublisherFailure("publisher_state") from None
        try:
            outcomes = sync_native_entries(publisher, incidents)
        except PublisherFailure:
            raise
        except Exception:
            raise PublisherFailure("publisher_sync") from None
        pending = db.execute("SELECT count(*) FROM issues WHERE pending_create != 0 OR pending_update IS NOT NULL").fetchone()[0]
        # Only fixed, non-sensitive counts in Pod logs.
        print(json.dumps({"total":len(incidents),
                          "created":outcomes.count("created"),
                          "updated":outcomes.count("updated"),
                          "pending":pending,
                          "deferred":outcomes.count("deferred")}), flush=True)
        if pending:
            raise PublisherFailure("publisher_pending")
        if "deferred" in outcomes:
            raise PublisherFailure("publisher_sync_budget_deferred")
    finally:
        if db is not None:
            db.close()
        if fd is not None:
            os.close(fd)


# These are the deployed receiver's fixed hard limits. Alert at 80 percent
# without changing admission, receiver readiness or evidence retention.
RECEIVER_RECEIPTS_LIMIT = 20000
RECEIVER_INCIDENTS_LIMIT = 200


def check_receiver_capacity():
    receiver, bearer = receiver_connection()
    health = request_json(receiver, RECEIVER.rsplit("/", 2)[0] + "/healthz",
                          bearer, phase="receiver_api")
    try:
        require(isinstance(health, dict) and set(health) == {
            "receiver_ready", "delivery_coverage", "incidents", "receipts"})
        require(health["receiver_ready"] is True and health["delivery_coverage"] == "unverified")
        receipts, incidents = health["receipts"], health["incidents"]
        require(type(receipts) is int and 0 <= receipts <= RECEIVER_RECEIPTS_LIMIT
                and type(incidents) is int and 0 <= incidents <= RECEIVER_INCIDENTS_LIMIT)
    except Exception:
        raise PublisherFailure("receiver_capacity_invalid") from None
    if receipts * 5 >= RECEIVER_RECEIPTS_LIMIT * 4 or incidents * 5 >= RECEIVER_INCIDENTS_LIMIT * 4:
        raise PublisherFailure("receiver_capacity_threshold")


def run_stages(termination_path=Path("/dev/termination-log")):
    """Independent stages, one Pod and PVC; never publish raw error text."""
    states = {"native": "unknown", "cve": "unknown", "capacity": "unknown"}
    def persist():
        # Fixed enum values, atomically meaningful only after Pod termination.
        termination_path.write_text("security-v2|" + states["native"] + "|" + states["cve"] + "|" + states["capacity"])
    persist()
    for name, action in (("native", main), ("cve", lambda: cve_main(os.environ.get("GITEA_TOKEN", ""))),
                         ("capacity", check_receiver_capacity)):
        if name == "cve" and os.environ.get("ENABLE_CVE_PUBLISHER") != "true":
            continue
        try:
            action()
            states[name] = "healthy"
        except PublisherFailure as exc:
            states[name] = "failed"
            print("security_" + name + "_publisher_failed phase=" + exc.code, flush=True)
        except Exception:
            states[name] = "failed"
            print("security_" + name + "_publisher_failed phase=unclassified", flush=True)
        persist()
    return 0 if all(state == "healthy" for state in states.values()) else 1


if __name__ == "__main__":
    raise SystemExit(run_stages())
