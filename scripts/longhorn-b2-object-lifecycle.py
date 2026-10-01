#!/usr/bin/env python3
"""Review or apply B2 backup policies for superseded object versions only."""

import argparse
import base64
import json
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request


class UnsafeLifecycle(RuntimeError):
    pass


PREFIXES = {"cooked-k8s": "longhorn/", "cooked-photos": "restic/immich-uploads/"}


def desired_rules(bucket, policy):
    rules = policy["lifecycleRules"]
    prefix = PREFIXES.get(policy.get("bucketName"))
    safe = {"fileNamePrefix": prefix, "daysFromHidingToDeleting": 1,
            "daysFromUploadingToHiding": None}
    if not prefix or bucket.get("bucketName") != policy.get("bucketName") or rules != [safe]:
        raise UnsafeLifecycle("Only the reviewed backup-prefix superseded-version rules are supported")
    existing = bucket.get("lifecycleRules", [])
    matched = []
    for rule in existing:
        existing_prefix = rule["fileNamePrefix"]
        if existing_prefix.startswith(prefix) or prefix.startswith(existing_prefix):
            equivalent = all(rule.get(k) == v for k, v in safe.items()) and rule.get("daysFromStartingToCancelingUnfinishedLargeFiles") is None
            if not equivalent:
                raise UnsafeLifecycle("An overlapping lifecycle rule requires separate review")
            matched.append(rule)
    if len(matched) > 1:
        raise UnsafeLifecycle("Multiple overlapping lifecycle rules require separate review")
    return existing if matched else existing + rules


def update_payload(bucket, policy, expected_revision):
    if expected_revision != bucket["revision"]:
        raise UnsafeLifecycle("Bucket revision changed; review its current rules again")
    return {"bucketId": bucket["bucketId"], "ifRevisionIs": expected_revision,
            "lifecycleRules": desired_rules(bucket, policy)}


def request_json(url, headers, payload=None):
    request = urllib.request.Request(url, headers=headers,
                                     data=None if payload is None else json.dumps(payload).encode())
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def kube(resource, name, namespace="longhorn-system"):
    output = subprocess.check_output(["kubectl", "--request-timeout=20s", "-n", namespace,
                                      "get", resource, name, "-o", "json"], text=True, timeout=45)
    return json.loads(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", choices=sorted(PREFIXES), default="cooked-k8s")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-revision", type=int)
    args = parser.parse_args()
    if args.apply and args.expected_revision is None:
        raise UnsafeLifecycle("Apply requires the bucket revision from a reviewed dry run")
    policy_path = Path(__file__).resolve().parents[1] / "ops/longhorn/b2-object-lifecycle.json"
    policy = next(p for p in json.loads(policy_path.read_text())["buckets"] if p["bucketName"] == args.bucket)
    if args.bucket == "cooked-k8s":
        target = kube("backuptargets.longhorn.io", "default")
        if target["spec"]["backupTargetURL"] != "s3://cooked-k8s@us-east-005/longhorn" or target["spec"]["credentialSecret"] != "longhorn-b2-credentials":
            raise UnsafeLifecycle("The configured Longhorn backup destination changed")
        namespace, secret_name = "longhorn-system", "longhorn-b2-credentials"
    else:
        job = kube("cronjob", "restic-immich-photos", "immich")
        containers = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
        if len(containers) != 1:
            raise UnsafeLifecycle("The photo backup container layout changed")
        env = {e["name"]: e for e in containers[0]["env"]}
        if env["RESTIC_REPOSITORY"].get("value") != "s3:https://s3.us-east-005.backblazeb2.com/cooked-photos/restic/immich-uploads" or any(env[key].get("valueFrom", {}).get("secretKeyRef", {}).get("name") != "restic-b2-photos" for key in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]):
            raise UnsafeLifecycle("The configured photo backup destination or credentials changed")
        namespace, secret_name = "immich", "restic-b2-photos"
    # Keep credentials and authorization tokens in memory; never print or persist them.
    secret = kube("secret", secret_name, namespace)["data"]
    key_id = base64.b64decode(secret["AWS_ACCESS_KEY_ID"]).decode()
    key = base64.b64decode(secret["AWS_SECRET_ACCESS_KEY"]).decode()
    basic = base64.b64encode((key_id + ":" + key).encode()).decode()
    auth = request_json("https://api.backblazeb2.com/b2api/v4/b2_authorize_account",
                        {"Authorization": "Basic " + basic})
    storage = auth["apiInfo"]["storageApi"]
    parsed = urllib.parse.urlparse(storage["apiUrl"])
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".backblazeb2.com"):
        raise UnsafeLifecycle("Unexpected B2 API endpoint")
    headers = {"Authorization": auth["authorizationToken"], "Content-Type": "application/json"}
    buckets = request_json(storage["apiUrl"] + "/b2api/v4/b2_list_buckets", headers,
                           {"accountId": auth["accountId"], "bucketName": policy["bucketName"]})["buckets"]
    if len(buckets) != 1:
        raise UnsafeLifecycle("Expected exactly one matching backup bucket")
    bucket = buckets[0]
    rules = desired_rules(bucket, policy)
    print(json.dumps({"bucketName": bucket["bucketName"], "revision": bucket["revision"],
                      "currentRules": bucket["lifecycleRules"], "desiredRules": rules}, indent=2), flush=True)
    if not args.apply:
        print("Dry run: no changes made. Current objects are never expired by this rule.")
        return
    payload = update_payload(bucket, policy, args.expected_revision)
    if rules == bucket["lifecycleRules"]:
        print("The reviewed rule is already installed; no write needed.")
        return
    if "writeBuckets" not in storage["allowed"]["capabilities"]:
        raise UnsafeLifecycle("The application key cannot update bucket lifecycle rules")
    payload["accountId"] = auth["accountId"]
    updated = request_json(storage["apiUrl"] + "/b2api/v4/b2_update_bucket", headers, payload)
    if updated["bucketId"] != bucket["bucketId"] or updated["bucketName"] != bucket["bucketName"] or desired_rules(updated, policy) != updated["lifecycleRules"]:
        raise UnsafeLifecycle("The returned bucket identity or lifecycle policy does not match")
    print(f"Verified lifecycle update at revision {updated['revision']}: newest object versions preserved.")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        print(f"STOP: B2 API request failed with HTTP {exc.code}", file=sys.stderr)
        sys.exit(1)
    except (UnsafeLifecycle, KeyError, ValueError, StopIteration, OSError, subprocess.SubprocessError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        sys.exit(1)
