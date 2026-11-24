# Confidential repository advisory publisher

The dedicated hourly job defaults to a **read-only API check**. Its sole
credential is the separately encrypted repository security-advisories token.
It has no Kubernetes API token, RBAC or public issue-writing credentials.
It does not consume Kubescape CVEs, runtime findings, or Kyverno violations:
these are not evidence of a confirmed vulnerability in this repository.

A maintainer must explicitly approve an actual repository vulnerability before
submission. A separate optional Secret named security-advisory-approved in
monitoring supplies approved.json. Keep evidence confidential; do not put its
plaintext in Git, public issue bodies, PR descriptions, or logs. Supply the
Secret only through a separately reviewed encrypted GitOps change. Never use
a synthetic production advisory as an acceptance test.

The exact approved document fields are schema (integer 1), findingId (stable
lowercase identifier), approved (boolean true), source (homelab-infra),
commit (reviewed full 40-character Git commit), summary, description,
severity (low/medium/high/critical), affectedVersionRange, patchedVersions
(string or null). The maintainer is responsible for the factual accuracy and
applicability of the evidence and package/version information; the publisher
does not validate vulnerability existence. No queue is installed by default.

The only write API is POST to the repository security-advisories collection.
No state, publication, CVE request, collaborator or private fork operation is
allowed. A stable marker and a durable pre-POST intent fence on an independent
128Mi Longhorn PVC prevent ambiguous retries. A missing or ambiguous remote
record is not evidence of resolution; investigate locally before any manual
state change. The complete listing is limited to 100 advisories, and a next
page or malformed response fails closed. No automatic updates or closure.

Inspect only the aggregate job status or its static idle/existing/created/
pending/unknown result. Keep actual advisory identifiers and descriptions
within the GitHub Security dashboard. Check the first scheduled run is idle
and successful before supplying any real approved record. The idle result
does not prove POST permissions or end-to-end creation.
