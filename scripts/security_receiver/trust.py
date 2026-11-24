"""Trusted Kubernetes identities for the Kubescape native HTTP receiver.

TokenReview and independently retrieved Pod/ReplicaSet/Deployment ownership are
the sole authorities. Event fields cannot authenticate the sender or workload.
No Secrets API, Kubernetes writes, Pod logs, nodes/proxy or admission mutation.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, build_opener, ProxyHandler, HTTPSHandler, HTTPRedirectHandler

from .native import (AGENT_PIN, AGENT_SA, DIGEST, IDENTITY, Rejected,
                     TrustedPod, VerifiedSource, decode, require)

AGENT_IMAGE = "quay.io/kubescape/node-agent:" + AGENT_PIN
AGENT_PLATFORM_DIGEST = "sha256:bd0e305a2e59e443722e8cf61bbd89251d4275c6c615b00bf5096118471129b0"
NAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")
TOKEN_REVIEW_PATH = "/apis/authentication.k8s.io/v1/tokenreviews"
TOKEN_AUDIENCE = "kubescape.io"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Rejected("api_redirect_refused")


def owner(obj, kind, uid):
    refs = [r for r in obj.get("metadata", {}).get("ownerReferences", ())
            if r.get("controller") is True]
    return len(refs) == 1 and refs[0].get("kind") == kind and refs[0].get("uid") == uid


def one(values, name):
    matches = [v for v in values if v.get("name") == name]
    require(len(matches) == 1, "inventory_ambiguous")
    return matches[0]


def named(name):
    require(isinstance(name, str) and bool(NAME.fullmatch(name)), "name_invalid")
    return quote(name, safe="")


def namespace_resource(namespace, resource, name):
    require(resource in ("pods", "replicasets", "deployments", "daemonsets"),
            "resource_invalid")
    api = "/api/v1" if resource == "pods" else "/apis/apps/v1"
    return f"{api}/namespaces/{named(namespace)}/{resource}/{named(name)}"


def running_status(pod, name):
    require(pod.get("status", {}).get("phase") == "Running"
            and pod.get("metadata", {}).get("deletionTimestamp") is None,
            "pod_not_running")
    statuses = pod["status"].get("containerStatuses") or []
    item = one(statuses, name)
    require(item.get("ready") is True and "running" in item.get("state", {})
            and bool(item.get("containerID")), "container_not_ready")
    return item


class KubernetesTrust:
    def __init__(self, origin, token_path, ca_path, *, namespaces, nodes):
        require(origin == "https://kubernetes.default.svc", "api_origin_invalid")
        self.origin = origin
        self.token_path, self.ca_path = Path(token_path), Path(ca_path)
        self.namespaces, self.nodes = frozenset(namespaces), frozenset(nodes)
        require(bool(self.namespaces) and bool(self.nodes)
                and all(NAME.fullmatch(v) for v in self.namespaces | self.nodes),
                "scope_invalid")
        context = ssl.create_default_context(cafile=str(self.ca_path))
        self.opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=context))

    def request(self, path, *, body=None):
        require(path.startswith(("/api/v1/", "/apis/apps/v1/", TOKEN_REVIEW_PATH))
                and not path.startswith("//") and ".." not in path,
                "api_path_invalid")
        try:
            with self.token_path.open("rb") as f:
                service_token = f.read(16385).decode("ascii").strip()
            require(0 < len(service_token) <= 16384
                    and "\n" not in service_token and "\r" not in service_token,
                    "service_token_invalid")
            headers = {"Authorization": "Bearer " + service_token,
                       "Accept": "application/json", "Accept-Encoding": "identity"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            request = Request(self.origin + path, data=body, headers=headers,
                              method="POST" if body is not None else "GET")
            with self.opener.open(request, timeout=8) as response:
                require(response.status in (200, 201)
                        and response.headers.get("Content-Encoding", "identity") == "identity",
                        "api_response_invalid")
                value = response.read(512 * 1024 + 1)
            return decode(value)
        except (HTTPError, URLError, OSError, UnicodeError, ValueError, ssl.SSLError) as exc:
            if isinstance(exc, Rejected):
                raise
            raise Rejected("kubernetes_unavailable") from None

    def get(self, namespace, kind, name):
        return self.request(namespace_resource(namespace, kind, name))

    def authenticate(self, bearer):
        """Return only a Kubernetes-attested, bound node-agent Pod identity."""
        require(isinstance(bearer, str) and 1 <= len(bearer) <= 8192
                and "\r" not in bearer and "\n" not in bearer,
                "bearer_invalid")
        review = self.request(TOKEN_REVIEW_PATH, body=json.dumps(
            {"apiVersion":"authentication.k8s.io/v1", "kind":"TokenReview",
             "spec":{"token":bearer, "audiences":[TOKEN_AUDIENCE]}}).encode())
        status = review.get("status") or {}
        user = status.get("user") or {}
        require(status.get("authenticated") is True
                and TOKEN_AUDIENCE in (status.get("audiences") or ())
                and user.get("username") == AGENT_SA,
                "unauthenticated_source")
        extra = user.get("extra") or {}
        def bound(field):
            values = extra.get(field)
            require(isinstance(values, list) and len(values) == 1
                    and isinstance(values[0], str) and bool(values[0]),
                    "bound_identity_missing")
            return values[0]
        pod_name = bound("authentication.kubernetes.io/pod-name")
        pod_uid = bound("authentication.kubernetes.io/pod-uid")
        pod = self.get("kubescape", "pods", pod_name)
        require(pod.get("metadata", {}).get("uid") == pod_uid
                and pod.get("metadata", {}).get("namespace") == "kubescape"
                and pod.get("spec", {}).get("serviceAccountName") == "node-agent",
                "agent_identity_invalid")
        require(owner(pod, "DaemonSet", (self.get("kubescape", "daemonsets",
                                                 "node-agent")).get("metadata", {}).get("uid")),
                "agent_owner_invalid")
        ds = self.get("kubescape", "daemonsets", "node-agent")
        require(ds.get("spec", {}).get("template", {}).get("spec", {})
                .get("serviceAccountName") == "node-agent"
                and one(ds["spec"]["template"]["spec"]["containers"],
                        "node-agent").get("image") == AGENT_IMAGE,
                "daemonset_identity_invalid")
        node = pod.get("spec", {}).get("nodeName")
        require(node in self.nodes and bool(IDENTITY.fullmatch(pod_uid)),
                "source_node_invalid")
        spec_container = one(pod["spec"]["containers"], "node-agent")
        require(spec_container.get("image") == AGENT_IMAGE,
                "agent_image_invalid")
        state = running_status(pod, "node-agent")
        image_id = state.get("imageID") or ""
        require(isinstance(image_id, str) and image_id.rsplit("@", 1)[-1]
                in (AGENT_IMAGE.rsplit("@", 1)[-1], AGENT_PLATFORM_DIGEST),
                "agent_image_invalid")
        return VerifiedSource(AGENT_SA, pod_uid, node)

    def inventory(self, source, alert):
        """Read the live Pod -> ReplicaSet -> Deployment chain independently."""
        require(type(source) is VerifiedSource and isinstance(alert, dict),
                "trusted_context_required")
        namespace, name, container = (alert.get("namespace"),
                                       alert.get("podName"),
                                       alert.get("containerName"))
        require(namespace in self.namespaces and isinstance(name, str)
                and isinstance(container, str),
                "workload_out_of_scope")
        pod = self.get(namespace, "pods", name)
        require(pod.get("metadata", {}).get("namespace") == namespace
                and pod.get("metadata", {}).get("name") == name
                and pod.get("spec", {}).get("nodeName") == source.node,
                "pod_identity_invalid")
        state = running_status(pod, container)
        ref = next((r for r in pod.get("metadata", {}).get("ownerReferences", ())
                    if r.get("controller") is True), None)
        require(ref is not None and ref.get("kind") == "ReplicaSet",
                "workload_owner_invalid")
        rs = self.get(namespace, "replicasets", ref.get("name"))
        require(rs.get("metadata", {}).get("uid") == ref.get("uid"),
                "replicaset_identity_invalid")
        controllers = [r for r in rs.get("metadata", {}).get("ownerReferences", ())
                       if r.get("controller") is True]
        require(len(controllers) == 1 and controllers[0].get("kind") == "Deployment",
                "deployment_owner_invalid")
        deployment = self.get(namespace, "deployments", controllers[0].get("name"))
        depmeta = deployment.get("metadata") or {}
        require(depmeta.get("uid") == controllers[0].get("uid")
                and not depmeta.get("deletionTimestamp")
                and owner(rs, "Deployment", depmeta.get("uid")),
                "deployment_identity_invalid")
        spec_image = one(pod["spec"]["containers"], container).get("image")
        template_image = one(deployment["spec"]["template"]["spec"]["containers"],
                             container).get("image")
        require(spec_image == template_image, "image_spec_mismatch")
        ref = state.get("imageID") or ""
        digest = ref.rsplit("@", 1)[-1]
        require(bool(DIGEST.fullmatch(digest)), "running_image_unverifiable")
        started_at = state["state"]["running"]["startedAt"]
        return TrustedPod("home", namespace, depmeta["name"], depmeta["uid"],
                          pod["metadata"]["name"], pod["metadata"]["uid"],
                          container, state["containerID"], source.node,
                          started_at, digest)
