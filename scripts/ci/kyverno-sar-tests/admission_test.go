package sartest

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	ar "k8s.io/api/admissionregistration/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apiserver/pkg/admission"
	cel "k8s.io/apiserver/pkg/admission/plugin/cel"
	webhook "k8s.io/apiserver/pkg/admission/plugin/webhook"
	"k8s.io/apiserver/pkg/admission/plugin/webhook/predicates/namespace"
	"k8s.io/apiserver/pkg/admission/plugin/webhook/predicates/rules"
	"k8s.io/apiserver/pkg/authentication/user"
	"k8s.io/apiserver/pkg/authorization/authorizer"
	"k8s.io/apiserver/pkg/cel/environment"
	request "k8s.io/apiserver/pkg/endpoints/request"
	auth "k8s.io/kubernetes/pkg/apis/authorization"
	selfrest "k8s.io/kubernetes/pkg/registry/authorization/selfsubjectaccessreview"
	subjectrest "k8s.io/kubernetes/pkg/registry/authorization/subjectaccessreview"
)

func readJSON(t *testing.T, file string, target any) {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(os.Getenv("SAR_FIXTURES"), file))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(b, target); err != nil {
		t.Fatal(err)
	}
}

// Use the API server's compiler, typed request construction, rule matcher and
// failure-policy handling. A hand-translated CEL predicate would miss errors.
func matches(t *testing.T, h ar.ValidatingWebhook, group, resource, ns string, op admission.Operation) (bool, error) {
	t.Helper()
	a := admission.NewAttributesRecord(nil, nil, schema.GroupVersionKind{Group: group, Version: "v1", Kind: "Synthetic"}, ns, "", schema.GroupVersionResource{Group: group, Version: "v1", Resource: resource}, "", op, nil, false, nil)
	ruleMatch := false
	for _, r := range h.Rules {
		m := rules.Matcher{Rule: r, Attr: a}
		ruleMatch = ruleMatch || m.Matches()
	}
	if !ruleMatch {
		return false, nil
	}
	accessor := webhook.NewValidatingWebhookAccessor("test", "synthetic", &h)
	// For cluster-scoped requests this must succeed without a namespace lister.
	if ns == "" {
		nm := namespace.Matcher{}
		ok, err := nm.MatchNamespaceSelector(accessor, a)
		if err != nil {
			return false, err
		}
		if !ok {
			return false, nil
		}
	}
	v := &admission.VersionedAttributes{Attributes: a, VersionedKind: a.GetKind()}
	c := cel.NewConditionCompiler(environment.MustBaseEnvSet(environment.DefaultCompatibilityVersion(), true))
	r := accessor.GetCompiledMatcher(c).Match(context.Background(), v, nil, nil)
	return r.Matches, r.Error
}

func TestKubernetes134Matching(t *testing.T) {
	var baseline, candidate, policy ar.ValidatingWebhook
	readJSON(t, "baseline-resource.json", &baseline)
	readJSON(t, "candidate-resource.json", &candidate)
	readJSON(t, "candidate-policy.json", &policy)
	for _, tc := range []struct {
		name, group, resource, ns string
		op                        admission.Operation
		before, after             bool
	}{
		{"SAR", "authorization.k8s.io", "subjectaccessreviews", "", admission.Create, true, false},
		{"self-SAR", "authorization.k8s.io", "selfsubjectaccessreviews", "", admission.Create, true, false},
		{"local-SAR", "authorization.k8s.io", "localsubjectaccessreviews", "tenant", admission.Create, true, true},
		{"self-rules", "authorization.k8s.io", "selfsubjectrulesreviews", "", admission.Create, true, true},
		{"SAR-update", "authorization.k8s.io", "subjectaccessreviews", "", admission.Update, true, true},
		{"SAR-delete", "authorization.k8s.io", "subjectaccessreviews", "", admission.Delete, true, true},
		{"different-group", "example.invalid", "subjectaccessreviews", "", admission.Create, true, true},
		{"empty-group", "", "subjectaccessreviews", "", admission.Create, true, true},
		{"empty-resource", "authorization.k8s.io", "", "", admission.Create, true, true},
		{"unknown-auth-resource", "authorization.k8s.io", "unsupportedreviews", "", admission.Create, true, true},
		{"unsupported-operation", "authorization.k8s.io", "subjectaccessreviews", "", admission.Operation("UNSUPPORTED"), false, false},
		{"Deployment-create", "apps", "deployments", "tenant", admission.Create, true, true},
		{"Deployment-update", "apps", "deployments", "tenant", admission.Update, true, true},
		{"ClusterPolicy-update", "kyverno.io", "clusterpolicies", "", admission.Update, true, true},
		{"Policy-update", "kyverno.io", "policies", "tenant", admission.Update, true, true},
		{"Longhorn", "longhorn.io", "volumes", "longhorn-system", admission.Update, false, false},
		{"Longhorn-BackupTarget", "longhorn.io", "backuptargets", "longhorn-system", admission.Update, true, true},
		{"CiliumNode", "cilium.io", "ciliumnodes", "", admission.Update, false, false},
		{"Cilium-policy", "cilium.io", "ciliumnetworkpolicies", "tenant", admission.Update, true, true},
		{"node-lease", "coordination.k8s.io", "leases", "kube-node-lease", admission.Update, false, false},
		{"other-lease", "coordination.k8s.io", "leases", "tenant", admission.Update, true, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			for i, h := range []ar.ValidatingWebhook{baseline, candidate} {
				want := []bool{tc.before, tc.after}[i]
				got, err := matches(t, h, tc.group, tc.resource, tc.ns, tc.op)
				if err != nil || got != want {
					t.Fatalf("candidate=%t: match=%t error=%v, want %t", i == 1, got, err, want)
				}
			}
		})
	}
	for _, resource := range []string{"clusterpolicies", "policies"} {
		for _, op := range []admission.Operation{admission.Create, admission.Update} {
			got, err := matches(t, policy, "kyverno.io", resource, "", op)
			if !got || err != nil {
				t.Fatalf("policy admission disabled: %s %s: %v", resource, op, err)
			}
		}
	}
	// A new error must reject a still-matching Deployment with Fail. Conditions
	// are ANDed: an explicit false can skip even alongside an error, by design.
	broken := *candidate.DeepCopy()
	broken.MatchConditions = append(broken.MatchConditions, ar.MatchCondition{Name: "synthetic-error", Expression: "request.name.size() / 0 > 1"})
	if got, err := matches(t, broken, "apps", "deployments", "tenant", admission.Create); got || err == nil {
		t.Fatalf("CEL evaluation did not fail closed: %t %v", got, err)
	}
}

