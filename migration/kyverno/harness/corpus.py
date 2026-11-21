"""Admission fixtures exercise #2159 rules; no containers are scheduled."""

import copy
import yaml
from rehearse import ROOT, OWNER, labels, namespace


def change(obj, path, value=None, remove=False):
    if path == ["spec", "volumes"] and not remove:
        # Keep referenced baseline volumes so admission failures test the policy,
        # rather than an unrelated missing-volumeMount validation error.
        value = copy.deepcopy(obj["spec"].get("volumes", [])) + value
    d = copy.deepcopy(obj)
    p = d
    for key in path[:-1]:
        p = p[key]
    if remove:
        p.pop(path[-1], None)
    else:
        p[path[-1]] = value
    return d


def workload(kind="Deployment", name="policy-probe", ns="cel-tests"):
    c = dict(
        name="main",
        image="example.invalid/app@sha256:" + "a" * 64,
        resources=dict(
            requests=dict(cpu="10m", memory="32Mi"),
            limits=dict(cpu="100m", memory="64Mi"),
        ),
        securityContext=dict(
            runAsNonRoot=True,
            allowPrivilegeEscalation=False,
            capabilities=dict(drop=["ALL"]),
            seccompProfile=dict(type="RuntimeDefault"),
        ),
    )
    t = dict(
        metadata=dict(labels={OWNER: "test", "app": "policy-probe"}),
        spec=dict(
            containers=[c],
            restartPolicy="Never" if kind in ["Job", "CronJob"] else "Always",
        ),
    )
    spec = dict(template=t)
    if kind in ["Deployment", "StatefulSet", "DaemonSet"]:
        spec["selector"] = dict(matchLabels={"app": "policy-probe"})
    if kind in ["Deployment", "StatefulSet"]:
        spec["replicas"] = 0
    if kind == "StatefulSet":
        spec["serviceName"] = "policy-probe"
    if kind == "Job":
        spec["suspend"] = True
    if kind == "CronJob":
        spec = dict(
            schedule="0 0 * * *", suspend=True, jobTemplate=dict(spec=dict(template=t))
        )
    return dict(
        apiVersion="batch/v1" if kind in ["Job", "CronJob"] else "apps/v1",
        kind=kind,
        metadata=dict(name=name, namespace=ns),
        spec=spec,
    )


def runner(pool):
    path = (
        "apps/gitea-runners/" + ("wowdecomp/" if pool == "wow" else "") + "config.yaml"
    )
    config = yaml.safe_load((ROOT / path).read_text())
    p = copy.deepcopy(config["kubernetes"]["pod_template"])
    p.update(apiVersion="v1", kind="Pod")
    p["metadata"].update(
        name="policy-probe", namespace=config["kubernetes"]["namespace"]
    )
    p["spec"]["containers"][0].update(
        image=config["runner"]["labels"][0].split("kubernetes://")[1],
        command=["sleep", "60"],
    )
    p["spec"]["restartPolicy"] = "Never"
    return p


