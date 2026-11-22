#!/usr/bin/env python3
"""Build reviewable draft CEL manifests from the fixed #2159 contracts.

No production Kustomization references migration/. Predicates deliberately retain
legacy substring, truthiness, regular-container image scope and UPDATE handling.
"""
import copy
import json
import pathlib
import re
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]
OUT = ROOT / "migration/kyverno"
OWNER = "homelab.cooked.beer/owner"
SYSTEM = ["flux-system", "kube-node-lease", "kube-public", "kube-system", "kyverno"]
INDEX = []
INVENTORY = json.loads((ROOT / "docs/kyverno/migration-inventory.json").read_text())
SOURCES = {
    p["name"]: yaml.safe_load((ROOT / p["path"]).read_text())
    for p in INVENTORY["policies"]
}


def lit(x):
    return json.dumps(x)


def rule(resources, groups=None, operations=None, versions=None):
    return dict(
        apiGroups=groups or ["*"],
        apiVersions=versions
        or (["*"] if groups is None or groups == ["*"] else ["v1"]),
        operations=operations or ["CREATE", "UPDATE"],
        resources=resources,
    )


def label(o, key):
    return f"{o}.metadata.?labels.orValue({{}})[?{lit(key)}].orValue('')"


def ann(o, key):
    return f"{o}.metadata.?annotations.orValue({{}})[?{lit(key)}].orValue('')"


def containers(s, init=True):
    return (
        f"({s}.?containers.orValue([])"
        + (f" + {s}.?initContainers.orValue([])" if init else "")
        + ")"
    )


def substitute(expr, obj):
    return re.sub(r"\bo\b", obj, expr)


def emit(path, docs):
    (OUT / path).write_text(
        "# DRAFT: integration fixtures only; activation gates are not satisfied.\n"
        + yaml.safe_dump_all(docs, sort_keys=False, width=110)
    )


def policy(
    source,
    r,
    predicate,
    rules,
    ns="",
    conditions=None,
    background=True,
    existing=True,
    audit=False,
):
    name = (
        source
        if source in ["platform-namespace-standards", "platform-flux-guardrails"]
        else f"{source}-{r}"
    )
    if len(name) > 63:
        name = name[:63].rstrip("-")
    original = SOURCES[source]
    authored = next(rule for rule in original["spec"]["rules"] if rule["name"] == r)
    message = authored["validate"].get("message", r)
    metadata = dict(
        name=name,
        annotations={
            **original["metadata"].get("annotations", {}),
            "migration.cooked.beer/source-policy": source,
            "migration.cooked.beer/source-rule": r,
        },
    )
    if ns:
        metadata["namespace"] = ns
    # Legacy Enforce denials emit no warning; its Audit guardrail does.
    spec = dict(
        failurePolicy="Fail",
        validationActions=(["Audit", "Warn"] if audit else ["Deny"]),
        evaluation=dict(
            admission=dict(enabled=True), background=dict(enabled=background)
        ),
        autogen=dict(
            podControllers=dict(controllers=[]),
            validatingAdmissionPolicy=dict(enabled=False),
        ),
        matchConstraints=dict(resourceRules=rules),
        matchConditions=conditions or [],
        validations=[dict(expression=substitute(predicate, "object"), message=message)],
    )
    if existing and not audit:
        # Legacy skips an Enforce rule only when both old and new fail that rule.
        # PSS implements the same old/new failure exemption in its own handler.
        spec["matchConditions"].append(
            dict(
                name="legacy-existing-violation",
                expression="request.operation != 'UPDATE' || oldObject == null || ("
                + substitute(predicate, "oldObject")
                + ") || ("
                + substitute(predicate, "object")
                + ")",
            )
        )
    if not spec["matchConditions"]:
        del spec["matchConditions"]
    p = dict(
        apiVersion="policies.kyverno.io/v1",
        kind="NamespacedValidatingPolicy" if ns else "ValidatingPolicy",
        metadata=metadata,
        spec=spec,
    )
    INDEX.append(
        dict(
            source_policy=source, source_rule=r, name=name, kind=p["kind"], namespace=ns
        )
    )
    return p


