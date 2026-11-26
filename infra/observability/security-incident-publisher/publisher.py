"""Isolated, stateful Kubescape publisher for private issue tracking.

Never import the receiver's private EventStore. Trust only a TokenReview-
protected, statically allowlisted projection, and never log credentials,
responses, private source data or failed HTTP bodies.
"""
import base64
from datetime import datetime, timezone, timedelta
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import ssl
import time
import zipfile
from urllib.parse import urlsplit
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
MAX_CVE_BODY = 528 * 1024  # Only the exact private CVE ConfigMap read; allow metadata overhead.
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
    def __init__(self, token, requester=request_json, *, allow_cve_closure=False, allow_cve_reopen=False):
        self.allow_cve_closure = allow_cve_closure is True
        self.allow_cve_reopen = allow_cve_reopen is True
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
        elif method == "PATCH":
            require(re.fullmatch(re.escape(ISSUE_PATH) + r"/[1-9][0-9]*", path)
                    and ((self.allow_cve_closure and body == {"state":"closed"})
                         or (self.allow_cve_reopen and body == {"state":"open"})))
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

    def close_cve(self, number):
        require(type(number) is int and 0 < number < 10**9)
        return self.call(ISSUE_PATH + "/" + str(number), "PATCH", {"state":"closed"})

    def reopen_cve(self, number):
        require(type(number) is int and 0 < number < 10**9)
        return self.call(ISSUE_PATH + "/" + str(number), "PATCH", {"state":"open"})


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
# Reviewed dependency identities are not a version or fix claim. Keep chart
# and Talos dependencies separate from directly declared container images.
CVE_DEPENDENCIES = {
    "gitea/Deployment/gitea-mcp": {"mcp": "docker.gitea.com/gitea-mcp-server"},
    "homepage/Deployment/homepage-assets": {"nginx": "nginxinc/nginx-unprivileged"},
    "homepage/Deployment/homepage": {"homepage": "ghcr.io/gethomepage/homepage"},
    "koito/Deployment/koito-lastfm-bridge": {"multi-scrobbler": "foxxmd/multi-scrobbler"},
    "koito/Deployment/koito": {"koito": "gabehf/koito"},
    "memos/Deployment/memos": {"memos": "neosmemo/memos"},
    "monitoring/Deployment/gatus": {"gatus": "twinproduction/gatus"},
    "openbrew/Deployment/openbrew": {"openbrew": "austinderbique/openbrew"},
    "stirling-pdf/Deployment/stirling-pdf": {"stirling-pdf": "ghcr.io/stirling-tools/stirling-pdf"},
    "wotlk/StatefulSet/wotlk-mariadb": {"mysql": "mysql"},
    "cloudflared/Deployment/cloudflared": {"cloudflared": "cloudflare/cloudflared"},
}

def reviewed_dependency(workload, container):
    """Only the explicitly reviewed repository of this exact container."""
    source = CVE_SOURCES.get(workload)
    if source is None or container not in source[1]:
        return None
    return CVE_DEPENDENCIES.get(workload, {}).get(container)


def dependency_description(workload, names):
    values = [(name, reviewed_dependency(workload, name)) for name in names]
    if len(values) == 1:
        return values[0][1] or "not established; verify Talos-managed component release"
    return "; ".join(name + ": " + (dependency or "not established")
                     for name, dependency in values)

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


MAX_PRIVATE_CVES = 320
MAX_RECOVERY_CVES = 256


def validate_recovery_proof(entry, generated):
    """Schema 3 is optional proof, not a promise to resolve an issue.

    A producer must independently qualify exact current workload coverage,
    direct report attribution, all-severity CVEs and absence of ignored matches.
    """
    proof = entry["recoveryProof"]
    if proof is None:
        return None
    require(entry["state"] == "complete" and isinstance(proof, dict)
            and set(proof) == {"workloadIdentity", "containers"})
    require(isinstance(proof["workloadIdentity"], str) and KEY.fullmatch(proof["workloadIdentity"]))
    values = proof["containers"]
    require(isinstance(values, list) and len(values) == len(entry["containers"]))
    expected = {c["name"]: c for c in entry["containers"]}
    seen, ids, qualified = set(), set(), []
    for item in values:
        require(isinstance(item, dict) and set(item) == {
            "name", "databaseBuilt", "databaseChecksum", "allCves", "assessmentId"})
        name = item["name"]
        require(isinstance(name, str) and name in expected and name not in seen)
        seen.add(name)
        for field in ("databaseChecksum", "assessmentId"):
            require(isinstance(item[field], str) and KEY.fullmatch(item[field]))
        built = datetime.fromisoformat(normalize_date(item["databaseBuilt"]).replace("Z", "+00:00"))
        assessed = datetime.fromisoformat(normalize_date(expected[name]["assessedAt"]).replace("Z", "+00:00"))
        # Strict fresh database for recovery, independently of the reporting
        # grace period. Both scan time and current projection must qualify.
        require(built <= assessed <= generated and generated-built <= timedelta(days=7))
        all_cves = item["allCves"]
        require(isinstance(all_cves, list) and len(all_cves) <= MAX_RECOVERY_CVES
                and all(isinstance(v, str) and CVE_ID.fullmatch(v) for v in all_cves)
                and len(set(all_cves)) == len(all_cves))
        ids.update(all_cves)
        require(all(m["id"] in all_cves for m in expected[name]["matches"]))
        qualified.append(dict(item, allCves=sorted(all_cves), databaseBuilt=normalize_date(item["databaseBuilt"])))
    require(seen == set(expected) and {f["id"] for f in entry["findings"]} <= ids)
    return {"workloadIdentity":proof["workloadIdentity"],
            "containers":sorted(qualified, key=lambda c:c["name"])}


