#!/usr/bin/env python3
"""Serve only an ephemeral fixture repository through Git's smart HTTP backend."""
import argparse
import http.server
import os
import subprocess
import urllib.parse


class GitHandler(http.server.BaseHTTPRequestHandler):
    def execute(self):
        url = urllib.parse.urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2 * 1024 * 1024:
            self.send_error(413)
            return
        result = subprocess.run(
            ["git", "http-backend"],
            input=self.rfile.read(length),
            capture_output=True,
            timeout=30,
            env={
                "PATH": os.environ["PATH"],
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_PROJECT_ROOT": self.server.project_root,
                "GIT_HTTP_EXPORT_ALL": "1",
                "REQUEST_METHOD": self.command,
                "PATH_INFO": url.path,
                "QUERY_STRING": url.query,
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": str(length),
                "HTTP_CONTENT_ENCODING": self.headers.get("Content-Encoding", ""),
                "SERVER_PROTOCOL": "HTTP/1.1",
            },
        )
        headers, _, body = result.stdout.partition(b"\r\n\r\n")
        values = [
            line.split(b":", 1) for line in headers.split(b"\r\n") if b":" in line
        ]
        status = next(
            (int(v.strip().split()[0]) for k, v in values if k.lower() == b"status"),
            200,
        )
        self.send_response(status)
        for key, value in values:
            if key.lower() != b"status":
                self.send_header(key.decode(), value.decode().strip())
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = execute
    do_POST = execute


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", required=True)
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--directory", required=True)
    args = parser.parse_args()
    server = http.server.ThreadingHTTPServer((args.bind, args.port), GitHandler)
    server.project_root = os.path.abspath(args.directory)
    server.serve_forever()


if __name__ == "__main__":
    main()
