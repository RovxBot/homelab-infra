#!/usr/bin/env python3
"""Immutable receiver image scan. Publish provenance, never raw findings."""
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


def provenance(report,digest,source_sha,asset_digest,now):
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
    require(target["userInput"] in {IMAGE+digest,"registry:"+IMAGE+digest}
            and target["manifestDigest"]==digest and target["architecture"]=="amd64"
            and target["os"]=="linux")
    # Syft v1.57.1 includes the raw manifest and image config as base64.
    require("sha256:"+hashlib.sha256(base64.b64decode(target["manifest"],validate=True)).hexdigest()==digest)
    require(DIGEST.fullmatch(target["imageID"]) and "sha256:"+hashlib.sha256(base64.b64decode(target["config"],validate=True)).hexdigest()==target["imageID"])
    schema=str(db["schemaVersion"])
    require(re.fullmatch(r"v?6(?:\.[0-9]+){0,2}",schema))
    return {"schema":1,"state":"observed","mode":"report-only","image":IMAGE+digest,
            "sourceCommit":source_sha,"scanner":"grype","scannerVersion":VERSION,
            "scannerReleaseAssetDigest":asset_digest,"databaseBuilt":built.isoformat(),
            "databaseSchema":schema,"observedAt":now.isoformat(),"platform":"linux/amd64",
            "findingDetails":"not published to public CI","policyGate":"not evaluated"}


def main():
    output=Path(".work/receiver-scan/provenance.json")
    output.parent.mkdir(parents=True,exist_ok=True)
    phase="input"
    try:
        digest=os.environ["SCANNED_DIGEST"]
        source=os.environ["SOURCE_SHA"]
        require(DIGEST.fullmatch(digest) and SHA.fullmatch(source))
        with tempfile.TemporaryDirectory(prefix="receiver-scan-") as tmp:
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
            result=directory/"report.json"
            with result.open("wb") as file:
                subprocess.run([str(binary),"registry:"+IMAGE+digest,"--platform","linux/amd64","-o","json"],
                               env=env,stdout=file,stderr=subprocess.DEVNULL,timeout=300,check=True)
            require(result.stat().st_size <= 128*1024**2)
            phase="provenance"
            safe=provenance(json.loads(result.read_text()),digest,source,asset,datetime.now(timezone.utc))
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