def cve_key(workload):
    return hashlib.sha256(("cve:" + workload).encode()).hexdigest()


def validate_cve(projection, now=None):
    now = now or datetime.now(timezone.utc)
    require(isinstance(projection, dict) and set(projection) == {"schema", "source", "generatedAt", "entries"})
    schema = projection["schema"]
    require(type(schema) is int and schema in (1, 2, 3) and projection["source"] == "kubescape-cve")
    generated = datetime.fromisoformat(normalize_date(projection["generatedAt"]).replace("Z", "+00:00"))
    require(timedelta(0) <= now-generated <= timedelta(minutes=65))
    entries = projection["entries"]
    require(isinstance(entries, list) and len(entries) == len(CVE_ALLOWED))
    seen, ready = set(), []
    for entry in entries:
        fields = {"workload", "state", "assessedAt", "findings", "otherHighCriticalMatches"}
        if schema >= 2:
            fields.add("containers")
        if schema == 3:
            fields.add("recoveryProof")
        require(isinstance(entry, dict) and set(entry) == fields)
        workload = entry["workload"]
        require(workload in CVE_ALLOWED and workload not in seen)
        seen.add(workload)
        require(entry["state"] in ("unknown", "complete"))
        findings = entry["findings"]
        other = entry["otherHighCriticalMatches"]
        require(type(other) is int and 0 <= other <= 1000000)
        require(isinstance(findings, list) and len(findings) <= MAX_PRIVATE_CVES)
        if entry["state"] == "unknown":
            require(entry["assessedAt"] is None and not findings and other == 0)
            if schema >= 2:
                validate_cve_details(entry, generated)
            if schema == 3:
                require(entry["recoveryProof"] is None)
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
        if schema >= 2:
            value["containers"] = validate_cve_details(entry, generated)
        if schema == 3:
            value["recoveryProof"] = validate_recovery_proof(value, generated)
        ready.append(value)
    return ready


REMEDIATION_REPO = "RovxBot/homelab-infra"
REMEDIATION_API = "https://api.github.com/repos/" + REMEDIATION_REPO
REMEDIATION_WORKFLOW = "security-remediation-candidates.yml"
REMEDIATION_ARTIFACT = "security-remediation-candidates"
MAX_REMEDIATION_BYTES = 512 * 1024
MAX_REMEDIATION_PRS = 50
MAX_REMEDIATION_PER_CONTAINER = 5
CANDIDATE_REF = re.compile(r"[a-z0-9][a-z0-9._:/-]{0,300}(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?@sha256:[a-f0-9]{64}\Z")


def canonical_repository(value):
    parts = value.split("/")
    if len(parts) == 1:
        return "docker.io/library/" + value
    if parts[0] in ("docker.io", "index.docker.io"):
        rest = parts[1:]
        return "docker.io/" + ("library/" if len(rest) == 1 else "") + "/".join(rest)
    if "." not in parts[0] and ":" not in parts[0] and parts[0] != "localhost":
        return "docker.io/" + value
    return value


def candidate_image_identity(value):
    if not isinstance(value, str) or not CANDIDATE_REF.fullmatch(value):
        return None
    named, digest = value.rsplit("@", 1)
    repository = named.rsplit(":", 1)[0] if ":" in named.rsplit("/", 1)[-1] else named
    return canonical_repository(repository), digest


def validate_remediation_snapshot(snapshot, base, now):
    require(isinstance(snapshot, dict) and set(snapshot) ==
            {"schema", "repository", "base", "state", "openPrCount", "skipped", "candidates", "generatedAt"})
    require(type(snapshot["schema"]) is int and snapshot["schema"] == 1
            and snapshot["repository"] == REMEDIATION_REPO and snapshot["state"] == "observed"
            and isinstance(base, str) and re.fullmatch(r"[a-f0-9]{40}", base) and snapshot["base"] == base)
    generated = datetime.fromisoformat(normalize_date(snapshot["generatedAt"]).replace("Z", "+00:00"))
    require(timedelta(0) <= now-generated <= timedelta(hours=2))
    count = snapshot["openPrCount"]
    require(type(count) is int and 0 <= count <= MAX_REMEDIATION_PRS)
    skipped = snapshot["skipped"]
    require(isinstance(skipped, dict) and set(skipped) <=
            {"not_ready", "unsupported_base_or_repository", "head_changed", "branch_behind_main"}
            and all(type(v) is int and 0 < v <= count for v in skipped.values())
            and sum(skipped.values()) <= count)
    candidates = snapshot["candidates"]
    require(isinstance(candidates, list) and len(candidates) <= count * sum(len(v[1]) for v in CVE_SOURCES.values()))
    seen, prs = set(), {}
    for item in candidates:
        require(isinstance(item, dict) and set(item) ==
                {"workload", "container", "path", "dependency", "before", "after", "state", "pr", "base", "head"})
        workload, name, number = item["workload"], item["container"], item["pr"]
        dependency = reviewed_dependency(workload, name)
        require(dependency is not None)
        path, names = CVE_SOURCES[workload]
        require(path and not path.endswith("-helmrelease.yaml") and item["path"] == path
                and name in names and item["state"] == "candidate_not_assessed"
                and type(number) is int and 0 < number < 10**9 and item["base"] == base
                and isinstance(item["head"], str) and re.fullmatch(r"[a-f0-9]{40}", item["head"])
                and item["head"] != base)
        old, new = candidate_image_identity(item["before"]), candidate_image_identity(item["after"])
        expected = canonical_repository(dependency)
        require(old is not None and new is not None and old[0] == new[0] == expected == item["dependency"]
                and old[1] != new[1])
        key = (number, workload, name)
        require(key not in seen and prs.setdefault(number, item["head"]) == item["head"])
        seen.add(key)
    require(len(prs) <= count - sum(skipped.values()))
    return sorted(candidates, key=lambda x: (x["workload"], x["container"], x["pr"]))