type permit struct {
	calls    int
	decision authorizer.Decision
}

func (p *permit) Authorize(context.Context, authorizer.Attributes) (authorizer.Decision, string, error) {
	p.calls++
	return p.decision, "synthetic", nil
}

func TestNativeReviewValidationBeforeAdmission(t *testing.T) {
	// Both native REST handlers must reject malformed specs and all metadata
	// relevant to Kyverno's pre-filter guards before calling admission or authz.
	for _, self := range []bool{false, true} {
		for _, tc := range []struct {
			name        string
			meta        metav1.ObjectMeta
			invalidSpec bool
			valid       bool
		}{
			{name: "valid", valid: true},
			{name: "provenance", meta: metav1.ObjectMeta{Annotations: map[string]string{"generate.kyverno.io/provenance": "v1:forged"}}},
			{name: "empty-provenance", meta: metav1.ObjectMeta{Annotations: map[string]string{"generate.kyverno.io/provenance": ""}}},
			{name: "routing-label", meta: metav1.ObjectMeta{Labels: map[string]string{"generate.kyverno.io/policy-name": "test"}}},
			{name: "managed-label", meta: metav1.ObjectMeta{Labels: map[string]string{"app.kubernetes.io/managed-by": "kyverno"}}},
			{name: "namespace", meta: metav1.ObjectMeta{Namespace: "tenant"}},
			{name: "name", meta: metav1.ObjectMeta{Name: "test"}},
			{name: "invalid-spec", invalidSpec: true},
		} {
			name := "SAR/"
			if self {
				name = "self-SAR/"
			}
			t.Run(name+tc.name, func(t *testing.T) {
				p := &permit{decision: authorizer.DecisionAllow}
				admissions := 0
				validate := func(context.Context, runtime.Object) error { admissions++; return nil }
				ctx := context.Background()
				var err error
				attrs := &auth.ResourceAttributes{Namespace: "tenant", Verb: "get", Resource: "pods"}
				if tc.invalidSpec {
					attrs = nil
				}
				if self {
					// Self-SAR requires an authenticated context, supplied locally.
					ctx = withUser(ctx)
					_, err = selfrest.NewREST(p).Create(ctx, &auth.SelfSubjectAccessReview{ObjectMeta: tc.meta, Spec: auth.SelfSubjectAccessReviewSpec{ResourceAttributes: attrs}}, validate, &metav1.CreateOptions{})
				} else {
					_, err = subjectrest.NewREST(p).Create(ctx, &auth.SubjectAccessReview{ObjectMeta: tc.meta, Spec: auth.SubjectAccessReviewSpec{User: "synthetic-user", ResourceAttributes: attrs}}, validate, &metav1.CreateOptions{})
				}
				if tc.valid {
					if err != nil || admissions != 1 || p.calls != 1 {
						t.Fatalf("valid review: err=%v admission=%d authz=%d", err, admissions, p.calls)
					}
				} else if err == nil || admissions != 0 || p.calls != 0 {
					t.Fatalf("invalid review reached admission/authorization: err=%v admission=%d authz=%d", err, admissions, p.calls)
				}
			})
		}
	}
}

func TestNativeAuthorizationWithoutWebhook(t *testing.T) {
	for _, decision := range []authorizer.Decision{authorizer.DecisionAllow, authorizer.DecisionDeny} {
		for _, self := range []bool{false, true} {
			p := &permit{decision: decision}
			attrs := &auth.ResourceAttributes{Namespace: "tenant", Verb: "get", Resource: "pods"}
			var result runtime.Object
			var err error
			if self {
				result, err = selfrest.NewREST(p).Create(withUser(context.Background()), &auth.SelfSubjectAccessReview{Spec: auth.SelfSubjectAccessReviewSpec{ResourceAttributes: attrs}}, nil, &metav1.CreateOptions{})
			} else {
				result, err = subjectrest.NewREST(p).Create(context.Background(), &auth.SubjectAccessReview{Spec: auth.SubjectAccessReviewSpec{User: "synthetic-user", ResourceAttributes: attrs}}, nil, &metav1.CreateOptions{})
			}
			if err != nil || p.calls != 1 {
				t.Fatalf("authorizer not called: %v calls=%d", err, p.calls)
			}
			var status auth.SubjectAccessReviewStatus
			if self {
				status = result.(*auth.SelfSubjectAccessReview).Status
			} else {
				status = result.(*auth.SubjectAccessReview).Status
			}
			if status.Allowed != (decision == authorizer.DecisionAllow) || status.Denied != (decision == authorizer.DecisionDeny) {
				t.Fatalf("authorization result changed without admission: %#v", status)
			}
		}
	}
}

func withUser(ctx context.Context) context.Context {
	return request.WithUser(ctx, &user.DefaultInfo{Name: "synthetic-user"})
}
