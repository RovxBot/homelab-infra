"""Check signed provenance subjects and release source after cosign verification."""

import base64
import hashlib
import json
import pathlib

root = pathlib.Path(".work/cel/trust")
source = "f666af06e9bd310c7e730f1523c0430e90371a89"
identity = "https://github.com/kyverno/kyverno/.github/workflows/release-publish.yaml@refs/tags/v1.20.0"
summary = {
    "status": "verified",
    "source_commit": source,
    "identity": identity,
    "issuer": "https://token.actions.githubusercontent.com",
    "images": {},
    "blobs": {},
}
for path in sorted(root.glob("*.signature.json")):
    sig = json.loads(path.read_text())[0]
    assert sig["optional"]["Subject"] == identity
    assert sig["optional"]["Issuer"] == summary["issuer"]
    digest = sig["critical"]["image"]["docker-manifest-digest"]
    name = path.name.removesuffix(".signature.json")
    envelopes = [
        json.loads(line)
        for line in (root / (name + ".provenance.json")).read_text().splitlines()
        if line.strip()
    ]
    statements = [json.loads(base64.b64decode(e["payload"])) for e in envelopes]
    assert any(
        any(
            s["digest"].get("sha256") == digest.removeprefix("sha256:")
            for s in p["subject"]
        )
        and p["predicate"]["invocation"]["configSource"]["digest"]["sha1"] == source
        for p in statements
    ), name
    summary["images"][name] = digest
for name in ["kyverno-3.10.0.tgz", "kyverno-cli_v1.20.0_linux_x86_64.tar.gz"]:
    summary["blobs"][name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
(root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