def correlate_remediation(ready, candidates):
    """Match qualified running repository AND digest; not a fix or closure proof."""
    for entry in ready:
        matches = []
        for container in entry.get("containers", []):
            current = candidate_image_identity(container["image"])
            if current is None:
                continue
            options = [x for x in candidates if x["workload"] == entry["workload"]
                       and x["container"] == container["name"]
                       and candidate_image_identity(x["before"]) == current]
            for item in options[:MAX_REMEDIATION_PER_CONTAINER]:
                matches.append({k: item[k] for k in ("container", "pr", "head", "after")})
        if matches:
            entry["remediationCandidates"] = matches
    return sum(len(e.get("remediationCandidates", [])) for e in ready)


class ArtifactRedirect(HTTPRedirectHandler):
    # Return the redirect to the caller; never forward an Authorization header.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def artifact_bytes(url, token):
    api = build_opener(ProxyHandler({}), ArtifactRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    request = Request(url, headers={"Authorization": "Bearer " + token, "User-Agent": "security-remediation-reader",
                                    "Accept": "application/vnd.github+json"})
    try:
        with api.open(request, timeout=10):
            raise ValueError("expected artifact redirect")
    except HTTPError as exc:
        require(exc.code == 302)
        location = exc.headers.get("Location", "")
        exc.close()
    parsed = urlsplit(location)
    require(parsed.scheme == "https" and parsed.hostname is not None and
            (parsed.hostname.endswith(".blob.core.windows.net") or
             parsed.hostname.endswith(".actions.githubusercontent.com")) and
            parsed.username is None and parsed.password is None and parsed.port in (None, 443)
            and not parsed.fragment)
    # Fresh request; only the signed storage URL, never the GitHub or Gitea token.
    blob = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    with blob.open(Request(location, headers={"Accept-Encoding": "identity"}), timeout=10) as response:
        require(response.status == 200 and response.headers.get("Content-Encoding", "identity") == "identity")
        data = response.read(MAX_REMEDIATION_BYTES + 1)
        require(len(data) <= MAX_REMEDIATION_BYTES)
        return data


def decode_remediation_artifact(data, digest):
    require(isinstance(data, bytes) and len(data) <= MAX_REMEDIATION_BYTES
            and digest == "sha256:" + hashlib.sha256(data).hexdigest())
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        require(len(members) == 1 and members[0].filename == "candidates.json"
                and 0 < members[0].file_size <= MAX_REMEDIATION_BYTES)
        with archive.open(members[0]) as stream:
            raw = stream.read(MAX_REMEDIATION_BYTES + 1)
        require(len(raw) == members[0].file_size)
        return json.loads(raw)


def fetch_remediation_candidates(token, now=None, requester=request_json, downloader=artifact_bytes):
    now = now or datetime.now(timezone.utc)
    require(isinstance(token, str) and 0 < len(token) <= 8192 and not any(c.isspace() for c in token))
    opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    def get(path):
        return requester(opener, REMEDIATION_API + path, token, method="GET", payload=None, phase="github_api")
    runs = get("/actions/workflows/" + REMEDIATION_WORKFLOW + "/runs?branch=main&status=success&per_page=1")
    require(isinstance(runs["workflow_runs"], list) and len(runs["workflow_runs"]) == 1)
    run = runs["workflow_runs"][0]
    number, base = run["id"], run["head_sha"]
    require(type(number) is int and 0 < number < 10**15 and isinstance(base, str)
            and re.fullmatch(r"[a-f0-9]{40}", base) and run["head_branch"] == "main"
            and run["head_repository"]["full_name"] == REMEDIATION_REPO
            and run["event"] in ("schedule", "workflow_dispatch") and run["conclusion"] == "success")
    created = datetime.fromisoformat(normalize_date(run["created_at"]).replace("Z", "+00:00"))
    require(timedelta(0) <= now-created <= timedelta(hours=2))
    artifacts = get("/actions/runs/" + str(number) + "/artifacts?per_page=20")
    require(type(artifacts["total_count"]) is int and artifacts["total_count"] == len(artifacts["artifacts"]) <= 20)
    matches = [a for a in artifacts["artifacts"] if a["name"] == REMEDIATION_ARTIFACT]
    require(len(matches) == 1)
    artifact = matches[0]
    identifier, size = artifact["id"], artifact["size_in_bytes"]
    require(type(identifier) is int and 0 < identifier < 10**15 and artifact["expired"] is False
            and type(size) is int and 0 < size <= MAX_REMEDIATION_BYTES
            and isinstance(artifact.get("digest"), str) and re.fullmatch(r"sha256:[a-f0-9]{64}", artifact["digest"]))
    raw = downloader(REMEDIATION_API + "/actions/artifacts/" + str(identifier) + "/zip", token)
    require(len(raw) == size)
    snapshot = decode_remediation_artifact(raw, artifact["digest"])
    require(created <= datetime.fromisoformat(normalize_date(snapshot["generatedAt"]).replace("Z", "+00:00")))
    return validate_remediation_snapshot(snapshot, base, now)


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
             "- Reviewed image dependency: " + dependency_description(entry["workload"], names),
             "- Verified replacement image / matching remediation PR: not established by this report.",
             ""]
    if entry.get("remediationCandidates"):
        lines += ["", "## Observed replacement PR candidates (not assessed)"]
        for candidate in entry["remediationCandidates"]:
            lines.append("- " + candidate["container"] + ": https://github.com/" + REMEDIATION_REPO +
                         "/pull/" + str(candidate["pr"]) + " at head `" + candidate["head"] +
                         "`; replacement `" + candidate["after"] + "`.")
        lines += ["These are recent manifest matches only. Recheck that the PR is still open and the head unchanged; "
                  "no fix or successful replacement assessment is established.", ""]
    lines.append("## Required action")
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
    value = {k: v for k, v in entry.items() if k not in ("assessedAt", "recoveryProof")}
    if "containers" in value:
        value["containers"] = [{k: v for k, v in c.items() if k != "assessedAt"} for c in value["containers"]]
    return revision({"formatVersion": 3, "evidence": value})


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


