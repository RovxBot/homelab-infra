#!/usr/bin/env python3
"""Immutable public build image scan. Publish provenance, never raw findings."""
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
from urllib.request import Request, urlopen

VERSION = "0.121.3"
ARCHIVE = "grype_" + VERSION + "_linux_amd64.tar.gz"
ASSET_DIGEST = "sha256:e9da18cc1429e786722e32161288103600efffd8e483c90761f90485bd1bec85"
DOWNLOAD = "https://github.com/anchore/grype/releases/download/v" + VERSION + "/" + ARCHIVE
IMAGE = "ghcr.io/rovxbot/security-receiver@"
ALLOWED_IMAGES = frozenset({IMAGE, "ghcr.io/rovxbot/kubernetes-mcp@", "ghcr.io/rovxbot/kubevuln@"})
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")


def require(condition):
    if not condition:
        raise ValueError("scan_contract")


def verify_asset(data):
    require(0 < len(data) <= 128*1024**2)
    require("sha256:"+hashlib.sha256(data).hexdigest() == ASSET_DIGEST)


def fetch(url, limit):
    request = Request(url, headers={"User-Agent":"homelab-image-scan",
                                    "Accept-Encoding":"identity"})
    with urlopen(request,timeout=60) as response:
        require(response.geturl().startswith("https://"))
        body=response.read(limit+1)
    require(len(body) <= limit)
    return body


def install(directory):
    data=fetch(DOWNLOAD,128*1024**2)
    verify_asset(data)
    archive=directory/"grype.tgz"
    archive.write_bytes(data)
    binary=directory/"grype"
    with tarfile.open(archive,"r:gz") as tar:
        member=tar.getmember("grype")
        require(member.isfile() and 0 < member.size <= 256*1024**2)
        binary.write_bytes(tar.extractfile(member).read())
    binary.chmod(0o500)
    return binary,ASSET_DIGEST


def provenance(report,digest,source_sha,asset_digest,now,image=IMAGE):
    require(image in ALLOWED_IMAGES)
    require(DIGEST.fullmatch(digest) and SHA.fullmatch(source_sha) and DIGEST.fullmatch(asset_digest))
    require(isinstance(report["matches"],list))
    descriptor=report["descriptor"]
    require(descriptor["name"]=="grype" and descriptor["version"]==VERSION)
    # Grype v0.121.3 root.dbInfo nests ProviderStatus under status.
    # Reject the obsolete flat database descriptor rather than guessing.
    db=descriptor["db"]["status"]
    require(db["valid"] is True and not db.get("error"))
    built=datetime.fromisoformat(db["built"].replace("Z","+00:00"))
    require(timedelta(0) <= now-built <= timedelta(hours=72))
    require(report["source"]["type"] == "image")
    target=report["source"]["target"]
    require(target["userInput"] in {image+digest,"registry:"+image+digest}
            and target["manifestDigest"]==digest and target["architecture"]=="amd64"
            and target["os"]=="linux")
    # Syft v1.57.1 includes the raw manifest and image config as base64.
    require("sha256:"+hashlib.sha256(base64.b64decode(target["manifest"],validate=True)).hexdigest()==digest)
    require(DIGEST.fullmatch(target["imageID"]) and "sha256:"+hashlib.sha256(base64.b64decode(target["config"],validate=True)).hexdigest()==target["imageID"])
    schema=str(db["schemaVersion"])
    require(re.fullmatch(r"v?6(?:\.[0-9]+){0,2}",schema))
    return {"schema":1,"state":"observed","mode":"report-only","image":image+digest,
            "sourceCommit":source_sha,"scanner":"grype","scannerVersion":VERSION,
            "scannerReleaseAssetDigest":asset_digest,"databaseBuilt":built.isoformat(),
            "databaseSchema":schema,"observedAt":now.isoformat(),"platform":"linux/amd64",
            "findingDetails":"not published to public CI","policyGate":"not evaluated"}


def serious_fixable(report):
    """Compare exact installed package identities, not just advisory IDs.

    A changed installed version with a remaining advisory is a candidate-only
    match for review, not a claim that the CVE itself was newly introduced.
    Reject suppressed or malformed evidence. Never emit any match identity.
    """
    require(report.get("ignoredMatches", []) == [])
    require(isinstance(report["matches"], list) and len(report["matches"]) <= 100000)
    result = set()
    for match in report["matches"]:
        vuln = match["vulnerability"]
        severity = vuln["severity"]
        require(severity in {"Unknown", "Negligible", "Low", "Medium", "High", "Critical"})
        if severity not in {"High", "Critical"}:
            continue
        fix = vuln["fix"]["state"]
        require(fix in {"fixed", "not-fixed", "wont-fix", "unknown"})
        if fix != "fixed":
            continue
        artifact = match["artifact"]
        key = (vuln["namespace"], vuln["id"], artifact["type"], artifact["name"], artifact["version"])
        require(all(isinstance(x, str) and 0 < len(x) <= 512 and not any(ord(c) < 32 for c in x) for x in key))
        result.add(key)
    return result


