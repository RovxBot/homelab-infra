package webhook

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	kyvernov1 "github.com/kyverno/kyverno/api/kyverno/v1"
	versionedfake "github.com/kyverno/kyverno/pkg/client/clientset/versioned/fake"
	informers "github.com/kyverno/kyverno/pkg/client/informers/externalversions"
	"github.com/kyverno/kyverno/pkg/config"
	"github.com/stretchr/testify/require"
	ar "k8s.io/api/admissionregistration/v1"
	coordinationv1 "k8s.io/api/coordination/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	kubefake "k8s.io/client-go/kubernetes/fake"
	arlisters "k8s.io/client-go/listers/admissionregistration/v1"
	coordinationlisters "k8s.io/client-go/listers/coordination/v1"
	corelisters "k8s.io/client-go/listers/core/v1"
	rbaclisters "k8s.io/client-go/listers/rbac/v1"
	"k8s.io/client-go/tools/cache"
)

func sarRead(t *testing.T, name string, target any) {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(os.Getenv("SAR_FIXTURES"), name))
	require.NoError(t, err)
	require.NoError(t, json.Unmarshal(b, target))
}

func sarWrite(t *testing.T, name string, target any) {
	t.Helper()
	b, err := json.Marshal(target)
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(os.Getenv("SAR_FIXTURES"), name), b, 0600))
}