def cve_recovery_snapshot(entry):
    # Input has already passed validate_cve. Persist only the minimum evidence
    # needed for comparing qualified observations, not package-detail payloads.
    containers = entry.get("containers")
    if not containers:
        return None
    snapshot = {
        "containers": {c["name"]: {"image": c["image"],
                                    "assessedAt": normalize_date(c["assessedAt"])}
                       for c in containers},
        "findings": sorted(f["id"] for f in entry["findings"]),
        "otherHighCriticalMatches": entry["otherHighCriticalMatches"],
    }
    if entry.get("recoveryProof") is not None:
        snapshot["proof"] = entry["recoveryProof"]
    return snapshot


def recovery_candidate(baseline, entry):
    """Conservative shadow qualification; never a remote state transition."""
    current = cve_recovery_snapshot(entry)
    if (current is None or current["findings"] or current["otherHighCriticalMatches"]
            or not (baseline["findings"] or baseline["otherHighCriticalMatches"])
            or any(c["detailState"] != "complete" for c in entry["containers"])):
        return None
    before, after = baseline["containers"], current["containers"]
    if set(before) != set(after) or not before:
        return None
    # Repeated inventory refresh, stale/older scans, same-image DB-only changes
    # and partially rescanned multi-container workloads cannot qualify here.
    if any(after[name]["assessedAt"] <= before[name]["assessedAt"] for name in before):
        return None
    if not any(after[name]["image"].rsplit("@", 1)[-1] != before[name]["image"].rsplit("@", 1)[-1] for name in before):
        return None
    return current


def proof_qualified_recovery(baseline, candidate):
    """Additional qualification beyond the shadow image-change candidate."""
    before, after = baseline.get("proof"), candidate.get("proof")
    if before is None or after is None or baseline["otherHighCriticalMatches"]:
        return False
    if before["workloadIdentity"] != after["workloadIdentity"]:
        return False
    old = {c["name"]:c for c in before["containers"]}
    new = {c["name"]:c for c in after["containers"]}
    if not old or set(old) != set(new):
        return False
    if any(new[name]["databaseBuilt"] < old[name]["databaseBuilt"]
           or (new[name]["databaseBuilt"] == old[name]["databaseBuilt"]
               and new[name]["databaseChecksum"] != old[name]["databaseChecksum"])
           or new[name]["assessmentId"] == old[name]["assessmentId"] for name in old):
        return False
    # A tracked CVE may have been downgraded to Medium/Low, not removed.
    tracked = set(baseline["findings"])
    return not any(tracked.intersection(c["allCves"]) for c in new.values())


