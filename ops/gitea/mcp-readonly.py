#!/usr/bin/env python3
"""Launch the pinned Gitea MCP server with read tools and a filtered environment."""
import os
import shutil

binary = os.environ.get("GITEA_MCP_BINARY") or shutil.which("gitea-mcp-1.12.0")
if not binary:
    raise SystemExit("Install and verify gitea-mcp 1.12.0 first")
if not os.environ.get("GITEA_ACCESS_TOKEN"):
    raise SystemExit("Supply a read-only Gitea token through the environment")
environment = {name: os.environ[name] for name in [
    "PATH", "HOME", "GITEA_ACCESS_TOKEN", "SSL_CERT_FILE", "SSL_CERT_DIR"]
    if name in os.environ}
environment["GITEA_HOST"] = os.environ.get("GITEA_HOST", "https://git.cooked.beer")
tools = "repo_read,get_repository_tree,list_issues,issue_read,list_pull_requests,pull_request_read,branch_read,actions_run_read"
os.execve(binary, [binary, "-t", "stdio", "-r", "-O", tools], environment)
