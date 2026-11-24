"""TLS-only native Kubescape HTTP receiver. No GitHub client or public ingress.

The source of the sender's identity is Kubernetes TokenReview for the original
node-agent projected token; the source of the workload is the Kubernetes API.
No incident exists until SQLite has atomically committed its safe receipt.
No runnable deployment manifest ships with this source-only implementation.
"""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import re
import sqlite3
import ssl

from .native import Rejected, MAX_BODY, decode, normalize, require
from .store import EventStore
from .trust import KubernetesTrust

ENDPOINT = "/v1/runtimealerts"
TOKEN = re.compile(r"Bearer ([A-Za-z0-9._~+/=-]{1,8192})\Z")


class Receiver:
    def __init__(self, trusted_api, store):
        self.api = trusted_api
        self.store = store

    def process(self, token, body, now):
        # Authentication is independent of every user-controlled JSON field.
        source = self.api.authenticate(token)
        envelope = decode(body)
        require(envelope.get("apiVersion") == "kubescape.io/v1"
                and envelope.get("kind") == "RuntimeAlerts",
                "exporter_schema_invalid")
        spec = envelope.get("spec")
        require(isinstance(spec, dict) and isinstance(spec.get("alerts"), list)
                and len(spec["alerts"]) == 1
                and isinstance(spec["alerts"][0], dict),
                "exporter_batch_unsupported")
        pod = self.api.inventory(source, spec["alerts"][0])
        safe = normalize(body, source, pod, now)
        return self.store.record(safe, now)

    def health(self):
        return self.store.health()


def make_handler(receiver):
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status, value):
            data = json.dumps(value, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/livez":
                return self.send_json(200, {"alive": True})
            if self.path in ("/readyz", "/healthz"):
                health = receiver.health()
                ready = health["database_ok"] is True
                if self.path == "/readyz":
                    return self.send_json(200 if ready else 503, {"ready": ready})
                # HTTP health means receiver/storage only, not proof that every
                # agent is exporting or that a quiet event stream is healthy.
                return self.send_json(200 if ready else 503,
                                      {"receiver_ready":ready,
                                       "delivery_coverage":"unverified",
                                       "incidents":health["incidents"],
                                       "receipts":health["receipts"]})
            self.send_json(404, {"error":"not_found"})

        def do_POST(self):
            if self.path != ENDPOINT:
                return self.send_json(404, {"error":"not_found"})
            if self.headers.get("Transfer-Encoding") is not None:
                return self.send_json(400, {"error":"invalid_request"})
            lengths = self.headers.get_all("Content-Length", [])
            auths = self.headers.get_all("Authorization", [])
            if len(lengths) != 1 or not lengths[0].isdigit():
                return self.send_json(400, {"error":"invalid_request"})
            length = int(lengths[0])
            if not 0 < length <= MAX_BODY:
                return self.send_json(413, {"error":"invalid_request"})
            if len(auths) != 1 or not TOKEN.fullmatch(auths[0]):
                return self.send_json(401, {"error":"unauthenticated"})
            ctype = self.headers.get("Content-Type")
            # node-agent v0.3.310 doesn't explicitly set Content-Type in
            # sendHTTPBody, so accept missing but not an unrelated content type.
            if ctype is not None and ctype.split(";", 1)[0].strip().lower() != "application/json":
                return self.send_json(415, {"error":"invalid_request"})
            body = self.rfile.read(length)
            if len(body) != length:
                return self.send_json(400, {"error":"invalid_request"})
            try:
                outcome = receiver.process(TOKEN.fullmatch(auths[0]).group(1),
                                           body, datetime.now(timezone.utc))
                self.send_json(200, outcome)
            except Rejected as exc:
                # Only code-owned names; never echo exception payloads or tokens.
                code = str(exc)
                if code in ("kubernetes_unavailable", "database_capacity",
                            "receipt_capacity", "incident_capacity", "event_rate_limit"):
                    self.send_json(503, {"error":"temporarily_unavailable"})
                elif code in ("unauthenticated_source", "bound_identity_missing",
                              "source_identity_invalid", "bearer_invalid",
                              "agent_identity_invalid", "agent_owner_invalid",
                              "daemonset_identity_invalid", "agent_image_invalid",
                              "source_node_invalid"):
                    self.send_json(403, {"error":"unauthenticated"})
                else:
                    self.send_json(422, {"error":"invalid_event"})
            except (OSError, ValueError, sqlite3.Error):
                self.send_json(503, {"error":"temporarily_unavailable"})

        def log_message(self, *_args):
            # The HTTP server must never log Authorization or raw paths/events.
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

    return Handler


def build_server(receiver, cert, key, *, host="0.0.0.0", port=8443):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    server = HTTPServer((host, port), make_handler(receiver))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    require(set(("namespaces","nodes","state_dir","tls_cert","tls_key")) <= set(config),
            "configuration_incomplete")
    api = KubernetesTrust("https://kubernetes.default.svc",
                          "/var/run/receiver-api/token",
                          "/var/run/receiver-api/ca.crt",
                          namespaces=config["namespaces"], nodes=config["nodes"])
    state = Path(config["state_dir"])
    require(state.is_dir() and not state.is_symlink(), "state_path_invalid")
    store = EventStore(state / "receiver.sqlite")
    try:
        receiver = Receiver(api, store)
        server = build_server(receiver, config["tls_cert"], config["tls_key"])
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            server.server_close()
    finally:
        store.close()


if __name__ == "__main__":
    main()