def observe_cve_recovery(publisher, ready, outcomes):
    """Retain a published positive baseline and compute read-only candidates.

    No PATCH, closure comment, issue creation, reopening or remote recovery write.
    Unknown/missing evidence removes candidate status, not the positive baseline.
    State is on the existing locked private publisher SQLite volume.
    """
    db = publisher.db
    db.execute("""CREATE TABLE IF NOT EXISTS cve_recovery_observations
        (key TEXT PRIMARY KEY, number INTEGER NOT NULL, published_revision TEXT NOT NULL,
         baseline TEXT NOT NULL, candidate TEXT)""")
    positives = [e for e in ready if e["findings"] or e["otherHighCriticalMatches"]]
    require(len(positives) == len(outcomes))
    success = {e["key"] for e, outcome in zip(positives, outcomes)
               if outcome in ("created", "updated", "unchanged")}
    counts = {"baselines": 0, "candidates": 0, "proofQualified": 0, "manualClosed": 0}
    # Invalidate every prior candidate atomically before evaluating this view.
    # An exception mid-observation rolls back the new view; readers must never
    # use this shadow table to perform writes. Only successful cycle counts
    # are reported as current.
    with db:
        db.execute("UPDATE cve_recovery_observations SET candidate=NULL")
        for entry in ready:
            key = entry["key"]
            mapping = db.execute(
                "SELECT number,revision,pending_create,pending_update FROM issues WHERE key=?",
                (key,)).fetchone()
            if mapping is None or mapping[0] is None or mapping[2] or mapping[3] is not None:
                continue
            number, published = mapping[:2]
            previous = db.execute(
                "SELECT number,published_revision,baseline FROM cve_recovery_observations WHERE key=?",
                (key,)).fetchone()
            snapshot = cve_recovery_snapshot(entry)
            if entry["findings"] or entry["otherHighCriticalMatches"]:
                if key not in success or snapshot is None or published != cve_revision(entry):
                    continue
                if previous is not None:
                    prior = json.loads(previous[2])
                    old = prior["containers"]
                    new = snapshot["containers"]
                    # Do not move the positive baseline backwards on replay.
                    if any(new[name]["assessedAt"] < old[name]["assessedAt"]
                           or (new[name]["assessedAt"] == old[name]["assessedAt"]
                               and new[name]["image"] != old[name]["image"])
                           for name in set(old) & set(new)):
                        continue
                    # Retain earlier tracked advisory IDs even if severity
                    # changes during a partial remediation. Preserve any
                    # unidentifiable non-CVE match as a manual-review blocker.
                    snapshot["findings"] = sorted(set(prior["findings"]) | set(snapshot["findings"]))
                    snapshot["otherHighCriticalMatches"] = max(prior["otherHighCriticalMatches"],
                                                               snapshot["otherHighCriticalMatches"])
                db.execute("INSERT OR REPLACE INTO cve_recovery_observations VALUES (?,?,?,?,NULL)",
                           (key, number, published, json.dumps(snapshot, sort_keys=True)))
                continue
            if previous is None or previous[:2] != (number, published):
                continue
            baseline = json.loads(previous[2])
            candidate = recovery_candidate(baseline, entry)
            if candidate is None:
                continue
            issue = publisher.github.issue(number)
            require(issue.get("number") == number and not issue.get("pull_request")
                    and isinstance(issue.get("body"), str) and publisher.owns(issue, key))
            if issue.get("state") != "open":
                counts["manualClosed"] += 1
                continue
            candidate["proofQualified"] = proof_qualified_recovery(baseline, candidate)
            db.execute("UPDATE cve_recovery_observations SET candidate=? WHERE key=?",
                       (json.dumps(candidate, sort_keys=True), key))
            counts["candidates"] += 1
            counts["proofQualified"] += int(candidate["proofQualified"])
        counts["baselines"] = db.execute("SELECT count(*) FROM cve_recovery_observations").fetchone()[0]
        require(counts["baselines"] <= len(CVE_ALLOWED))
    return counts


# Deliberately not configurable in production yet. Enablement requires a
# separate reviewed change and real recovery acceptance, not an env toggle.
CVE_CLOSURE_ENABLED = False
CVE_REOPEN_ENABLED = False
MAX_RECURRENCE_CYCLES = 32
SECURITY_BOT = "homelab-security-bot"


def recovery_marker(key, proof_id):
    require(KEY.fullmatch(key) and KEY.fullmatch(proof_id))
    return "<!-- native-security-recovery:" + key + ":" + proof_id + " -->"


def render_recovery(key, baseline, candidate):
    proof_id = revision(candidate)
    lines = [recovery_marker(key, proof_id), "## Verified vulnerability recovery",
             "Fresh independently attributed replacement assessments show no High/Critical findings and none of the previously tracked CVEs at any severity.",
             "", "| Container | Previous running image | Verified replacement | Assessed at (UTC) |",
             "| --- | --- | --- | --- |"]
    for name in sorted(candidate["containers"]):
        old, new = baseline["containers"][name], candidate["containers"][name]
        lines.append("| " + name + " | `" + old["image"] + "` | `" + new["image"] + "` | " + new["assessedAt"] + " |")
    lines += ["", "Tracked advisory IDs verified absent: " + str(len(baseline["findings"])),
              "This resolves the tracked assessment findings, not a guarantee that the application is free from every vulnerability."]
    return proof_id, "\n".join(lines)


def cve_closure_ledger(db):
    db.execute("""CREATE TABLE IF NOT EXISTS cve_closure_intents
        (key TEXT PRIMARY KEY, number INTEGER NOT NULL, proof_id TEXT NOT NULL,
         phase TEXT NOT NULL, evidence TEXT NOT NULL)""")


def bot_owned_issue(issue, number, key):
    # Adopted/human-created issues are not candidates for automatic closure.
    user = issue.get("user")
    return (issue.get("number") == number and not issue.get("pull_request")
            and isinstance(issue.get("body"), str) and owns_cve(issue,key)
            and isinstance(user, dict) and user.get("login") == SECURITY_BOT
            and issue.get("state") in ("open","closed"))


