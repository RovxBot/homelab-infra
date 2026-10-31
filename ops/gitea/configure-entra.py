#!/usr/bin/env python3
"""Create the dedicated single-tenant app and encrypt its credential immediately.

Uses the current Azure CLI identity as the initial, explicitly assigned admin.
Reruns preserve the app, roles and existing SOPS credential. --rotate appends a
credential; remove the previous credential only after testing a new login.
"""
import argparse
import datetime as dt
import json
import pathlib
import subprocess
import uuid

import yaml


def az(*args):
    return json.loads(subprocess.check_output(["az", *args, "-o", "json"]))


def graph(method, resource, body):
    return az("rest", "--method", method, "--url",
              "https://graph.microsoft.com/v1.0/" + resource,
              "--body", json.dumps(body))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sops", default="sops")
    parser.add_argument("--rotate", action="store_true")
    args = parser.parse_args()
    path = pathlib.Path("secrets/gitea-entra.enc.yaml")
    account = az("account", "show")
    me = az("ad", "signed-in-user", "show")
    apps = az("ad", "app", "list", "--filter", "displayName eq 'Cooked Gitea'")
    if len(apps) > 1:
        raise SystemExit("Multiple matching applications; resolve explicitly first")
    if apps:
        app = apps[0]
    else:
        roles = [{"allowedMemberTypes": ["User"], "description": description,
                  "displayName": value, "id": str(uuid.uuid4()),
                  "isEnabled": True, "value": value}
                 for value, description in [
                     ("Gitea.Admin", "Gitea administrator"),
                     ("Gitea.User", "Gitea developer")]]
        app = graph("POST", "applications", {
            "displayName": "Cooked Gitea", "signInAudience": "AzureADMyOrg",
            "appRoles": roles,
            "web": {"redirectUris": [
                "https://git.cooked.beer/user/oauth2/entra/callback"],
                "implicitGrantSettings": {"enableIdTokenIssuance": False,
                                           "enableAccessTokenIssuance": False}},
            "optionalClaims": {"idToken": [{"name": "email"}]}})
    if app["signInAudience"] != "AzureADMyOrg":
        raise SystemExit("Existing app is not single-tenant; refusing reuse")
    principals = az("ad", "sp", "list", "--filter", "appId eq '" + app["appId"] + "'")
    sp = principals[0] if principals else graph("POST", "servicePrincipals", {
        "appId": app["appId"], "appRoleAssignmentRequired": True})
    if not sp.get("appRoleAssignmentRequired"):
        graph("PATCH", "servicePrincipals/" + sp["id"],
              {"appRoleAssignmentRequired": True})
    admin = next(r for r in app["appRoles"] if r["value"] == "Gitea.Admin")
    assignments = az("rest", "--method", "GET", "--url",
                     "https://graph.microsoft.com/v1.0/servicePrincipals/" +
                     sp["id"] + "/appRoleAssignedTo")["value"]
    if not any(a["principalId"] == me["id"] and a["appRoleId"] == admin["id"]
               for a in assignments):
        graph("POST", "servicePrincipals/" + sp["id"] + "/appRoleAssignedTo",
              {"principalId": me["id"], "resourceId": sp["id"],
               "appRoleId": admin["id"]})
    if not path.exists() or args.rotate:
        end = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=365)
        credential = graph("POST", "applications/" + app["id"] + "/addPassword", {
            "passwordCredential": {"displayName": "gitea-sops",
                                   "endDateTime": end.isoformat()}})
        secret = {"apiVersion": "v1", "kind": "Secret",
                  "metadata": {"name": "gitea-entra", "namespace": "gitea"},
                  "type": "Opaque", "stringData": {
                      "key": app["appId"], "secret": credential["secretText"]}}
        encrypted = subprocess.check_output([
            args.sops, "--encrypt", "--input-type", "yaml", "--output-type", "yaml",
            "--filename-override", str(path), "/dev/stdin"],
            input=yaml.safe_dump(secret, sort_keys=False).encode())
        path.write_bytes(encrypted)
        print("Credential encrypted; expires", credential["endDateTime"],
              "key ID", credential["keyId"])
    print(json.dumps({"tenant": account["tenantId"], "applicationId": app["appId"],
                      "objectId": app["id"], "servicePrincipal": sp["id"],
                      "initialAdmin": me["userPrincipalName"]}, indent=2))


if __name__ == "__main__":
    main()
