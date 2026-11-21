package handlers

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/go-logr/logr"
	"github.com/kyverno/kyverno/pkg/config"
	"github.com/stretchr/testify/require"
	admissionv1 "k8s.io/api/admission/v1"
	authenticationv1 "k8s.io/api/authentication/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime/schema"
)

func TestHomelabSARFilterAndPrefilterProtection(t *testing.T) {
	var cm corev1.ConfigMap
	b, err := os.ReadFile(filepath.Join(os.Getenv("SAR_FIXTURES"), "candidate-config.json"))
	require.NoError(t, err)
	require.NoError(t, json.Unmarshal(b, &cm))
	cfg := config.NewDefaultConfiguration(false)
	cfg.Load(&cm)
	for _, kind := range []string{"SubjectAccessReview", "SelfSubjectAccessReview", "LocalSubjectAccessReview"} {
		for _, username := range []string{"synthetic-user", "system:serviceaccount:kyverno:kyverno-admission-controller"} {
			t.Run(kind+"/"+username, func(t *testing.T) {
				called := false
				inner := AdmissionHandler(func(context.Context, logr.Logger, AdmissionRequest, time.Time) AdmissionResponse {
					called = true
					return AdmissionResponse{Allowed: true}
				})
				gvk := schema.GroupVersionKind{Group: "authorization.k8s.io", Version: "v1", Kind: kind}
				r := AdmissionRequest{AdmissionRequest: admissionv1.AdmissionRequest{
					UID: "synthetic", Operation: admissionv1.Create, Kind: metav1.GroupVersionKind(gvk),
					UserInfo: authenticationv1.UserInfo{Username: username},
				}, GroupVersionKind: gvk}
				r.Object.Raw = []byte(`{"apiVersion":"authorization.k8s.io/v1","kind":"` + kind + `","spec":{}}`)
				h := inner.WithFilter(cfg).WithGenerateProvenanceProtection("system:serviceaccount:kyverno:kyverno-admission-controller").WithProtection(false)
				response := h(context.Background(), logr.Discard(), r, time.Now())
				require.True(t, response.Allowed)
				require.Equal(t, kind == "LocalSubjectAccessReview", called, "only the two proven review kinds skip policy evaluation")
			})
		}
	}
}