def recovery_comments(github, number, key, proof_id):
    code = recovery_marker(key, proof_id)
    comments = github.comments(number)
    # Gitea does not expose compare-and-swap state changes. Do not automate
    # closure of issues with human discussion, edited/unknown authors, or
    # unexpected comments. Their disposition must be reviewed manually.
    owned = all(isinstance(c.get("user"), dict) and
                c["user"].get("login") == SECURITY_BOT and
                isinstance(c.get("body"), str) and
                (c["body"].startswith("<!-- native-security-update:" + key + ":")
                 or c["body"].startswith("<!-- native-security-recovery:" + key + ":")
                 or c["body"].startswith("<!-- native-security-recurrence:" + key + ":"))
                for c in comments)
    found = sum(c.get("body","").startswith(code + "\n") for c in comments if isinstance(c.get("body"),str))
    require(found <= 1)
    return owned, found == 1


def sync_cve_closure(publisher, ready, outcomes, *, enabled=False):
    """Durable closure state machine. Production caller is hard-disabled.

    Recovery is requalified from the current successful observation, not
    a cached candidate from an earlier failed cycle. A missing response
    never triggers a second POST/PATCH. Reopening is intentionally separate.
    """
    db = publisher.db
    cve_closure_ledger(db)
    if not enabled:
        return {"closed":0,"pending":0,"manualReview":0,"deferred":0}
    counts = {"closed":0,"pending":0,"manualReview":0,"deferred":0}
    ready_by_key = {e["key"]:e for e in ready}
    for key, number, published, raw_baseline, raw_candidate in db.execute(
            "SELECT key,number,published_revision,baseline,candidate FROM cve_recovery_observations ORDER BY key").fetchall():
        existing = db.execute("SELECT number,proof_id,phase,evidence FROM cve_closure_intents WHERE key=?", (key,)).fetchone()
        if existing and existing[2] == "closed":
            continue
        entry = ready_by_key.get(key)
        baseline = json.loads(raw_baseline)
        candidate = recovery_candidate(baseline,entry) if entry is not None else None
        # Never treat a stale persisted shadow candidate as fresh authority.
        if (candidate is None or not proof_qualified_recovery(baseline,candidate)
                or raw_candidate is None):
            continue
        candidate["proofQualified"] = True
        if candidate != json.loads(raw_candidate):
            continue
        mapping = db.execute("SELECT number,revision,pending_create,pending_update FROM issues WHERE key=?", (key,)).fetchone()
        if mapping != (number,published,0,None):
            continue
        proof_id, body = render_recovery(key,baseline,candidate)
        if existing and existing[:2] != (number,proof_id):
            counts["manualReview"] += 1
            continue
        issue = publisher.github.issue(number)
        if not bot_owned_issue(issue,number,key):
            counts["manualReview"] += 1
            continue
        owned, found = recovery_comments(publisher.github,number,key,proof_id)
        if not owned:
            counts["manualReview"] += 1
            continue
        phase = existing[2] if existing else None
        if issue["state"] == "closed":
            # A read after a possibly successful PATCH cannot distinguish
            # a concurrent human close from ours. Preserve the ambiguity;
            # do not claim machine ownership or later reopen automatically.
            if phase == "patch_pending":
                with db:
                    db.execute("UPDATE cve_closure_intents SET phase='ambiguous_closed' WHERE key=?", (key,))
            counts["manualReview"] += 1
            continue
        if phase in ("patch_pending","ambiguous_closed"):
            counts["manualReview"] += 1
            continue
        try:
            if phase is None:
                if found:
                    counts["manualReview"] += 1
                    continue
                publisher.reserve("comment")
                with db:
                    db.execute("INSERT INTO cve_closure_intents VALUES (?,?,?,'comment_pending',?)",
                               (key,number,proof_id,json.dumps(candidate,sort_keys=True)))
                publisher.github.comment(number,body)
                phase = "comment_pending"
                owned, found = recovery_comments(publisher.github,number,key,proof_id)
            if phase == "comment_pending":
                if not owned or not found:
                    counts["manualReview"] += 1
                    continue
                with db:
                    db.execute("UPDATE cve_closure_intents SET phase='comment_confirmed' WHERE key=?", (key,))
                phase = "comment_confirmed"
            if phase != "comment_confirmed" or not found:
                counts["manualReview"] += 1
                continue
            # Fresh read immediately before the state-only write. No CAS is
            # available: the residual race is an explicit enablement blocker.
            latest = publisher.github.issue(number)
            owned, found = recovery_comments(publisher.github,number,key,proof_id)
            if not bot_owned_issue(latest,number,key) or latest["state"] != "open" or not owned or not found:
                counts["manualReview"] += 1
                continue
            publisher.reserve("close")
            with db:
                db.execute("UPDATE cve_closure_intents SET phase='patch_pending' WHERE key=?", (key,))
            closed = publisher.github.close_cve(number)
            require(bot_owned_issue(closed,number,key) and closed["state"] == "closed")
            evidence = dict(candidate, closureAcknowledgement=closure_acknowledgement(closed))
            with db:
                db.execute("UPDATE cve_closure_intents SET phase='closed',evidence=? WHERE key=?",
                           (json.dumps(evidence,sort_keys=True),key))
            counts["closed"] += 1
        except WriteBudgetDeferred:
            counts["deferred"] += 1
    counts["pending"] = db.execute("SELECT count(*) FROM cve_closure_intents WHERE phase != 'closed'").fetchone()[0]
    return counts


