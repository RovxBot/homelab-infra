"""Publish only explicitly reviewed vulnerabilities in this project as private drafts.

An empty queue is a read-only credential/API check. Neither upstream CVEs nor
runtime detections are evidence of a vulnerability in our own project.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler, ProxyHandler

URL = "https://api.github.com/repos/RovxBot/homelab-infra/security-advisories"
MAX_BODY = 2 * 1024**2
QUEUE = Path("/queue/approved.json")
STATE = Path("/state")
TOKEN = Path("/credential/token")
FIELDS = {"schema", "findingId", "approved", "source", "commit", "summary",
          "description", "severity", "affectedVersionRange", "patchedVersions"}
STATES = {"draft", "triage", "published", "closed"}


def require(value):
    if not value:
        raise ValueError("advisory_contract")


def text_field(value, maximum):
    require(isinstance(value, str) and 0 < len(value.strip()) <= maximum
            and len(value) <= maximum and "\x00" not in value)
    return value.strip()


def approved_payload(entry):
    require(isinstance(entry, dict) and set(entry) == FIELDS)
    require(type(entry["schema"]) is int and entry["schema"] == 1
            and entry["approved"] is True and entry["source"] == "homelab-infra")
    require(isinstance(entry["findingId"], str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", entry["findingId"]))
    require(isinstance(entry["commit"], str) and re.fullmatch(r"[0-9a-f]{40}", entry["commit"]))
    require(entry["severity"] in ("low", "medium", "high", "critical"))
    key = hashlib.sha256(("homelab-infra:" + entry["findingId"]).encode()).hexdigest()
    marker = "<!-- homelab-advisory:" + key + " -->"
    summary = text_field(entry["summary"], 200)
    description = text_field(entry["description"], 12000)
    require("homelab-advisory:" not in description and "homelab-advisory:" not in summary)
    affected = text_field(entry["affectedVersionRange"], 200)
    patched = entry["patchedVersions"]
    if patched is not None:
        patched = text_field(patched, 200)
    return key, marker, {
        "summary": summary,
        "description": marker + "\n\n" + description + "\n\nReviewed source revision: " + entry["commit"],
        "severity": entry["severity"],
        "cve_id": None,
        "start_private_fork": False,
        "vulnerabilities": [{"package": {"ecosystem": "other", "name": "homelab-infra"},
                             "vulnerable_version_range": affected, "patched_versions": patched}],
    }


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GitHub:
    def __init__(self, token):
        require(isinstance(token, str) and 20 <= len(token) <= 512 and not any(c.isspace() for c in token))
        self.token = token
        self.opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))

    def request(self, method, payload=None):
        require(method in ("GET", "POST"))
        if method == "POST":
            require(isinstance(payload, dict) and set(payload) == {"summary", "description", "severity", "cve_id", "start_private_fork", "vulnerabilities"}
                    and payload["cve_id"] is None and payload["start_private_fork"] is False)
        else:
            require(payload is None)
        # No arbitrary endpoint, update, publish, CVE request, or fork API.
        request = Request(URL + ("?per_page=100" if method == "GET" else ""),
                          data=None if payload is None else json.dumps(payload).encode(),
                          method=method, headers={"Authorization": "Bearer " + self.token,
                              "Accept": "application/vnd.github+json",
                              "X-GitHub-Api-Version": "2026-03-10",
                              "Content-Type": "application/json",
                              "User-Agent": "homelab-draft-advisory-publisher"})
        with self.opener.open(request, timeout=20) as response:
            require(response.status == (200 if method == "GET" else 201))
            body = response.read(MAX_BODY + 1)
            require(len(body) <= MAX_BODY)
            # Fail closed on a partial listing. Do not follow attacker-controlled URLs.
            require(method != "GET" or 'rel="next"' not in response.headers.get("Link", ""))
            return json.loads(body)

    def list(self):
        data = self.request("GET")
        require(isinstance(data, list) and len(data) <= 100)
        for item in data:
            require(isinstance(item, dict) and item.get("state") in STATES
                    and isinstance(item.get("description"), str)
                    and isinstance(item.get("ghsa_id"), str))
        return data

    def create(self, payload):
        return self.request("POST", payload)


def fence(path, value):
    # A crash after POST or a lost response cannot trigger another creation.
    # The only automatic recovery is a unique matching remote marker.
    with path.open("x") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sync(client, state, entry=None):
    if entry is None:
        client.list()
        return "idle"
    key, marker, payload = approved_payload(entry)
    path = state / (key + ".intent")
    matches = [item for item in client.list() if marker in item["description"].splitlines()]
    require(len(matches) <= 1)
    if matches:
        require(re.fullmatch(r"GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}", matches[0]["ghsa_id"]))
        if not path.exists():
            fence(path, "remote-observed\n")
        return "existing"
    if path.exists():
        # A missing remote advisory is not permission to resubmit.
        return "pending"
    require(len(list(state.glob("*.intent"))) < 1000)
    fence(path, "create-intent\n")
    result = client.create(payload)
    require(isinstance(result, dict) and result.get("state") == "draft"
            and isinstance(result.get("description"), str)
            and marker in result["description"].splitlines()
            and isinstance(result.get("ghsa_id"), str)
            and re.fullmatch(r"GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}", result["ghsa_id"]))
    return "created"


def main():
    try:
        entry = None
        if QUEUE.exists():
            with QUEUE.open("rb") as stream:
                body = stream.read(32769)
            require(len(body) <= 32768)
            entry = json.loads(body)
            require(entry is not None)
        result = sync(GitHub(TOKEN.read_text().strip()), STATE, entry)
        print(json.dumps({"advisoryPublisher": result}), flush=True)
        return int(result == "pending")
    except Exception:
        # No confidential evidence, GHSA identifiers, exception messages or token.
        print('{"advisoryPublisher":"unknown"}', flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