// This test is overlaid into the pinned upstream package without modifying any
// application source. It executes the real controller and fake Kubernetes API.
func TestHomelabSARReconciliation(t *testing.T) {
	ctx := context.Background()
	var baseCM, candidateCM corev1.ConfigMap
	var guardrail kyvernov1.ClusterPolicy
	sarRead(t, "baseline-config.json", &baseCM)
	sarRead(t, "candidate-config.json", &candidateCM)
	sarRead(t, "guardrail.json", &guardrail)
	factory := informers.NewSharedInformerFactory(versionedfake.NewSimpleClientset(), 0)
	require.NoError(t, factory.Kyverno().V1().ClusterPolicies().Informer().GetIndexer().Add(&guardrail))
	leases := cache.NewIndexer(cache.MetaNamespaceKeyFunc, cache.Indexers{})
	require.NoError(t, leases.Add(&coordinationv1.Lease{ObjectMeta: metav1.ObjectMeta{
		Name: "kyverno-health", Namespace: config.KyvernoNamespace(),
		Annotations: map[string]string{AnnotationLastRequestTime: time.Now().Format(time.RFC3339)},
	}}))
	secrets := cache.NewIndexer(cache.MetaNamespaceKeyFunc, cache.Indexers{})
	require.NoError(t, secrets.Add(&corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: "synthetic-ca", Namespace: config.KyvernoNamespace()}, Data: map[string][]byte{corev1.TLSCertKey: []byte("synthetic-ca")}}))
	index := cache.NewIndexer(cache.MetaNamespaceKeyFunc, cache.Indexers{})
	client := kubefake.NewClientset()
	cfg := config.NewDefaultConfiguration(false)
	cfg.Load(&baseCM)
	changed := 0
	cfg.OnChanged(func() { changed++ })
	c := &controller{
		defaultTimeout: 10, servicePort: 443, autoUpdateWebhooks: true, caSecretName: "synthetic-ca",
		configuration:      cfg,
		vwcClient:          client.AdmissionregistrationV1().ValidatingWebhookConfigurations(),
		vwcLister:          arlisters.NewValidatingWebhookConfigurationLister(index),
		secretLister:       corelisters.NewSecretLister(secrets),
		clusterroleLister:  rbaclisters.NewClusterRoleLister(cache.NewIndexer(cache.MetaNamespaceKeyFunc, cache.Indexers{})),
		leaseLister:        coordinationlisters.NewLeaseLister(leases),
		cpolLister:         factory.Kyverno().V1().ClusterPolicies().Lister(),
		polLister:          factory.Kyverno().V1().Policies().Lister(),
		vpolLister:         factory.Policies().V1beta1().ValidatingPolicies().Lister(),
		nvpolLister:        factory.Policies().V1beta1().NamespacedValidatingPolicies().Lister(),
		gpolLister:         factory.Policies().V1beta1().GeneratingPolicies().Lister(),
		ngpolLister:        factory.Policies().V1beta1().NamespacedGeneratingPolicies().Lister(),
		ivpolLister:        factory.Policies().V1beta1().ImageValidatingPolicies().Lister(),
		nivpolLister:       factory.Policies().V1beta1().NamespacedImageValidatingPolicies().Lister(),
		celExpressionCache: NewExpressionCache(),
	}
	get := func(name string) *ar.ValidatingWebhookConfiguration {
		v, err := client.AdmissionregistrationV1().ValidatingWebhookConfigurations().Get(ctx, name, metav1.GetOptions{})
		require.NoError(t, err)
		require.NoError(t, index.Update(v.DeepCopy())) // simulate informer delivery
		return v
	}
	reconcile := func() (*ar.ValidatingWebhookConfiguration, *ar.ValidatingWebhookConfiguration) {
		require.NoError(t, c.reconcileResourceValidatingWebhookConfiguration(ctx))
		r := get(config.ValidatingWebhookConfigurationName)
		require.NoError(t, c.reconcilePolicyValidatingWebhookConfiguration(ctx))
		return r, get(config.PolicyValidatingWebhookConfigurationName)
	}
	baseResource, basePolicy := reconcile()
	require.Len(t, baseResource.Webhooks, 2, "wildcard Flux guardrail plus metadata protection")
	find := func(v *ar.ValidatingWebhookConfiguration) ar.ValidatingWebhook {
		for _, h := range v.Webhooks {
			if h.Name == config.ValidatingWebhookName+"-fail" {
				return h
			}
		}
		t.Fatal("missing fail-closed resource webhook")
		return ar.ValidatingWebhook{}
	}
	sarWrite(t, "baseline-resource.json", find(baseResource))
	cfg.Load(&candidateCM)
	require.Equal(t, 1, changed, "ConfigMap reload must notify the webhook controller")
	for round := 0; round < 3; round++ {
		resource, policy := reconcile()
		require.Equal(t, basePolicy.Webhooks, policy.Webhooks, "policy validation must be identical")
		h := find(resource)
		require.Equal(t, cfg.GetMatchConditions(), h.MatchConditions)
		require.Equal(t, ar.Fail, *h.FailurePolicy)
		require.EqualValues(t, 10, *h.TimeoutSeconds)
		// Only the global condition changes; rules/selectors/metadata protection
		// remain identical, including the wildcard guardrail admission scope.
		comparison := resource.DeepCopy()
		for i := range comparison.Webhooks {
			if comparison.Webhooks[i].Name == h.Name {
				comparison.Webhooks[i].MatchConditions = find(baseResource).MatchConditions
			}
		}
		require.True(t, reflect.DeepEqual(baseResource.Webhooks, comparison.Webhooks))
		sarWrite(t, "candidate-resource.json", h)
		sarWrite(t, "candidate-policy.json", policy.Webhooks[0])
		// Deliberate drift in the fake API must be corrected, not retained.
		for i := range resource.Webhooks {
			if resource.Webhooks[i].Name == h.Name {
				resource.Webhooks[i].MatchConditions = nil
			}
		}
		_, err := client.AdmissionregistrationV1().ValidatingWebhookConfigurations().Update(ctx, resource, metav1.UpdateOptions{})
		require.NoError(t, err)
		get(resource.Name)
		// Reparse the same ConfigMap to simulate a controller restart.
		fresh := config.NewDefaultConfiguration(false)
		fresh.Load(&candidateCM)
		c.configuration = fresh
	}
	// Reverting only the added condition restores the entire previous routing.
	c.configuration = cfg
	cfg.Load(&baseCM)
	rolledBack, _ := reconcile()
	require.Equal(t, baseResource.Webhooks, rolledBack.Webhooks)
}