def closure_acknowledgement(issue):
    # Missing timestamps or editable issue metadata never authorize reopening.
    # Hash rather than persist the complete human-visible issue.
    try:
        updated = normalize_date(issue["updated_at"])
        closed = normalize_date(issue["closed_at"])
        if updated < closed:
            return None
        fields = ("title","body","labels","assignees","milestone")
        if any(k not in issue for k in fields):
            return None
        return {"updatedAt":updated,"closedAt":closed,
                "content":revision({k:issue[k] for k in fields})}
    except (ValueError,KeyError,TypeError):
        return None


def recurrence_candidate(closed_evidence, entry):
    current = cve_recovery_snapshot(entry)
    before = closed_evidence.get("proof")
    after = current.get("proof") if current else None
    if (not before or not after or not current["findings"] or current["otherHighCriticalMatches"]
            or before["workloadIdentity"] != after["workloadIdentity"]):
        return None
    old, new = closed_evidence["containers"], current["containers"]
    previous = {c["name"]:c for c in before["containers"]}
    latest = {c["name"]:c for c in after["containers"]}
    if not old or set(old) != set(new) or set(old) != set(previous) or set(old) != set(latest):
        return None
    if any(new[n]["assessedAt"] <= old[n]["assessedAt"]
           or latest[n]["assessmentId"] == previous[n]["assessmentId"]
           or latest[n]["databaseBuilt"] < previous[n]["databaseBuilt"]
           or (latest[n]["databaseBuilt"] == previous[n]["databaseBuilt"]
               and latest[n]["databaseChecksum"] != previous[n]["databaseChecksum"]) for n in old):
        return None
    return current


def recurrence_ledger(db):
    db.execute("""CREATE TABLE IF NOT EXISTS cve_recurrence_intents
        (key TEXT NOT NULL, closure_id TEXT NOT NULL, number INTEGER NOT NULL,
         recurrence_id TEXT NOT NULL, phase TEXT NOT NULL, closure_evidence TEXT NOT NULL,
         recurrence_evidence TEXT NOT NULL, PRIMARY KEY(key,closure_id))""")


def sync_cve_recurrence(publisher, ready, *, enabled=False):
    """Reopen only confirmed machine closures, with durable comment/PATCH intents.

    Reconciliation never replays an uncertain write. Both state-change gates
    remain hard disabled in production until live qualification.
    """
    db = publisher.db
    cve_closure_ledger(db)
    recurrence_ledger(db)
    counts = {"candidates":0,"reopened":0,"manualReview":0,"deferred":0,"pending":0}
    for entry in ready:
        key = entry["key"]
        row = db.execute("SELECT number,proof_id,phase,evidence FROM cve_closure_intents WHERE key=?",
                         (key,)).fetchone()
        if row is None or row[2] != "closed":
            continue
        number, closure_id, _, raw_closed = row
        closed = json.loads(raw_closed)
        current = recurrence_candidate(closed,entry)
        if current is None:
            continue
        counts["candidates"] += 1
        if not enabled:
            continue
        mapping = db.execute("SELECT number,pending_create,pending_update FROM issues WHERE key=?",(key,)).fetchone()
        if mapping != (number,0,None):
            counts["manualReview"] += 1
            continue
        ack = closed.get("closureAcknowledgement")
        if not isinstance(ack,dict):
            counts["manualReview"] += 1
            continue
        existing = db.execute("SELECT recurrence_id,phase FROM cve_recurrence_intents WHERE key=? AND closure_id=?",
                              (key,closure_id)).fetchone()
        recurrence_id = revision(current)
        phase = existing[1] if existing else None
        if (existing and existing[0] != recurrence_id) or phase in ("patch_pending","reopened"):
            counts["manualReview"] += 1
            continue
        if existing is None and db.execute("SELECT count(*) FROM cve_recurrence_intents WHERE key=?",(key,)).fetchone()[0] >= MAX_RECURRENCE_CYCLES:
            counts["manualReview"] += 1
            continue
        issue = publisher.github.issue(number)
        current_ack = closure_acknowledgement(issue)
        if (not bot_owned_issue(issue,number,key) or issue["state"] != "closed"
                or current_ack is None or current_ack["closedAt"] != ack["closedAt"]
                or current_ack["content"] != ack["content"]
                or (phase is None and current_ack != ack)):
            counts["manualReview"] += 1
            continue
        owned, recovered = recovery_comments(publisher.github,number,key,closure_id)
        if not owned or not recovered:
            counts["manualReview"] += 1
            continue
        code = "<!-- native-security-recurrence:" + key + ":" + recurrence_id + " -->"
        body = code + "\n## Vulnerability recurrence\nA newer qualified assessment again reports High/Critical CVEs for this workload. The previous verified recovery remains in the issue history.\n\n" + render_cve(entry)[1].replace(marker(key)+"\n","",1)
        def matching_comment():
            comments = publisher.github.comments(number)
            found = [c for c in comments if isinstance(c.get("body"),str) and c["body"].startswith(code+"\n")]
            require(len(found) <= 1)
            return bool(found and found[0].get("user",{}).get("login")==SECURITY_BOT
                        and found[0]["body"] == body)
        try:
            if phase is None:
                if matching_comment():
                    counts["manualReview"] += 1
                    continue
                publisher.reserve("comment")
                with db:
                    db.execute("INSERT INTO cve_recurrence_intents VALUES (?,?,?,?,'comment_pending',?,?)",
                               (key,closure_id,number,recurrence_id,raw_closed,json.dumps(current,sort_keys=True)))
                publisher.github.comment(number,body)
                phase = "comment_pending"
            if phase == "comment_pending":
                if not matching_comment():
                    counts["manualReview"] += 1
                    continue
                with db:
                    db.execute("UPDATE cve_recurrence_intents SET phase='comment_confirmed' WHERE key=? AND closure_id=?",
                               (key,closure_id))
                phase = "comment_confirmed"
            latest_issue = publisher.github.issue(number)
            latest_ack = closure_acknowledgement(latest_issue)
            owned,recovered = recovery_comments(publisher.github,number,key,closure_id)
            if (phase != "comment_confirmed" or not bot_owned_issue(latest_issue,number,key)
                    or latest_issue["state"]!="closed" or latest_ack is None
                    or latest_ack["closedAt"]!=ack["closedAt"] or latest_ack["content"]!=ack["content"]
                    or not owned or not recovered or not matching_comment()):
                counts["manualReview"] += 1
                continue
            publisher.reserve("reopen")
            with db:
                db.execute("UPDATE cve_recurrence_intents SET phase='patch_pending' WHERE key=? AND closure_id=?",
                           (key,closure_id))
            reopened = publisher.github.reopen_cve(number)
            require(bot_owned_issue(reopened,number,key) and reopened["state"]=="open")
            with db:
                db.execute("UPDATE cve_recurrence_intents SET phase='reopened' WHERE key=? AND closure_id=?",
                           (key,closure_id))
                # Archive the confirmed close in the recurrence ledger; reset
                # only the active lifecycle. The recurrence comment is the
                # published positive baseline for the next recovery cycle.
                db.execute("DELETE FROM cve_closure_intents WHERE key=? AND proof_id=?",(key,closure_id))
                db.execute("DELETE FROM cve_recovery_observations WHERE key=?",(key,))
                db.execute("UPDATE issues SET revision=? WHERE key=?",(cve_revision(entry),key))
            counts["reopened"] += 1
        except WriteBudgetDeferred:
            counts["deferred"] += 1
    counts["pending"] = db.execute("SELECT count(*) FROM cve_recurrence_intents WHERE phase!='reopened'").fetchone()[0]
    return counts


