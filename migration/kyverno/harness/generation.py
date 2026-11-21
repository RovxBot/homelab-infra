"""Bounded Flux/prune rehearsal using authentic Namespace-generated resources."""

import copy
import json
import pathlib
import subprocess
import threading
import time
import yaml
from rehearse import (
    WORK,
    EVIDENCE,
    ROOT,
    load,
    apply,
    delete,
    get,
    k,
    run,
    wait,
    record,
    namespace,
    admission,
    ready,
)

OUTPUTS = [
    ("limitranges", "default-container-limits"),
    ("resourcequotas", "namespace-defaults"),
    ("networkpolicies", "default-deny"),
]


def snapshot(ns):
    rows = []
    for kind, name in OUTPUTS:
        obj = get(kind, name, ns)
        rows.append(
            {
                "kind": kind,
                "name": name,
                "uid": obj["metadata"]["uid"],
                "labels": obj["metadata"].get("labels", {}),
                "annotations": obj["metadata"].get("annotations", {}),
                "spec": obj["spec"],
            }
        )
    return rows


def correct(ns):
    rows = snapshot(ns)
    return (
        rows[0]["spec"]["limits"][0]["default"]["cpu"] == "500m"
        and rows[1]["spec"]["hard"]["pods"] == "20"
        and set(rows[2]["spec"]["policyTypes"]) == {"Ingress", "Egress"}
        and rows[2]["spec"]["podSelector"] == {}
        and not rows[2]["spec"].get("ingress")
        and not rows[2]["spec"].get("egress")
    )


class Flux:
    def __enter__(self):
        self.base = WORK / "git-server"
        self.base.mkdir(exist_ok=True)
        self.repo = WORK / "git-working"
        self.repo.mkdir(exist_ok=True)
        run(["git", "init", "--bare", str(self.base / "rehearsal.git")])
        run(
            [
                "git",
                "--git-dir",
                str(self.base / "rehearsal.git"),
                "symbolic-ref",
                "HEAD",
                "refs/heads/main",
            ]
        )
        run(["git", "init", "-b", "main", str(self.repo)])
        for key, value in [
            ("user.email", "rehearsal@example.invalid"),
            ("user.name", "Isolated rehearsal"),
        ]:
            run(["git", "-C", str(self.repo), "config", key, value])
        self.gateway = run(
            [
                "docker",
                "inspect",
                "kyverno-cel-rehearsal-control-plane",
                "--format",
                '{{(index .NetworkSettings.Networks "kind").Gateway}}',
            ]
        ).stdout.strip()
        self.http_log = (EVIDENCE / "git-http.log").open("w")
        self.server = subprocess.Popen(
            [
                "python3",
                "-m",
                "http.server",
                "18080",
                "--bind",
                self.gateway,
                "--directory",
                str(self.base),
            ],
            stdout=self.http_log,
            stderr=self.http_log,
        )
        return self

    def commit(self, docs, stage):
        folder = self.repo / "policies"
        folder.mkdir(exist_ok=True)
        (folder / "resources.yaml").write_text(yaml.safe_dump_all(docs))
        (folder / "kustomization.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "kustomize.config.k8s.io/v1beta1",
                    "kind": "Kustomization",
                    "resources": ["resources.yaml"] if docs else [],
                }
            )
        )
        for args in [
            ["add", "."],
            ["commit", "-m", stage],
            ["push", str(self.base / "rehearsal.git"), "HEAD:main"],
        ]:
            run(["git", "-C", str(self.repo), *args])
        run(
            ["git", "--git-dir", str(self.base / "rehearsal.git"), "update-server-info"]
        )
        sha = run(["git", "-C", str(self.repo), "rev-parse", "HEAD"]).stdout.strip()
        source = {
            "apiVersion": "source.toolkit.fluxcd.io/v1",
            "kind": "GitRepository",
            "metadata": {"name": "rehearsal", "namespace": "flux-system"},
            "spec": {
                "interval": "1h",
                "timeout": "30s",
                "url": f"http://{self.gateway}:18080/rehearsal.git",
                "ref": {"commit": sha},
            },
        }
        apply(source)
        k(
            "annotate",
            "gitrepository",
            "rehearsal",
            "-n",
            "flux-system",
            "--overwrite",
            f"reconcile.fluxcd.io/requestedAt={time.time_ns()}",
        )
        wait(
            "Flux source " + stage,
            lambda: get("gitrepository", "rehearsal", "flux-system")
            .get("status", {})
            .get("artifact", {})
            .get("revision", "")
            .endswith(sha),
        )
        ks = {
            "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
            "kind": "Kustomization",
            "metadata": {"name": "rehearsal", "namespace": "flux-system"},
            "spec": {
                "interval": "1h",
                "timeout": "45s",
                "prune": True,
                "path": "./policies",
                "sourceRef": {"kind": "GitRepository", "name": "rehearsal"},
                "wait": False,
            },
        }
        apply(ks)
        k(
            "annotate",
            "kustomization",
            "rehearsal",
            "-n",
            "flux-system",
            "--overwrite",
            f"reconcile.fluxcd.io/requestedAt={time.time_ns()}",
        )
        wait(
            "Flux apply " + stage,
            lambda: get("kustomization", "rehearsal", "flux-system")
            .get("status", {})
            .get("lastAppliedRevision", "")
            .endswith(sha),
        )
        record(
            "flux-stage",
            status="verified",
            stage=stage,
            revision=sha,
            inventory=get("kustomization", "rehearsal", "flux-system")
            .get("status", {})
            .get("inventory", {}),
        )

    def __exit__(self, *args):
        self.server.terminate()
        self.server.wait(timeout=5)
        self.http_log.close()


