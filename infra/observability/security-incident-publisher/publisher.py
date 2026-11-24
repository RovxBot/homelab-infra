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
                     "koito/Deployment/koito-lastfm-bridge"))
FIELDS = frozenset(("key", "workload", "rule", "severity", "first", "last", "count", "state"))
KEY = re.compile(r"[0-9a-f]{64}\Z")
RULE = re.compile(r"R[0-9]{4}\Z")
MAX_BODY = 128 * 1024
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
    require(isinstance(values, list) and len(values) <= 30)
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
                data = response.read(MAX_BODY + 1)
                require(len(data) <= MAX_BODY)
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
))
CVE_TITLE = "Security (Kubescape CVE)"
CVE_CONFIG = "https://kubernetes.default.svc/api/v1/namespaces/kubescape/configmaps/kubescape-private-cve"


def cve_key(workload):
    return hashlib.sha256(("cve:" + workload).encode()).hexdigest()


def validate_cve(projection, now=None):
    now = now or datetime.now(timezone.utc)
    require(isinstance(projection, dict) and set(projection) == {"schema", "source", "generatedAt", "entries"})
    require(projection["schema"] == 1 and projection["source"] == "kubescape-cve")
    generated = datetime.fromisoformat(normalize_date(projection["generatedAt"]).replace("Z", "+00:00"))
    require(timedelta(0) <= now-generated <= timedelta(minutes=65))
    entries = projection["entries"]
    require(isinstance(entries, list) and len(entries) == len(CVE_ALLOWED))
    seen, ready = set(), []
    for entry in entries:
        require(isinstance(entry, dict) and set(entry) == {"workload", "state", "assessedAt", "findings", "otherHighCriticalMatches"})
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
        ready.append(dict(entry, key=cve_key(workload), findings=sorted(findings, key=lambda x: x["id"])))
    return ready


def render_cve(entry):
    title = CVE_TITLE + ": " + entry["workload"]
    lines = [marker(entry["key"]), "Verified Kubescape image-assessment summary (reviewed workload cohort).",
             "Affected workload: " + entry["workload"],
             "Last assessed: " + normalize_date(entry["assessedAt"]),
             "", "| Advisory | Scanner severity | Fix reported for at least one match |",
             "| --- | --- | --- |"]
    lines += ["| " + f["id"] + " | " + f["severity"] + " | " + ("Yes" if f["fixAvailable"] else "Not established") + " |"
              for f in entry["findings"]]
    lines += ["", "Additional High/Critical non-CVE matches retained privately: " + str(entry["otherHighCriticalMatches"]),
              "Inspect the original assessment for affected packages and applicability; a scanner severity is not proof of exploitability.",
              "Review the owning image dependency and any Renovate proposal, then independently reassess the deployed replacement.",
              "Never close automatically on absent or stale reports."]
    return title, "\n".join(lines)


def cve_revision(entry):
    # A daily repeat with identical findings does not create a new comment.
    return revision({k: v for k, v in entry.items() if k != "assessedAt"})


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
                                     fingerprint=cve_revision, owns=owns_cve, create_limit=len(CVE_ALLOWED))
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
            outcomes = [publisher.sync(item) for item in incidents]
        except PublisherFailure:
            raise
        except Exception:
            raise PublisherFailure("publisher_sync") from None
        pending = db.execute("SELECT count(*) FROM issues WHERE pending_create != 0 OR pending_update IS NOT NULL").fetchone()[0]
        # Only fixed, non-sensitive counts in Pod logs.
        print(json.dumps({"total":len(incidents),
                          "created":outcomes.count("created"),
                          "updated":outcomes.count("updated"),
                          "pending":pending}), flush=True)
        if pending:
            raise PublisherFailure("publisher_pending")
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