def cve_main(token):
    ready = fetch_cve_projection()
    remediation_token = os.environ.get("REMEDIATION_GITHUB_READ_TOKEN")
    remediation_state, remediation_count = "unconfigured", 0
    if remediation_token and remediation_token == token:
        # A misconfigured Secret must not send the private writer credential
        # to GitHub, even for an otherwise permitted GET.
        remediation_state = "credential_conflict"
    elif remediation_token:
        try:
            candidates = fetch_remediation_candidates(remediation_token)
            remediation_count = correlate_remediation(ready, candidates)
            remediation_state = "observed"
        except Exception:
            # Optional advisory enrichment never gates qualified CVE reporting.
            remediation_state = "unavailable"
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
        publisher = DurablePublisher(conn, PrivateGitea(token, allow_cve_closure=CVE_CLOSURE_ENABLED,
                                                   allow_cve_reopen=CVE_REOPEN_ENABLED), renderer=render_cve,
                                     fingerprint=cve_revision, owns=owns_cve, create_limit=min(9, len(CVE_ALLOWED)))
        # Only positive findings cause remote writes. Recovery observations
        # are shadow-only and never change remote issue state.
        phase = "cve_sync"
        recurrence = sync_cve_recurrence(publisher, ready, enabled=CVE_REOPEN_ENABLED)
        outcomes = sync_cve_entries(publisher, ready)
        recovery = observe_cve_recovery(publisher, ready, outcomes)
        closure = sync_cve_closure(publisher, ready, outcomes, enabled=CVE_CLOSURE_ENABLED)
        pending = conn.execute("SELECT count(*) FROM issues WHERE pending_create != 0 OR pending_update IS NOT NULL").fetchone()[0]
        print(json.dumps({"source": "kubescape-cve", "qualifiedWorkloads": len(ready),
                          "expectedWorkloads": len(CVE_ALLOWED), "created": outcomes.count("created"),
                          "remediationCorrelation": remediation_state, "remediationCandidates": remediation_count,
                          "updated": outcomes.count("updated"), "pending": pending,
                          "deferred": outcomes.count("deferred"),
                          "recoveryBaselines": recovery["baselines"],
                          "recoveryCandidates": recovery["candidates"],
                          "recoveryProofQualified": recovery["proofQualified"],
                          "recoveryManualClosed": recovery["manualClosed"],
                          "closureEnabled": CVE_CLOSURE_ENABLED,
                          "closurePending": closure["pending"],
                          "recurrenceCandidates": recurrence["candidates"],
                          "reopenEnabled": CVE_REOPEN_ENABLED,
                          "reopenPending": recurrence["pending"]}), flush=True)
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
