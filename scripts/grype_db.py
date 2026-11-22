#!/usr/bin/env python3
"""Verify official Grype snapshots and prepare a narrowly scoped GitOps refresh.

Only reads the registry/publisher and writes local files. Never contacts a cluster.
Requires zstandard for verification and PyYAML for GitOps preparation.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tarfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "quay.io/kubescape/grype-offline-db"
REGISTRY = "https://quay.io/v2/kubescape/grype-offline-db/"
PUBLISHER = "https://grype.anchore.io/databases/v6/latest.json"
PUBLISHERS = (PUBLISHER, "https://toolbox-data.anchore.io/grype/databases/v6/latest.json")
MAX_AGE_HOURS = 72  # New proposals must still be recent.
DATABASE_FRESH_HOURS = 7 * 24
DATABASE_MAXIMUM_HOURS = 14 * 24
SCHEMAS = {"v6.1.10"}  # Qualified with Grype 0.121.0. Changes require review.
MARGIN = 512 * 1024**2
MAX_DATABASE = 5 * 1024**3
ACCEPT = ", ".join(("application/vnd.oci.image.index.v1+json",
                    "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.oci.image.manifest.v1+json",
                    "application/vnd.docker.distribution.manifest.v2+json"))


class Rejected(ValueError):
    def __init__(self, reason, detail=""):
        self.reason = reason
        super().__init__(reason + (": " + detail if detail else ""))


def require(condition, reason, detail=""):
    if not condition:
        raise Rejected(reason, detail)


def utcnow():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(parsed.utcoffset() is not None, "invalid_timestamp", str(value))
        return parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError) as error:
        raise Rejected("invalid_timestamp", str(value)) from error


def age_hours(built, now=None):
    age = ((now or utcnow()) - timestamp(built)).total_seconds() / 3600
    require(age >= 0, "future_timestamp", built)
    return age


def digest_bytes(content):
    return "sha256:" + hashlib.sha256(content).hexdigest()


def digest_file(path):
    with Path(path).open("rb") as source:
        return "sha256:" + hashlib.file_digest(source, "sha256").hexdigest()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def metadata(document, now=None):
    require(document.get("status") == "active", "inactive_database")
    require(document.get("schemaVersion") in SCHEMAS, "incompatible_schema")
    require(re.fullmatch(r"sha256:[a-f0-9]{64}", document.get("checksum", "")), "invalid_checksum")
    name = document.get("path", "")
    require(bool(re.fullmatch(r"vulnerability-db_v6\.[0-9]+\.[0-9]+_[A-Za-z0-9_:.-]+\.tar\.zst", name)),
            "invalid_archive_path")
    age_hours(document.get("built"), now)  # Validate the timestamp independently of age policy.
    return document


def database_freshness(built, now=None):
    age = age_hours(built, now)
    return {"ageHours": round(age, 6),
            "state": "fresh" if age <= DATABASE_FRESH_HOURS else "grace" if age <= DATABASE_MAXIMUM_HOURS else "expired",
            "freshHours": DATABASE_FRESH_HOURS, "maximumAgeHours": DATABASE_MAXIMUM_HOURS}


def publisher_match(mirror, publisher, now=None):
    metadata(mirror, now)
    metadata(publisher, now)
    require(all(mirror[k] == publisher[k] for k in ("built", "schemaVersion", "path", "checksum")),
            "publisher_mismatch", "mirror assertions do not match independent Anchore metadata")


def get_bytes(url, limit=4 * 1024**2, accept=ACCEPT):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": accept,
                                "User-Agent": "grype-db-maintenance/1.0"}), timeout=30) as response:
        require(response.geturl().startswith("https://"), "insecure_redirect")
        body = response.read(limit + 1)
        require(len(body) <= limit, "oversized_document")
        return body, dict(response.headers)


def publisher_metadata():
    failures = []
    for url in PUBLISHERS:
        try:
            body, _ = get_bytes(url, accept="application/json")
            document = metadata(json.loads(body))
            require(age_hours(document["built"]) <= MAX_AGE_HOURS, "stale_database")
            return document, {"status": "verified", "method": "independent-publisher-https-metadata",
                              "url": url, "retrievedAt": stamp(utcnow()),
                              "documentSHA256": digest_bytes(body), "document": document,
                              "signature": "not established", "unavailableEndpoints": failures}
        except Rejected:
            raise
        except (OSError, ValueError) as error:
            failures.append({"url": url, "error": type(error).__name__, "status": getattr(error, "code", None)})
    raise Rejected("publisher_unavailable", json.dumps(failures, sort_keys=True))


def descriptor_bytes(descriptor, category, work):
    digest = descriptor.get("digest", "")
    size = descriptor.get("size", -1)
    require(re.fullmatch(r"sha256:[a-f0-9]{64}", digest), "invalid_oci_digest")
    require(isinstance(size, int) and 0 <= size <= 1024**3, "oversized_oci_blob")
    path = work / digest.replace(":", "-")
    if not path.exists():
        temporary = path.with_suffix(".partial")
        try:
            with urllib.request.urlopen(urllib.request.Request(REGISTRY + category + "/" + digest,
                                        headers={"Accept": ACCEPT}), timeout=60) as response, temporary.open("wb") as output:
                require(response.geturl().startswith("https://"), "insecure_redirect")
                copied = 0
                while chunk := response.read(1024**2):
                    copied += len(chunk)
                    require(copied <= size, "oci_size_mismatch")
                    output.write(chunk)
            require(temporary.stat().st_size == size and digest_file(temporary) == digest, "oci_digest_mismatch")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    require(path.stat().st_size == size and digest_file(path) == digest, "oci_digest_mismatch")
    return path


def inspect_sqlite(path):
    try:
        with sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True) as connection:
            require(connection.execute("PRAGMA quick_check").fetchall() == [("ok",)], "sqlite_integrity_failed")
            rows = connection.execute("SELECT build_timestamp, model, revision, addition FROM db_metadata").fetchall()
        require(len(rows) == 1, "invalid_database_metadata")
        built, model, revision, addition = rows[0]
        schema = f"v{model}.{revision}.{addition}"
        require(schema in SCHEMAS, "incompatible_schema", schema)
        return {"built": stamp(timestamp(built)), "schemaVersion": schema,
                "bytes": Path(path).stat().st_size, "sha256": digest_file(path), "quickCheck": "ok"}
    except sqlite3.Error as error:
        raise Rejected("sqlite_integrity_failed", str(error)) from error


def inspect_archive(archive, latest, work):
    import zstandard

    require(digest_file(archive) == latest["checksum"], "archive_checksum_mismatch")
    target = work / "vulnerability.db"
    found = False
    with Path(archive).open("rb") as compressed, zstandard.ZstdDecompressor().stream_reader(compressed) as stream:
        with tarfile.open(fileobj=stream, mode="r|") as contents:
            for entry in contents:
                name = PurePosixPath(entry.name)
                require(not name.is_absolute() and ".." not in name.parts, "unsafe_archive_member")
                if entry.isdir():
                    continue
                require(entry.isfile() and name.as_posix() == "vulnerability.db" and not found,
                        "unexpected_archive_member", entry.name)
                require(0 < entry.size <= MAX_DATABASE, "oversized_database")
                require(shutil.disk_usage(work).free >= entry.size + MARGIN, "insufficient_verifier_storage")
                with contents.extractfile(entry) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                found = True
    require(found, "missing_database")
    result = inspect_sqlite(target)
    require(result["built"] == latest["built"] and result["schemaVersion"] == latest["schemaVersion"],
            "archive_metadata_mismatch")
    result["archiveBytes"] = Path(archive).stat().st_size
    return result


def storage_budget(database_bytes, archive_bytes, available=None):
    # Old hydrated DB is already charged to used space. Allow 1.5x new raw DB
    # for hydration, another 0.5x for SQLite/index temporaries, two compressed
    # archive copies and a fixed margin. Never subtract the old DB early.
    required = 2 * database_bytes + 2 * archive_bytes + MARGIN
    result = {"requiredFreeBytes": required, "databaseBytes": database_bytes,
              "hydratedBudgetBytes": (3 * database_bytes + 1) // 2,
              "sqliteTemporaryBudgetBytes": (database_bytes + 1) // 2,
              "archiveTemporaryBudgetBytes": 2 * archive_bytes, "marginBytes": MARGIN}
    if available is not None:
        result["availableBytes"] = available
        require(available >= required, "insufficient_storage", f"need {required}, have {available}")
    return result


def mirror_init(image):
    return {"name": "wait-for-verified-mirror", "image": image,
            "command": ["sh", "-ec", "for attempt in $(seq 1 60); do "
                        "if wget -T 5 -q -O /tmp/grype-mirror-latest.json "
                        "http://grype-offline-db:80/databases/v6/latest.json && "
                        "cmp -s /tmp/grype-mirror-latest.json /www/databases/v6/latest.json; "
                        "then exit 0; fi; sleep 2; done; exit 1"],
            "resources": {"requests": {"cpu": "10m", "memory": "32Mi"},
                          "limits": {"cpu": "100m", "memory": "128Mi"}},
            "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                "runAsNonRoot": True, "capabilities": {"drop": ["ALL"]}},
            "volumeMounts": [{"name": "tmp-dir", "mountPath": "/tmp"}]}


def verify_image(reference, work):
    """Resolve discovery tag once; everything below it is fetched by digest."""
    work.mkdir(parents=True, exist_ok=True)
    require(reference == "v6-latest" or re.fullmatch(r"sha256:[a-f0-9]{64}", reference), "invalid_image_reference")
    body, headers = get_bytes(REGISTRY + "manifests/" + reference)
    digest = digest_bytes(body)
    if reference.startswith("sha256:"):
        require(digest == reference, "index_digest_mismatch")
    declared = next((v for k, v in headers.items() if k.lower() == "docker-content-digest"), None)
    require(declared == digest, "index_digest_mismatch")
    index = json.loads(body)
    require(index.get("schemaVersion") == 2 and "manifests" in index, "invalid_oci_index")
    save_json(work / "index.json", index)
    candidates = [m for m in index["manifests"] if m.get("platform") == {"architecture": "amd64", "os": "linux"}]
    require(len(candidates) == 1, "unsupported_platform", "phase qualified for linux/amd64 only")
    selected, = candidates
    manifest = json.loads(descriptor_bytes(selected, "manifests", work).read_bytes())
    require(manifest.get("schemaVersion") == 2, "invalid_platform_manifest")
    config = json.loads(descriptor_bytes(manifest["config"], "blobs", work).read_bytes())
    require((config.get("os"), config.get("architecture")) == ("linux", "amd64"), "platform_mismatch")
    latest = None
    archive_paths = {}
    for layer in manifest["layers"]:
        require(layer["mediaType"] in {"application/vnd.oci.image.layer.v1.tar+gzip",
                                     "application/vnd.docker.image.rootfs.diff.tar.gzip"}, "unsupported_layer")
        path = descriptor_bytes(layer, "blobs", work)
        with tarfile.open(path, "r:gz") as contents:
            for entry in contents:
                name = PurePosixPath(entry.name)
                require(not name.is_absolute() and ".." not in name.parts, "unsafe_layer_member")
                if not name.as_posix().startswith("www/databases/"):
                    continue
                require(entry.isdir() or entry.isfile(), "unsafe_database_layer")
                require(not name.name.startswith(".wh."), "database_whiteout")
                if entry.isdir():
                    continue
                require(name.parent.as_posix() == "www/databases/v6", "unexpected_database_path")
                if name.name == "latest.json":
                    require(latest is None and entry.size <= 16384, "ambiguous_mirror_metadata")
                    latest = metadata(json.load(contents.extractfile(entry)))
                elif name.name.endswith(".tar.zst"):
                    require(name.name not in archive_paths and entry.size <= 1024**3, "ambiguous_database_archive")
                    target = work / name.name
                    with contents.extractfile(entry) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    archive_paths[name.name] = target
                else:
                    raise Rejected("unexpected_database_path", str(name))
    require(latest is not None and set(archive_paths) == {latest["path"]}, "missing_or_ambiguous_database_archive")
    database = inspect_archive(archive_paths[latest["path"]], latest, work)
    return {"image": IMAGE + "@" + digest, "indexDigest": digest, "platform": "linux/amd64",
            "manifestDigest": selected["digest"], "configDigest": manifest["config"]["digest"],
            "layers": manifest["layers"], "metadata": latest, "database": database,
            "storage": storage_budget(database["bytes"], database["archiveBytes"]),
            "verifiedAt": stamp(utcnow()), "registryIntegrity": "verified"}


def qualify(reference, work):
    result = verify_image(reference, work)
    publisher, evidence = publisher_metadata()
    publisher_match(result["metadata"], publisher)
    require(age_hours(result["metadata"]["built"]) <= MAX_AGE_HOURS, "stale_database")
    result["publisher"] = evidence
    result["state"] = "verified"
    return result


def prepare(result, root=ROOT):
    import yaml

    publisher_match(result["metadata"], result["publisher"]["document"])
    require(result["state"] == "verified" and result["publisher"]["status"] == "verified", "unverified_provenance")
    release_path = root / "infra/kubescape/helmrelease.yaml"
    text = release_path.read_text()
    release = yaml.safe_load(text)
    previous = release["spec"]["values"]["grypeOfflineDB"]["image"]["sha"]
    lock_path = root / "infra/kubescape/grype-database.json"
    old = json.loads(lock_path.read_text()) if lock_path.exists() else {}
    if old.get("metadata"):
        require(timestamp(result["metadata"]["built"]) >= timestamp(old["metadata"]["built"]), "database_downgrade")
        if result["metadata"]["built"] == old["metadata"]["built"]:
            require(result["metadata"]["checksum"] == old["metadata"]["checksum"], "same_build_checksum_changed")
            return False
    if previous == result["indexDigest"]:
        return False
    text, count = re.subn(r"(    grypeOfflineDB:\n      enabled: true\n      image:\n        tag: \"\"\n        sha: )sha256:[a-f0-9]{64}",
                          lambda m: m[1] + result["indexDigest"], text)
    require(count == 1, "unexpected_gitops_layout")
    key = "homelab.cooked.beer/grype-index"
    if key in text:
        text, count = re.subn(r"(        homelab\.cooked\.beer/grype-index: )sha256:[a-f0-9]{64}",
                              lambda m: m[1] + result["indexDigest"], text)
        require(count == 1, "unexpected_rollout_annotation")
    else:
        text = text.replace("    kubevuln:\n      image:", "    kubevuln:\n      podAnnotations:\n        " + key + ": " + result["indexDigest"] + "\n      image:")
    text = text.replace("image: " + IMAGE + "@" + previous, "image: " + result["image"])
    actual = yaml.safe_load(text)
    expected = json.loads(json.dumps(release))
    expected["spec"]["values"]["grypeOfflineDB"]["image"]["sha"] = result["indexDigest"]
    expected["spec"]["values"]["kubevuln"].setdefault("podAnnotations", {})[key] = result["indexDigest"]
    for renderer in expected["spec"]["postRenderers"]:
        for patch in renderer["kustomize"]["patches"]:
            if patch["target"].get("kind") == "Deployment" and patch["target"].get("name") == "kubevuln":
                patch["patch"] = patch["patch"].replace("image: " + IMAGE + "@" + previous, "image: " + result["image"])
    require(actual == expected, "unrelated_gitops_change")
    result["previous"] = {"indexDigest": previous, "metadata": old.get("metadata", {})}
    # The initial unverified baseline remains explicit instead of inventing proof.
    release_path.write_text(text)
    save_json(lock_path, result)
    inventory_path = root / "infra/kubescape/rendered-inventory.json"
    if inventory_path.exists():
        inventory = json.loads(inventory_path.read_text())
        inventory.update(databaseImage=result["image"], databaseRolloutIndex=result["indexDigest"],
                         databaseStartupGate=mirror_init(result["image"]))
        save_json(inventory_path, inventory)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".work/grype-db")
    parser.add_argument("--output", type=Path, default=ROOT / ".work/grype-db/result.json")
    parser.add_argument("--reference", default="v6-latest")
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    try:
        result = qualify(args.reference, args.work_dir)
        result["changed"] = prepare(result) if args.prepare else False
        save_json(args.output, result)
        print(json.dumps({k: result[k] for k in ("state", "image", "metadata", "changed")}))
        return 0
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        result = {"state": "failed", "reason": getattr(error, "reason", "verification_error"), "detail": str(error)}
        save_json(args.output, result)
        print(json.dumps(result))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