class Watch:
    def __init__(self, ns):
        self.ns = ns
        self.procs = []
        self.files = []

    def start(self):
        for kind, name in OUTPUTS:
            rv = get(kind, name, self.ns)["metadata"]["resourceVersion"]
            path = EVIDENCE / f"watch-{self.ns}-{kind}.jsonl"
            out = path.open("w")
            self.files.append(out)
            self.procs.append(
                subprocess.Popen(
                    [
                        "kubectl",
                        "--context",
                        "kind-kyverno-cel-rehearsal",
                        "get",
                        kind,
                        name,
                        "-n",
                        self.ns,
                        "--watch-only",
                        "--output-watch-events",
                        "--resource-version=" + rv,
                        "-o",
                        "json",
                    ],
                    stdout=out,
                    stderr=out,
                )
            )

    def stop(self):
        healthy = all(p.poll() is None for p in self.procs)
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
            p.wait(timeout=5)
        for f in self.files:
            f.close()
        events = []
        decoder = json.JSONDecoder()
        for kind, name in OUTPUTS:
            raw = (EVIDENCE / f"watch-{self.ns}-{kind}.jsonl").read_text().strip()
            while raw:
                event, end = decoder.raw_decode(raw)
                raw = raw[end:].lstrip()
                events.append(event)
                assert event["type"] != "DELETED", event
                if kind == "networkpolicies":
                    spec = event["object"]["spec"]
                    assert (
                        set(spec["policyTypes"]) == {"Ingress", "Egress"}
                        and spec["podSelector"] == {}
                        and not spec.get("ingress")
                        and not spec.get("egress")
                    ), event
        assert healthy, "Watch stream exited before teardown"
        record(
            "continuous-output-watch",
            status="verified",
            namespace=self.ns,
            events=len(events),
            limitation="API object continuity; kindnet does not enforce NetworkPolicy traffic",
        )