def main():
    nslabels = [OWNER] + [
        "pod-security.kubernetes.io/" + s
        for s in [
            "enforce",
            "enforce-version",
            "audit",
            "audit-version",
            "warn",
            "warn-version",
        ]
    ]
    nscond = [
        dict(
            name="excluded-names",
            expression="!(object.metadata.name in ['default','kube-node-lease','kube-public','kube-system','sops'])",
        )
    ]
    ns = policy(
        "platform-namespace-standards",
        "require-psa-and-owner-labels",
        " && ".join(f"{label('o',k)} != ''" for k in nslabels),
        [rule(["namespaces"], [""])],
        conditions=nscond,
    )
    emit("policies/namespace.yaml", [ns])
    # Typed controllers have exactly one of these template paths.
    t = "(o.kind == 'CronJob' ? o.spec.jobTemplate.spec.template : o.spec.template)"
    s = t + ".spec"
    budget = "c.?resources.?requests.orValue({})[?'cpu'].orValue('') != '' && c.?resources.?requests.orValue({})[?'memory'].orValue('') != '' && c.?resources.?limits.orValue({})[?'cpu'].orValue('') != '' && c.?resources.?limits.orValue({})[?'memory'].orValue('') != ''"
    hard = f"(c.?securityContext.?runAsNonRoot.orValue(false) || {s}.?securityContext.?runAsNonRoot.orValue(false)) && c.?securityContext.?allowPrivilegeEscalation.orValue(true) == false && !c.?securityContext.?privileged.orValue(false) && 'ALL' in c.?securityContext.?capabilities.?drop.orValue([]) && (c.?securityContext.?seccompProfile.?type.orValue('') != '' ? c.securityContext.seccompProfile.type : {s}.?securityContext.?seccompProfile.?type.orValue('')) == 'RuntimeDefault'"
    predicates = {
        "require-owner-label": f"{label(t,OWNER)} != ''",
        "require-requests-and-limits": f"{containers(s)}.all(c, {budget})",
        "require-runtime-hardening": f"{containers(s)}.all(c, {hard})",
        "disallow-host-network": f"!{s}.?hostNetwork.orValue(false)",
        "require-digest-pinned-images": f"{containers(s,False)}.all(c, c.?image.orValue('').contains('@sha256:'))",
        "disallow-latest-tags": f"{containers(s,False)}.all(c, !c.?image.orValue('').contains(':latest'))",
        "require-probes": f"{containers(s,False)}.all(c, c.?ports.orValue([]).size() == 0 || (has(c.readinessProbe) && has(c.livenessProbe)))",
    }
    docs = []
    for r, expr in predicates.items():
        rules = [rule(["deployments", "statefulsets", "daemonsets"], ["apps"])]
        if r != "require-probes":
            rules.append(rule(["jobs", "cronjobs"], ["batch"]))
        docs.append(
            policy(
                "platform-pod-standards",
                r,
                expr,
                rules,
                conditions=[
                    dict(
                        name="excluded-namespaces",
                        expression=f"!(request.namespace in {lit(SYSTEM)})",
                    )
                ],
            )
        )
    emit("policies/workloads.yaml", docs)
    flux = "request.userInfo.username in ['system:serviceaccount:flux-system:helm-controller','system:serviceaccount:flux-system:kustomize-controller']"
    fc = [
        dict(
            name="has-flux-label",
            expression="'kustomize.toolkit.fluxcd.io/name' in (request.operation == 'DELETE' ? oldObject : object).metadata.?labels.orValue({})",
        ),
        dict(
            name="excluded-namespaces",
            expression=f"!(request.?namespace.orValue('') in {lit(SYSTEM)})",
        ),
        dict(
            name="excluded-kinds",
            expression="!((request.operation == 'DELETE' ? oldObject : object).kind in ['Endpoints','EndpointSlice','PolicyReport','ClusterPolicyReport'])",
        ),
        # Exactly one runtime-only ConfigMap updater, without disabling the
        # Flux audit for other users, resource names or operations.
        dict(
            name="kubescape-assessment-status-writer",
            expression=(
                "!(request.operation == 'UPDATE' && request.resource.group == '' "
                "&& request.resource.version == 'v1' && request.resource.resource == 'configmaps' "
                "&& request.?namespace.orValue('') == 'kubescape' "
                "&& request.name == 'kubescape-assessment-status' "
                "&& request.userInfo.username == 'system:serviceaccount:kubescape:kubescape-assessment')"
            ),
        ),
        dict(
            name="longhorn-controller",
            expression="!(request.resource.group == 'longhorn.io' && request.resource.version == 'v1beta2' && request.resource.resource == 'backuptargets' && request.namespace == 'longhorn-system' && request.userInfo.username == 'system:serviceaccount:longhorn-system:longhorn-service-account')",
        ),
    ]
    emit(
        "policies/flux.yaml",
        [
            policy(
                "platform-flux-guardrails",
                "audit-manual-edits-to-flux-managed-resources",
                flux,
                [rule(["*"], operations=["CREATE", "UPDATE", "DELETE"])],
                conditions=fc,
                background=False,
                audit=True,
            )
        ],
    )
    runners = []
    for source, path, ns in [
        ("gitea-job-security", "apps/gitea-runners/job-security.yaml", "gitea-jobs"),
        (
            "wowdecomp-linux-security",
            "apps/gitea-runners/wowdecomp/job-security.yaml",
            "wowdecomp-ci-jobs",
        ),
    ]:
        legacy = yaml.safe_load((ROOT / path).read_text())
        image = legacy["spec"]["rules"][0]["validate"]["podSecurity"]["exclude"][0][
            "images"
        ][0]
        cs = containers("o.spec")[:-1] + " + o.spec.?ephemeralContainers.orValue([]))"
        ctx = "c.?securityContext"
        pc = "o.spec.?securityContext"
        exempt = f"c.image == {lit(image)}"
        # Explicit restricted profile, including baseline controls. Never delegate
        # enforcement to privileged PSA. Image exceptions apply to exactly 3 controls.
        controls = [
            "!o.spec.?hostNetwork.orValue(false) && !o.spec.?hostPID.orValue(false) && !o.spec.?hostIPC.orValue(false)",
            f"{cs}.all(c, !c.?securityContext.?privileged.orValue(false))",
            f"{cs}.all(c, c.?ports.orValue([]).all(p, p.?hostPort.orValue(0) == 0))",
            f"{cs}.all(c, c.?securityContext.?allowPrivilegeEscalation.orValue(true) == false)",
            f"{cs}.all(c, {exempt} || ('ALL' in c.?securityContext.?capabilities.?drop.orValue([]) && c.?securityContext.?capabilities.?add.orValue([]).all(a, a == 'NET_BIND_SERVICE')))",
            f"{cs}.all(c, {exempt} || c.?securityContext.?runAsNonRoot.orValue(o.spec.?securityContext.?runAsNonRoot.orValue(false)))",
            f"{cs}.all(c, {exempt} || c.?securityContext.?runAsUser.orValue(o.spec.?securityContext.?runAsUser.orValue(-1)) != 0)",
            f"{cs}.all(c, c.?securityContext.?seccompProfile.?type.orValue(o.spec.?securityContext.?seccompProfile.?type.orValue('')) in ['RuntimeDefault','Localhost'])",
            f"{cs}.all(c, c.?securityContext.?procMount.orValue('Default') == 'Default')",
            "o.spec.?volumes.orValue([]).all(v, has(v.configMap) || has(v.csi) || has(v.downwardAPI) || has(v.emptyDir) || has(v.ephemeral) || has(v.persistentVolumeClaim) || has(v.projected) || has(v.secret))",
            "o.spec.?securityContext.?sysctls.orValue([]).all(s, s.name in ['kernel.shm_rmid_forced','net.ipv4.ip_local_port_range','net.ipv4.tcp_syncookies','net.ipv4.ping_group_range','net.ipv4.ip_unprivileged_port_start','net.ipv4.ip_local_reserved_ports','net.ipv4.tcp_keepalive_time','net.ipv4.tcp_fin_timeout','net.ipv4.tcp_keepalive_intvl','net.ipv4.tcp_keepalive_probes','net.ipv4.tcp_rmem','net.ipv4.tcp_wmem'])",
            f"([o.spec.?securityContext.orValue({{}})] + {cs}.map(c,c.?securityContext.orValue({{}}))).all(sc, sc.?appArmorProfile.?type.orValue('RuntimeDefault') in ['RuntimeDefault','Localhost'])",
            "o.metadata.?annotations.orValue({}).all(k, !k.startsWith('container.apparmor.security.beta.kubernetes.io/') || o.metadata.annotations[k] == 'runtime/default' || o.metadata.annotations[k].startsWith('localhost/'))",
            f"([o.spec.?securityContext.orValue({{}})] + {cs}.map(c,c.?securityContext.orValue({{}}))).all(sc, sc.?seLinuxOptions.?type.orValue('') in ['','container_t','container_init_t','container_kvm_t','container_engine_t'] && sc.?seLinuxOptions.?user.orValue('') == '' && sc.?seLinuxOptions.?role.orValue('') == '')",
            f"{cs}.all(c, [c.?livenessProbe.orValue({{}}),c.?readinessProbe.orValue({{}}),c.?startupProbe.orValue({{}})].all(p, p.?httpGet.?host.orValue('') == '' && p.?tcpSocket.?host.orValue('') == '') && [c.?lifecycle.?postStart.orValue({{}}),c.?lifecycle.?preStop.orValue({{}})].all(p, p.?httpGet.?host.orValue('') == '' && p.?tcpSocket.?host.orValue('') == ''))",
        ]
        controls.append(
            "o.spec.?securityContext.?seccompProfile.?type.orValue('RuntimeDefault') in ['RuntimeDefault','Localhost']"
        )
        controls.append(
            f"([o.spec.?securityContext.orValue({{}})] + {cs}.map(c,c.?securityContext.orValue({{}}))).all(sc, !sc.?windowsOptions.?hostProcess.orValue(false))"
        )
        common = {
            "restricted-with-sanitizer-capability": " && ".join(
                f"({x})" for x in controls
            ),
            "bound-job-environment": f"{label('o',OWNER)} == 'sam' && o.spec.?automountServiceAccountToken.orValue(true) == false && o.spec.containers.exists(c, c.name == 'job' && c.image == {lit(image)} && ({budget}))"
            + (
                " && o.spec.?imagePullSecrets.orValue([]).exists(p,p.name == 'wowdecomp-toolchain-pull')"
                if ns.startswith("wow")
                else ""
            ),
            "one-job-container": "o.spec.containers.size() == 1 && o.spec.?initContainers.orValue([]).size() == 0 && o.spec.?ephemeralContainers.orValue([]).size() == 0",
            "no-extra-job-credentials": "o.spec.?volumes.orValue([]).all(v, has(v.emptyDir) || has(v.secret))",
        }
        if ns.startswith("wow"):
            common.update(
                {
                    "bounded-toolchain-capabilities": "o.spec.containers.all(c, 'ALL' in c.?securityContext.?capabilities.?drop.orValue([]) && c.?securityContext.?capabilities.?add.orValue([]).all(a,a in ['CHOWN','DAC_OVERRIDE','FOWNER','SETUID','SETGID','SYS_PTRACE']))",
                    "no-mounted-registry-credential": "o.spec.?volumes.orValue([]).all(v, v.?secret.?secretName.orValue('') != 'wowdecomp-toolchain-pull')",
                    "no-registry-credential-in-environment": "o.spec.containers.all(c, c.?env.orValue([]).all(e, e.?valueFrom.?secretKeyRef.?name.orValue('') != 'wowdecomp-toolchain-pull') && c.?envFrom.orValue([]).all(e, e.?secretRef.?name.orValue('') != 'wowdecomp-toolchain-pull'))",
                }
            )
        else:
            san = label("o", "homelab.cooked.beer/sanitizer") + " == 'true'"
            common.update(
                {
                    "drop-all-and-only-allow-ptrace": "o.spec.containers.all(c, 'ALL' in c.?securityContext.?capabilities.?drop.orValue([]) && c.?securityContext.?capabilities.?add.orValue([]).all(a,a == 'SYS_PTRACE'))",
                    "ptrace-only-in-sanitizer-jobs": f"({san}) || o.spec.containers.all(c, !('SYS_PTRACE' in c.?securityContext.?capabilities.?add.orValue([])))",
                    "ordinary-job-identity": f"({san}) || (o.spec.?securityContext.?runAsUser.orValue(-1) == 1000 && o.spec.?securityContext.?runAsNonRoot.orValue(false) && o.spec.containers.exists(c,c.name == 'job' && c.?securityContext.?runAsNonRoot.orValue(false) && c.?securityContext.?runAsUser.orValue(1000) == 1000))",
                    "bounded-sanitizer-identity": f"!({san}) || o.spec.containers.exists(c,c.name == 'job' && c.?securityContext.?runAsUser.orValue(-1) == 0 && c.?securityContext.?runAsGroup.orValue(-1) == 0 && c.?securityContext.?runAsNonRoot.orValue(true) == false && c.?securityContext.?capabilities.?add.orValue([]) == ['SYS_PTRACE'] && c.?securityContext.?capabilities.?drop.orValue([]) == ['ALL'])",
                }
            )
        for r in legacy["spec"]["rules"]:
            conditions = []
            if source == "gitea-job-security" and r["name"] in [
                "ptrace-only-in-sanitizer-jobs",
                "ordinary-job-identity",
                "bounded-sanitizer-identity",
            ]:
                sanitizer = label("object", "homelab.cooked.beer/sanitizer")
                positive = r["name"] == "bounded-sanitizer-identity"
                conditions = [
                    dict(
                        name="legacy-sanitizer-precondition",
                        expression=sanitizer
                        + (" == " if positive else " != ")
                        + "'true'",
                    )
                ]
                # The guard above carries the precondition. Admission tests show
                # legacy does not exempt pre-existing failures of these 3 rules.
                marker = " || "
                common[r["name"]] = common[r["name"]].split(marker, 1)[1]
            runners.append(
                policy(
                    source,
                    r["name"],
                    common[r["name"]],
                    [rule(["pods", "pods/ephemeralcontainers"], [""])],
                    ns=ns,
                    conditions=conditions,
                    existing=r["name"]
                    not in [
                        "ptrace-only-in-sanitizer-jobs",
                        "ordinary-job-identity",
                        "bounded-sanitizer-identity",
                    ],
                )
            )
    emit("policies/runners.yaml", runners)
    # Preserve each exception union, kinds/names/namespaces and governance metadata.
    exceptions = []
    for path in sorted((ROOT / "infra/kyverno/exceptions").glob("*.yaml")):
        old = yaml.safe_load(path.read_text())
        if old["kind"] != "PolicyException":
            continue
        refs = []
        for ex in old["spec"]["exceptions"]:
            refs += [
                dict(name=i["name"], kind=i["kind"])
                for i in INDEX
                if i["source_policy"] == ex["policyName"]
                and i["source_rule"] in ex["ruleNames"]
            ]
        union = []
        for m in old["spec"]["match"]["any"]:
            constraints = []
            for key in ["kinds", "namespaces", "names"]:
                vals = m["resources"].get(key)
                if not vals:
                    continue
                field = {
                    "kinds": "object.kind",
                    "namespaces": "request.namespace",
                    "names": "object.metadata.name",
                }[key]
                constraints.append(
                    "("
                    + " || ".join(
                        (
                            f"{field}.startsWith({lit(v[:-1])})"
                            if v.endswith("*")
                            else f"{field} == {lit(v)}"
                        )
                        for v in vals
                    )
                    + ")"
                )
            union.append(" && ".join(constraints))
        exceptions.append(
            dict(
                apiVersion="policies.kyverno.io/v1",
                kind="PolicyException",
                metadata=old["metadata"],
                spec=dict(
                    policyRefs=refs,
                    matchConditions=[
                        dict(
                            name="legacy-scope",
                            expression=" || ".join("(" + c + ")" for c in union),
                        )
                    ],
                ),
            )
        )
    emit("exceptions/exceptions.yaml", exceptions)
    old = yaml.safe_load(
        (ROOT / "infra/kyverno/policies/platform-namespace-bootstrap.yaml").read_text()
    )
    outputs = []
    for r in old["spec"]["rules"]:
        g = r["generate"]
        obj = copy.deepcopy(g["data"])
        obj.update(apiVersion=g["apiVersion"], kind=g["kind"])
        obj["metadata"]["name"] = g["name"]
        outputs.append(obj)
    expr = "[" + ", ".join("dyn(" + json.dumps(o) + ")" for o in outputs) + "]"
    gp = dict(
        apiVersion="policies.kyverno.io/v1",
        kind="GeneratingPolicy",
        metadata=dict(name=old["metadata"]["name"]),
        spec=dict(
            evaluation=dict(
                synchronize=dict(enabled=True),
                generateExisting=dict(enabled=False),
                orphanDownstreamOnPolicyDelete=dict(enabled=False),
            ),
            matchConstraints=dict(resourceRules=[rule(["namespaces"], [""])]),
            matchConditions=[
                dict(
                    name="bootstrap-label",
                    expression=label("object", "homelab.cooked.beer/bootstrap")
                    + " == 'enabled'",
                ),
                dict(
                    name="exclude-system",
                    expression=f'!(object.metadata.name in {lit(["default"]+SYSTEM)})',
                ),
            ],
            generate=[
                dict(expression="generator.apply(object.metadata.name, " + expr + ")")
            ],
        ),
    )
    emit("policies/generator.yaml", [gp])
    old_mutation = SOURCES["longhorn-csi-registrar-lifecycle"]
    refs = old_mutation["spec"]["rules"][0]["preconditions"]["all"][1]["value"]
    c = "object.spec.template.spec.containers[0]"
    # One short-circuit guard avoids indexing absent/empty fields. Kubernetes
    # schema validation rejects wrong types; those are tested separately.
    guard = (
        "request.namespace == 'longhorn-system' && object.metadata.name == 'longhorn-csi-plugin'"
        " && has(object.spec.template.spec.containers) && size(object.spec.template.spec.containers) > 0"
        f" && {c}.?name.orValue('') == 'node-driver-registrar'"
        f" && {c}.?image.orValue('') in {lit(refs)}"
        f" && has({c}.lifecycle) && has({c}.lifecycle.preStop) && has({c}.lifecycle.preStop.exec)"
        f" && has({c}.lifecycle.preStop.exec.command) && size({c}.lifecycle.preStop.exec.command) > 0"
        f" && {c}.lifecycle.preStop.exec.command[0] == '/bin/sh'"
    )
    emit("policies/mutation.yaml", [dict(
        apiVersion="policies.kyverno.io/v1", kind="MutatingPolicy",
        metadata=copy.deepcopy(old_mutation["metadata"]),
        spec=dict(
            matchConstraints=dict(resourceRules=[rule(["daemonsets"], ["apps"])]),
            matchConditions=[dict(name="exact-legacy-preconditions", expression=guard)],
            mutations=[dict(patchType="JSONPatch", jsonPatch=dict(expression=
                '[JSONPatch{op: "remove", path: "/spec/template/spec/containers/0/lifecycle/preStop"}]'))],
        ),
    )])
    # Native governance covers both APIs and needs no Kyverno admission dependency.
    good = f"{label('object',OWNER)} != '' && {ann('object','kyverno.cooked.beer/reason')} != '' && {ann('object','kyverno.cooked.beer/expires-at')} != '' && ({ann('object','github.cooked.beer/issue-create')} == 'true' || {ann('object','github.cooked.beer/issue-url')}.matches('^https://github[.]com/.*/issues/.*$'))"
    vap = dict(
        apiVersion="admissionregistration.k8s.io/v1",
        kind="ValidatingAdmissionPolicy",
        metadata=dict(name="policy-exception-accountability"),
        spec=dict(
            failurePolicy="Fail",
            matchConstraints=dict(
                resourceRules=[
                    rule(
                        ["policyexceptions"],
                        ["kyverno.io", "policies.kyverno.io"],
                        versions=["*"],
                    )
                ]
            ),
            matchConditions=[
                dict(
                    name="exception-namespace",
                    expression="request.namespace == 'kyverno'",
                )
            ],
            validations=[
                dict(
                    expression=good,
                    message="Exceptions require owner, reason, expiry and issue tracking",
                )
            ],
        ),
    )
    binding = dict(
        apiVersion="admissionregistration.k8s.io/v1",
        kind="ValidatingAdmissionPolicyBinding",
        metadata=dict(name="policy-exception-accountability"),
        spec=dict(
            policyName="policy-exception-accountability",
            validationActions=["Deny", "Audit"],
        ),
    )
    emit("policies/governance-native.yaml", [vap, binding])
    (OUT / "policy-map.json").write_text(json.dumps(INDEX, indent=2) + "\n")


if __name__ == "__main__":
    main()
