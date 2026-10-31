#!/usr/bin/env python3
"""Generate Linux-only Gitea CI adaptations without editing the source project."""
import argparse
import ast
import hashlib
import json
import pathlib
import subprocess

import yaml

UPLOAD = "http://gitea-http.gitea.svc.cluster.local:3000/actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
DOWNLOAD = "http://gitea-http.gitea.svc.cluster.local:3000/actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093"
CHECKOUT = r'''python3 - <<'PY'
import base64, os, subprocess, pathlib
base = 'http://gitea-http.gitea.svc.cluster.local:3000/'
env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_CONFIG_COUNT='1',
           GIT_CONFIG_KEY_0='http.' + base + '.extraHeader',
           GIT_CONFIG_VALUE_0='Authorization: Basic ' + base64.b64encode(
               ('mirror-bot:' + os.environ['SOURCE_TOKEN']).encode()).decode())
for name, ref, destination in SOURCES:
    subprocess.run(['git', 'clone', '--no-checkout', base + 'github-dr/' + name + '.git', destination], env=env, check=True)
    subprocess.run(['git', '-C', destination, 'checkout', '--detach', ref], env=env, check=True)
# LOCAL_TOOLCHAIN
PY'''


def checkout(sources):
    script = CHECKOUT.replace("for name, ref, destination in SOURCES:",
                              "for name, ref, destination in " + repr(sources) + ":")
    toolchain = (pathlib.Path(__file__).parent / 'images/recovery-toolchain.cmake').read_text()
    script = script.replace('# LOCAL_TOOLCHAIN',
        "toolchain = pathlib.Path.cwd() / 'recovery-toolchain.cmake'\n"
        "toolchain.write_text(" + repr(toolchain) + ")\n"
        "with open(os.environ['GITHUB_ENV'], 'a') as stream:\n"
        "    stream.write('CMAKE_TOOLCHAIN_FILE=' + str(toolchain) + '\\n')")
    return {"name": "Fetch verified local sources", "env": {
        "SOURCE_TOKEN": "${{ secrets.SOURCE_TOKEN }}"}, "run": script}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    output = args.output / ".gitea/workflows"
    output.mkdir(parents=True, exist_ok=True)
    inventory, report, jobs = {}, [], {}
    for path in sorted((args.source / "Tools/Porting").glob("*")):
        if not path.is_file(): continue
        text = path.read_text()
        if path.suffix == ".py": ast.parse(text)
        inventory[str(path.relative_to(args.source))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for path in sorted((args.source / ".github/workflows").glob("*.yml")):
        workflow = yaml.safe_load(path.read_text())
        entry = {"workflow": path.name, "jobs": []}
        for key, job in workflow["jobs"].items():
            matrix = job.get("strategy", {}).get("matrix", {})
            include = matrix.get("include", [])
            platforms = sorted({r["runner"] for r in include}) if include else [job["runs-on"]]
            entry["jobs"].append({"name": key, "platforms": platforms,
                                  "matrix": json.loads(json.dumps(matrix)), "timeout": job.get("timeout-minutes"),
                                  "verified_execution": "pending"})
            if include:
                linux = [row for row in include if row["runner"] == "ubuntu-24.04"]
                if not linux: continue
                job["strategy"]["matrix"]["include"] = linux
            elif job["runs-on"] != "ubuntu-24.04": continue
            job["runs-on"] = ["ubuntu-24.04", "dr-offline"]
            if 'asan' in json.dumps(matrix):
                job['runs-on'].append("${{ contains(matrix.preset, 'asan') && 'dr-sanitizer' || 'dr-offline' }}")
            job["env"] = job.get("env", {}) | {
                "GITHUB_ACTIONS": "true", "CMAKE_BUILD_PARALLEL_LEVEL": "2",
                "ASAN_OPTIONS": "detect_leaks=1:halt_on_error=1",
                "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1",
                "LIBGL_ALWAYS_SOFTWARE": "1"}
            sources = [("wowdecomp", "origin/main", "product")]
            is_audit = path.name == "m0-audit.yml"
            if is_audit:
                sources.append(("wowdecomp15595", "959b0b85c5f55c565328d7c3ca33027a65e41327", "reference-15595"))
            steps = [{"name": "Prove GitHub is unavailable in this job", "run":
                      "python3 -c \"import socket; assert socket.gethostbyname('github.com') == '127.0.0.2'\"\n"
                      "if curl --connect-timeout 2 --max-time 4 -fsS https://github.com/; then exit 1; fi\n"}, checkout(sources)]
            for step in job["steps"]:
                uses = step.get("uses", "")
                if uses.startswith("actions/checkout@"): continue
                if "sudo apt-get" in step.get("run", ""):
                    # Dependencies are already installed in the immutable image.
                    # The original compilation and runtime assertions remain intact.
                    continue
                if uses.startswith("actions/upload-artifact@"):
                    step["uses"] = UPLOAD
                    location = step["with"]["path"]
                    if not is_audit: step["with"]["path"] = "product/" + location
                elif "run" in step and not is_audit:
                    step["working-directory"] = "product"
                steps.append(step)
            job["steps"] = steps
            jobs[path.stem.replace("-", "_") + "_" + key.replace("-", "_")] = job
        report.append(entry)
    smoke = {"name": "Local recovery smoke", "on": {"workflow_dispatch": {}, "push": {"branches": ["main"]}},
             "jobs": {"core": json.loads(json.dumps(jobs["m1_core_core"]))}}
    smoke["jobs"]["core"]["strategy"]["matrix"]["include"] = [
        {"runner": "ubuntu-24.04", "preset": "linux-core", "python": "python3"},
        {"runner": "ubuntu-24.04", "preset": "linux-core-asan", "python": "python3"}]
    full = {"name": "All available Linux recovery checks", "on": {"workflow_dispatch": {}},
            "concurrency": {"group": "local-linux-full", "cancel-in-progress": True}, "jobs": jobs}
    (output / "smoke.yaml").write_text(yaml.safe_dump(smoke, sort_keys=False))
    (output / "linux-full.yaml").write_text(yaml.safe_dump(full, sort_keys=False))
    platform = {'name': 'Local artifact round trip', 'on': {'workflow_dispatch': {}},
                'jobs': {'artifact': {'runs-on': ['ubuntu-24.04', 'dr-offline'], 'timeout-minutes': 5,
                  'steps': [
                    {'run': "printf 'Independent Gitea artifact recovery\\n' > proof.txt\nsha256sum proof.txt > expected.sha256"},
                    {'uses': UPLOAD, 'with': {'name': 'dr-proof', 'path': 'proof.txt', 'retention-days': 14, 'if-no-files-found': 'error'}},
                    {'run': 'rm proof.txt'},
                    {'uses': DOWNLOAD, 'with': {'name': 'dr-proof', 'path': 'restored'}},
                    {'run': 'cd restored\nsha256sum --check ../expected.sha256'}]}}}
    controls = {'name': 'Runner lifecycle controls', 'on': {'workflow_dispatch': {'inputs': {
        'exercise': {'type': 'choice', 'required': True, 'options': ['timeout', 'cancel']}}}},
        'jobs': {name: {'if': "${{ inputs.exercise == '" + name + "' }}",
            'runs-on': ['ubuntu-24.04', 'dr-offline'], 'timeout-minutes': minutes,
            'steps': [{'run': "echo 'Lifecycle probe started'\ntrap 'echo lifecycle-probe-stopped; exit 143' TERM INT\nsleep 300 &\nwait $!"}]}
            for name, minutes in [('timeout', 1), ('cancel', 10)]}}
    (output / 'platform.yaml').write_text(yaml.safe_dump(platform, sort_keys=False))
    (output / 'controls.yaml').write_text(yaml.safe_dump(controls, sort_keys=False))
    record = {"source_commit": subprocess.check_output(["git", "-C", str(args.source), "rev-parse", "HEAD"]).decode().strip(),
              "workflow_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted((args.source / '.github/workflows').glob('*.yml'))},
              "cmake_presets_sha256": hashlib.sha256((args.source / 'CMakePresets.json').read_bytes()).hexdigest(),
              "script_sha256": inventory, "workflows": report}
    (args.output / "compatibility.json").write_text(json.dumps(record, indent=2) + "\n")
    print("Generated", len(jobs), "Linux workflow jobs; native Windows/macOS entries excluded")


if __name__ == "__main__": main()