def freeze():
    name = "rehearsal-freeze-bootstrap"
    p = {
        "apiVersion": "admissionregistration.k8s.io/v1",
        "kind": "ValidatingAdmissionPolicy",
        "metadata": {"name": name},
        "spec": {
            "failurePolicy": "Fail",
            "matchConstraints": {
                "resourceRules": [
                    {
                        "apiGroups": [""],
                        "apiVersions": ["v1"],
                        "operations": ["CREATE", "UPDATE"],
                        "resources": ["namespaces"],
                    }
                ]
            },
            "validations": [
                {
                    "expression": "object.metadata.?labels.orValue({})[?'homelab.cooked.beer/bootstrap'].orValue('') != 'enabled' && (oldObject == null || oldObject.metadata.?labels.orValue({})[?'homelab.cooked.beer/bootstrap'].orValue('') != 'enabled')",
                    "message": "Rehearsal freezes bootstrap triggers during ownership transfer",
                }
            ],
        },
    }
    b = {
        "apiVersion": "admissionregistration.k8s.io/v1",
        "kind": "ValidatingAdmissionPolicyBinding",
        "metadata": {"name": name},
        "spec": {"policyName": name, "validationActions": ["Deny"]},
    }
    apply([p, b])
    time.sleep(2)
    probe = namespace("freeze-probe")
    probe["metadata"]["labels"]["homelab.cooked.beer/bootstrap"] = "enabled"
    result = admission(probe, check_policy=False)
    assert not result["allowed"] and name in result["stderr"], result
    return [b, p]


def queue_drained():
    urs = get("updaterequests.kyverno.io")["items"]
    return all(u.get("status", {}).get("state") in ["Completed", "Skip"] for u in urs)


def before_upgrade():
    legacy = load("infra/kyverno/policies/platform-namespace-bootstrap.yaml")[0]
    gp = load("migration/kyverno/policies/generator.yaml")[0]
    state = {"passed": False}
    with Flux() as flux:
        ns = "generation-existing"
        apply(namespace(ns))
        flux.commit([legacy], "legacy-active")
        trigger = get("namespaces", ns)
        trigger["metadata"]["labels"]["homelab.cooked.beer/bootstrap"] = "enabled"
        apply(trigger)
        wait("legacy generates all real outputs", lambda: correct(ns))
        initial = snapshot(ns)
        state["initial"] = initial
        (EVIDENCE / "generation-initial.json").write_text(
            json.dumps(initial, indent=2) + "\n"
        )
        watcher = Watch(ns)
        watcher.start()
        locks = freeze()
        try:
            # Deliberate orphan configuration is applied and observed BEFORE prune.
            orphan = copy.deepcopy(legacy)
            for r in orphan["spec"]["rules"]:
                r["generate"]["orphanDownstreamOnPolicyDelete"] = True
            flux.commit([orphan], "legacy-orphan-before-prune")
            live = get("clusterpolicies", "platform-namespace-bootstrap")
            assert all(
                r["generate"].get("orphanDownstreamOnPolicyDelete")
                for r in live["spec"]["rules"]
            )
            wait("legacy queue drains", queue_drained)
            flux.commit([], "prune-legacy-under-freeze")
            assert not any(
                p["metadata"]["name"] == "platform-namespace-bootstrap"
                for p in get("clusterpolicies")["items"]
            )
            wait("post-prune legacy queue drains", queue_drained)
            assert [r["uid"] for r in snapshot(ns)] == [r["uid"] for r in initial]
            assert correct(ns)
            # No legacy generator exists when CEL synchronization is activated.
            flux.commit([gp], "cel-active-after-legacy-absent")
            ready(gp)
            delete(locks)
            locks = []
            k(
                "annotate",
                "namespace",
                ns,
                "--overwrite",
                "rehearsal.cooked.beer/trigger=cel",
            )
            # Force drift so resource equality alone cannot falsely prove adoption.
            k(
                "patch",
                "limitrange",
                "default-container-limits",
                "-n",
                ns,
                "--type=json",
                "-p",
                '[{"op":"replace","path":"/spec/limits/0/default/cpu","value":"600m"}]',
            )
            wait(
                "CEL takes ownership and repairs real legacy output",
                lambda: correct(ns),
                45,
            )
            adopted = snapshot(ns)
            assert [r["uid"] for r in adopted] == [r["uid"] for r in initial]
            (EVIDENCE / "generation-adopted.json").write_text(
                json.dumps(adopted, indent=2) + "\n"
            )
            state["passed"] = True
            record(
                "generator-existing-handoff",
                status="verified",
                namespace=ns,
                uids=[r["uid"] for r in adopted],
            )
        except Exception as e:
            state["error"] = str(e)
            (EVIDENCE / "generation-failure-snapshot.json").write_text(
                json.dumps(
                    {
                        "outputs": snapshot(ns),
                        "urs": get("updaterequests.kyverno.io"),
                        "legacy": get("clusterpolicies"),
                        "cel": get("generatingpolicies"),
                    },
                    indent=2,
                )
                + "\n"
            )
            record("generator-existing-handoff", status="failed", error=str(e))
            # Stop generator qualification at the first transfer gate failure.
        finally:
            watcher.stop()
            if locks:
                delete(locks)
    return state