def baseline_review(candidate_report, baseline_report, candidate, baseline):
    require(all(candidate[k] == baseline[k] for k in
                ("scanner", "scannerVersion", "scannerReleaseAssetDigest", "databaseBuilt", "databaseSchema", "platform")))
    new = serious_fixable(candidate_report) - serious_fixable(baseline_report)
    return {"mode": "report-only", "state": "review_required" if new else "no_candidate_only_matches",
            "baselineImage": baseline["image"], "baselineSourceCommit": baseline["sourceCommit"],
            "comparison": "exact installed package identity; fixable high/critical; no ignored matches",
            "findingDetails": "not published", "policyGate": "not enforced"}


def database_fingerprint(root):
    files = list(root.rglob("vulnerability.db"))
    require(len(files) == 1 and files[0].is_file() and not files[0].is_symlink())
    require(0 < files[0].stat().st_size <= 4*1024**3)
    with files[0].open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def scan_report(binary, env, directory, image, digest, name):
    result = directory / name
    with result.open("wb") as file:
        subprocess.run([str(binary),"registry:"+image+digest,"--platform","linux/amd64","-o","json"],
                       env=env,stdout=file,stderr=subprocess.DEVNULL,timeout=300,check=True)
    require(0 < result.stat().st_size <= 128*1024**2)
    return json.loads(result.read_text())


def main():
    output=Path(".work/image-scan/provenance.json")
    output.parent.mkdir(parents=True,exist_ok=True)
    phase="input"
    try:
        image=os.environ.get("SCAN_IMAGE_REPOSITORY",IMAGE)
        require(image in ALLOWED_IMAGES)
        digest=os.environ["SCANNED_DIGEST"]
        source=os.environ["SOURCE_SHA"]
        require(DIGEST.fullmatch(digest) and SHA.fullmatch(source))
        baseline_digest = os.environ.get("SCAN_BASELINE_DIGEST", "")
        baseline_source = os.environ.get("SCAN_BASELINE_SOURCE_SHA", "")
        require(bool(baseline_digest) == bool(baseline_source))
        if baseline_digest:
            require(DIGEST.fullmatch(baseline_digest) and SHA.fullmatch(baseline_source))
        with tempfile.TemporaryDirectory(prefix="image-scan-") as tmp:
            directory=Path(tmp)
            phase="scanner_install"
            binary,asset=install(directory)
            env=dict(os.environ,GRYPE_CHECK_FOR_APP_UPDATE="false",GRYPE_DB_CACHE_DIR=str(directory/"db"),
                     GRYPE_DB_VALIDATE_AGE="true",GRYPE_DB_MAX_ALLOWED_BUILT_AGE="72h",
                     GRYPE_DB_AUTO_UPDATE="false")
            phase="database_update"
            subprocess.run([str(binary),"db","update"],env=env,stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,timeout=240,check=True)
            phase="scan"
            fingerprint = database_fingerprint(directory/"db") if baseline_digest else None
            candidate_report = scan_report(binary,env,directory,image,digest,"report.json")
            phase="provenance"
            safe=provenance(candidate_report,digest,source,asset,datetime.now(timezone.utc),image=image)
            if baseline_digest:
                phase="baseline_scan"
                require(database_fingerprint(directory/"db") == fingerprint)
                baseline_report = (candidate_report if baseline_digest == digest else
                                   scan_report(binary,env,directory,image,baseline_digest,"baseline.json"))
                require(database_fingerprint(directory/"db") == fingerprint)
                baseline = provenance(baseline_report,baseline_digest,baseline_source,asset,datetime.now(timezone.utc),image=image)
                phase="baseline_review"
                safe["baselineReview"] = baseline_review(candidate_report,baseline_report,safe,baseline)
        output.write_text(json.dumps(safe,sort_keys=True,indent=2)+"\n")
        print("Immutable image scan completed; vulnerability details withheld from public CI.")
        return 0
    except Exception as exc:
        category = type(exc).__name__
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,60}", category):
            category = "UnknownError"
        output.write_text(json.dumps({"schema":1,"state":"unknown","phase":phase,"category":category})+"\n")
        print("Image scan unavailable at phase="+phase+" category="+category)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