def cases(source, r):
    if source == "platform-namespace-standards":
        base = namespace("admission-probe")
        rows = [("valid", base)]
        for key in labels():
            rows.extend(
                [
                    (
                        key + "-missing",
                        change(base, ["metadata", "labels", key], remove=True),
                    ),
                    (key + "-empty", change(base, ["metadata", "labels", key], "")),
                ]
            )
        # Native PSA validates its own values; native rejection is recorded distinctly.
        rows += [
            (name, namespace(name) | {"metadata": {"name": name}})
            for name in [
                "default",
                "kube-node-lease",
                "kube-public",
                "kube-system",
                "sops",
            ]
        ]
        return rows
    if source == "platform-pod-standards":
        rows = []
        for kind in ["Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"]:
            b = workload(kind)
            tp = (
                ["spec", "jobTemplate", "spec", "template"]
                if kind == "CronJob"
                else ["spec", "template"]
            )
            sp = tp + ["spec"]
            cp = sp + ["containers", 0]
            add = lambda name, p: rows.append((kind + "-" + name, p))
            add("valid", b)
            if r == "require-owner-label":
                add(
                    "missing",
                    change(b, tp + ["metadata", "labels", OWNER], remove=True),
                )
                add("empty", change(b, tp + ["metadata", "labels", OWNER], ""))
            if r == "require-requests-and-limits":
                for tier in ["requests", "limits"]:
                    for key in ["cpu", "memory"]:
                        add(
                            tier + "-" + key,
                            change(b, cp + ["resources", tier, key], remove=True),
                        )
                add("no-resources", change(b, cp + ["resources"], remove=True))
                init = copy.deepcopy(b)
                p = init
                for key in sp:
                    p = p[key]
                p["initContainers"] = [
                    copy.deepcopy(p["containers"][0]) | {"name": "init"}
                ]
                add("init-valid", init)
                add(
                    "init-no-resources",
                    change(init, sp + ["initContainers", 0, "resources"], remove=True),
                )
            if r == "require-runtime-hardening":
                for key, value in [
                    ("runAsNonRoot", False),
                    ("allowPrivilegeEscalation", True),
                    ("privileged", True),
                    ("capabilities", {}),
                    ("seccompProfile", {"type": "Unconfined"}),
                ]:
                    add(key, change(b, cp + ["securityContext", key], value))
                inherit = change(
                    b,
                    sp + ["securityContext"],
                    {
                        "runAsNonRoot": True,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                )
                add(
                    "explicit-false-pod-true",
                    change(inherit, cp + ["securityContext", "runAsNonRoot"], False),
                )
                add("absent-security", change(b, cp + ["securityContext"], remove=True))
            if r == "disallow-host-network":
                for val in [True, False]:
                    add(str(val), change(b, sp + ["hostNetwork"], val))
            if r in ["require-digest-pinned-images", "disallow-latest-tags"]:
                for image in [
                    "repo:v1",
                    "repo:latest",
                    "repo:latest-extra",
                    "repo@sha256:",
                    "repo:latest@sha256:" + "a" * 64,
                ]:
                    add(image, change(b, cp + ["image"], image))
                d = copy.deepcopy(b)
                p = d
                for key in sp:
                    p = p[key]
                p["initContainers"] = [{"name": "init", "image": "repo:latest"}]
                add("init-image-legacy-scope", d)
            if r == "require-probes":
                p = change(b, cp + ["ports"], [{"containerPort": 80}])
                add("ports-no-probes", p)
                p = change(p, cp + ["livenessProbe"], {"exec": {"command": ["true"]}})
                add("liveness-only", p)
                p = change(p, cp + ["readinessProbe"], {"exec": {"command": ["true"]}})
                add("both-probes", p)
                add("empty-ports", change(b, cp + ["ports"], []))
        bad = workload()
        bad["spec"]["template"]["metadata"]["labels"].pop(OWNER)
        bad["spec"]["template"]["spec"]["containers"][0].pop("resources")
        bad["spec"]["template"]["spec"]["containers"][0]["image"] = "repo:latest"
        bad["spec"]["template"]["spec"]["hostNetwork"] = True
        for ns in [
            "flux-system",
            "kube-node-lease",
            "kube-public",
            "kube-system",
            "kyverno",
        ]:
            rows.append(("excluded-" + ns, change(bad, ["metadata", "namespace"], ns)))
        # Exception positives and adjacent namespace/name/kind negatives. Rule refs
        # are always taken from the existing exception resources unchanged.
        for ns, name, kind in [
            ("longhorn-system", "probe", "Deployment"),
            ("gpu-operator", "probe", "Deployment"),
            ("wireguard", "probe", "Deployment"),
            ("wotlk", "wotlk-weekly-restart", "CronJob"),
            ("wotlk", "wotlk-weekly-restart-123", "Job"),
            ("wotlk", "adjacent", "Job"),
            ("home-assistant", "home-assistant", "Deployment"),
            ("home-assistant", "adjacent", "Deployment"),
            ("home-assistant", "home-assistant", "StatefulSet"),
            ("cloudflared", "cloudflared", "Deployment"),
            ("homepage", "homepage-assets", "StatefulSet"),
            ("homepage", "adjacent", "StatefulSet"),
            ("cloudflared", "homepage-assets", "Deployment"),
            ("homepage", "cloudflared", "StatefulSet"),
            ("homepage", "pod-report", "CronJob"),
            ("cloudflared", "pod-report", "Job"),
            ("cel-tests", "cloudflared", "Deployment"),
            ("wotlk", "wotlk-weekly-restart", "Deployment"),
        ]:
            d = workload(kind, name, ns)
            p = (
                d["spec"]["jobTemplate"]["spec"]["template"]
                if kind == "CronJob"
                else d["spec"]["template"]
            )
            p["metadata"]["labels"].pop(OWNER, None)
            p["spec"]["hostNetwork"] = True
            p["spec"]["containers"][0]["image"] = "repo:latest"
            p["spec"]["containers"][0].pop("resources")
            p["spec"]["containers"][0]["securityContext"] = {}
            p["spec"]["containers"][0]["ports"] = [{"containerPort": 80}]
            rows.append(("exception-" + ns + "-" + name + "-" + kind, d))
        return rows
    pool = "wow" if source.startswith("wow") else "core"
    b = runner(pool)
    cp = ["spec", "containers", 0]
    sc = cp + ["securityContext"]
    rows = [
        ("valid", b),
        ("adjacent-namespace", change(b, ["metadata", "namespace"], "cel-tests")),
    ]
    add = lambda name, d: rows.append((name, d))
    if r == "restricted-with-sanitizer-capability":
        for key in ["hostPID", "hostIPC", "hostNetwork"]:
            add(key, change(b, ["spec", key], True))
        for key, val in [
            ("privileged", True),
            ("allowPrivilegeEscalation", True),
            ("seccompProfile", {"type": "Unconfined"}),
            ("appArmorProfile", {"type": "Unconfined"}),
            ("seLinuxOptions", {"type": "unconfined_t"}),
            ("seLinuxOptions", {"user": "system_u"}),
            ("procMount", "Unmasked"),
        ]:
            add(key + str(val), change(b, sc + [key], val))
        add(
            "host-port",
            change(b, cp + ["ports"], [{"containerPort": 80, "hostPort": 8080}]),
        )
        add(
            "host-probe",
            change(
                b,
                cp + ["livenessProbe"],
                {"httpGet": {"host": "127.0.0.1", "port": 80}},
            ),
        )
        add(
            "host-lifecycle",
            change(
                b,
                cp + ["lifecycle"],
                {"preStop": {"httpGet": {"host": "127.0.0.1", "port": 80}}},
            ),
        )
        add(
            "sysctl-unsafe",
            change(
                b,
                ["spec", "securityContext", "sysctls"],
                [{"name": "kernel.core_pattern", "value": "x"}],
            ),
        )
        add(
            "apparmor-annotation",
            change(
                b,
                ["metadata", "annotations"],
                {"container.apparmor.security.beta.kubernetes.io/job": "unconfined"},
            ),
        )
        add(
            "host-path",
            change(
                b, ["spec", "volumes"], [{"name": "host", "hostPath": {"path": "/"}}]
            ),
        )
        add(
            "pss-volume",
            change(
                b, ["spec", "volumes"], [{"name": "config", "configMap": {"name": "x"}}]
            ),
        )
        add("image-excluded-root", change(b, sc + ["runAsUser"], 0))
        add(
            "image-adjacent-root",
            change(
                change(b, sc + ["runAsUser"], 0),
                cp + ["image"],
                "example.invalid/toolchain:v1",
            ),
        )
        add(
            "image-adjacent-caps",
            change(
                change(b, sc + ["capabilities", "add"], ["SYS_PTRACE"]),
                cp + ["image"],
                "example.invalid/toolchain:v1",
            ),
        )
        add("container-seccomp-absent", change(b, sc + ["seccompProfile"], remove=True))
        add(
            "pod-seccomp-unconfined",
            change(
                b, ["spec", "securityContext", "seccompProfile"], {"type": "Unconfined"}
            ),
        )
    if r == "bound-job-environment":
        for path, value in [
            (["metadata", "labels", OWNER], "other"),
            (["spec", "automountServiceAccountToken"], True),
            (cp + ["image"], "repo:latest"),
            (cp + ["name"], "other"),
        ]:
            add(str(path), change(b, path, value))
        for tier in ["requests", "limits"]:
            for field in ["cpu", "memory"]:
                add(
                    tier + field,
                    change(b, cp + ["resources", tier, field], remove=True),
                )
        if pool == "wow":
            add(
                "missing-pull-secret",
                change(b, ["spec", "imagePullSecrets"], remove=True),
            )
    if r == "one-job-container":
        add(
            "sidecar",
            change(
                b,
                ["spec", "containers"],
                b["spec"]["containers"] + [dict(name="sidecar", image="repo:v1")],
            ),
        )
        add(
            "init",
            change(b, ["spec", "initContainers"], [dict(name="init", image="repo:v1")]),
        )
    if r in [
        "drop-all-and-only-allow-ptrace",
        "bounded-toolchain-capabilities",
        "ptrace-only-in-sanitizer-jobs",
    ]:
        for cap in [[], ["SYS_PTRACE"], ["SYS_ADMIN"], ["CHOWN"]]:
            add(str(cap), change(b, sc + ["capabilities", "add"], cap))
        add("drop-absent", change(b, sc + ["capabilities", "drop"], remove=True))
        p = change(b, ["metadata", "labels", "homelab.cooked.beer/sanitizer"], "true")
        add("sanitizer-ptrace", change(p, sc + ["capabilities", "add"], ["SYS_PTRACE"]))
    if r in ["ordinary-job-identity", "bounded-sanitizer-identity"]:
        for uid in [0, 1000, 2000]:
            add("uid" + str(uid), change(b, sc + ["runAsUser"], uid))
        add("container-nonroot-false", change(b, sc + ["runAsNonRoot"], False))
        san = change(b, ["metadata", "labels", "homelab.cooked.beer/sanitizer"], "true")
        san["spec"]["containers"][0]["securityContext"].update(
            runAsUser=0,
            runAsGroup=0,
            runAsNonRoot=False,
            capabilities={"drop": ["ALL"], "add": ["SYS_PTRACE"]},
        )
        add("sanitizer", san)
        add("sanitizer-bad-uid", change(san, sc + ["runAsUser"], 2000))
        add(
            "sanitizer-extra-cap",
            change(san, sc + ["capabilities", "add"], ["SYS_PTRACE", "SYS_ADMIN"]),
        )
    if r == "no-extra-job-credentials":
        for typ, data in [
            ("secret", {"secretName": "job-secret"}),
            ("configMap", {"name": "x"}),
            ("persistentVolumeClaim", {"claimName": "x"}),
            ("hostPath", {"path": "/"}),
        ]:
            add(typ, change(b, ["spec", "volumes"], [{"name": "test", typ: data}]))
    if r == "no-mounted-registry-credential":
        for name in ["wowdecomp-toolchain-pull", "adjacent"]:
            add(
                name,
                change(
                    b,
                    ["spec", "volumes"],
                    [{"name": "credential", "secret": {"secretName": name}}],
                ),
            )
    if r == "no-registry-credential-in-environment":
        for name in ["wowdecomp-toolchain-pull", "adjacent"]:
            add(
                "env-" + name,
                change(
                    b,
                    cp + ["env"],
                    [
                        {
                            "name": "CREDENTIAL",
                            "valueFrom": {"secretKeyRef": {"name": name, "key": "x"}},
                        }
                    ],
                ),
            )
            add(
                "envFrom-" + name,
                change(b, cp + ["envFrom"], [{"secretRef": {"name": name}}]),
            )
    return rows