def after_upgrade(state):
    ns = "generation-existing"
    before = snapshot(ns)
    k("rollout", "restart", "deployment/kyverno-background-controller", "-n", "kyverno")
    k(
        "rollout",
        "status",
        "deployment/kyverno-background-controller",
        "-n",
        "kyverno",
        "--timeout=90s",
        timeout=100,
    )
    k(
        "patch",
        "resourcequota",
        "namespace-defaults",
        "-n",
        ns,
        "--type=merge",
        "-p",
        '{"spec":{"hard":{"pods":"21"}}}',
    )
    wait("post-upgrade drift correction", lambda: correct(ns))
    assert [r["uid"] for r in snapshot(ns)] == [r["uid"] for r in before]
    record(
        "generator-upgrade-restart", status="verified", version="1.20.0", namespace=ns
    )
    # Empty-output case: freeze prevents new trigger admission while legacy is
    # absent. Namespace is created without bootstrap and cannot run any Pods;
    # existing generated deny objects stay continuously present during transfer.
    empty = "generation-empty"
    apply(namespace(empty))
    assert not get("networkpolicies", ns=empty)["items"]
    trigger = get("namespaces", empty)
    trigger["metadata"]["labels"]["homelab.cooked.beer/bootstrap"] = "enabled"
    apply(trigger)
    wait("CEL empty outputs", lambda: correct(empty))
    record(
        "generator-empty-create",
        status="verified",
        namespace=empty,
        outputs=snapshot(empty),
    )
    gp = load("migration/kyverno/policies/generator.yaml")[0]
    watcher = Watch(ns)
    watcher.start()
    locks = freeze()
    try:
        with Flux() as flux:
            orphan = copy.deepcopy(gp)
            orphan["spec"]["evaluation"]["orphanDownstreamOnPolicyDelete"][
                "enabled"
            ] = True
            flux.commit([orphan], "cel-orphan-before-rollback")
            flux.commit([], "prune-cel-before-downgrade")
            assert not get("generatingpolicies")["items"]
            from rehearse import install

            install("1.19.2")
            legacy = load("infra/kyverno/policies/platform-namespace-bootstrap.yaml")[0]
            flux.commit([legacy], "restore-legacy-after-downgrade")
            delete(locks)
            locks = []
            k(
                "annotate",
                "namespace",
                ns,
                "--overwrite",
                "rehearsal.cooked.beer/trigger=rollback",
            )
            k(
                "patch",
                "limitrange",
                "default-container-limits",
                "-n",
                ns,
                "--type=json",
                "-p",
                '[{"op":"replace","path":"/spec/limits/0/default/cpu","value":"600m"}]',
            )
            wait("legacy rollback drift repair", lambda: correct(ns), 45)
            assert [r["uid"] for r in snapshot(ns)] == [r["uid"] for r in before]
            record("generator-rollback", status="verified", namespace=ns)
    except Exception as e:
        record("generator-rollback", status="failed", error=str(e))
    finally:
        watcher.stop()
        if locks:
            delete(locks)
