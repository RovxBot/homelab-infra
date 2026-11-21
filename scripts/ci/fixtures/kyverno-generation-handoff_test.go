package libs

// Run as a test-only overlay in an unmodified, version-matched Kyverno tree.
// These synthetic legacy-shaped objects demonstrate why name matching alone is
// not a lifecycle migration proof. No Kubernetes cluster is contacted.
import (
	"testing"

	"github.com/kyverno/kyverno/api/kyverno"
	"github.com/kyverno/kyverno/pkg/background/common"
	"github.com/kyverno/kyverno/pkg/clients/dclient"
	"github.com/stretchr/testify/require"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
)

func TestMigrationBoundaryLegacyShapedDownstream(t *testing.T) {
	for _, tc := range []struct {
		name, policy, trigger string
		updated               bool
	}{
		{"renamed-policy-does-not-adopt", "platform-namespace-bootstrap-cel", "namespace-uid", false},
		{"same-name-updates-but-drops-legacy-rule-label", "platform-namespace-bootstrap", "namespace-uid", true},
		{"different-trigger-does-not-adopt", "platform-namespace-bootstrap", "other-namespace-uid", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			existing := &unstructured.Unstructured{Object: map[string]any{
				"apiVersion": "v1", "kind": "ConfigMap",
				"metadata": map[string]any{
					"name": "data", "namespace": "tenant-ns",
					"uid": "downstream-uid", "resourceVersion": "7",
					"labels": map[string]any{
						kyverno.LabelAppManagedBy:      kyverno.ValueKyvernoApp,
						common.GeneratePolicyLabel:     "platform-namespace-bootstrap",
						common.GenerateRuleLabel:       "generate-default-limitrange",
						common.GenerateTriggerUIDLabel: "namespace-uid",
					},
				},
				"data": map[string]any{"quota": "old"},
			}}
			client, err := dclient.NewFakeClient(runtime.NewScheme(), nil, existing)
			require.NoError(t, err)
			client.SetDiscovery(dclient.NewFakeDiscoveryClient(nil))
			cp := &contextProvider{client: client, restMapper: generateTestRESTMapper()}
			cp.SetGenerateContext(tc.policy, "", "tenant-ns", "", "v1", "", "Namespace", tc.trigger, false, false)
			err = cp.GenerateResources("tenant-ns", []map[string]any{{
				"apiVersion": "v1", "kind": "ConfigMap",
				"metadata": map[string]any{"name": "data"},
				"data":     map[string]any{"quota": "new"},
			}})
			require.NoError(t, err)
			actual, err := client.GetResource(t.Context(), "v1", "ConfigMap", "tenant-ns", "data")
			require.NoError(t, err)
			require.Equal(t, existing.GetUID(), actual.GetUID())
			quota, _, err := unstructured.NestedString(actual.Object, "data", "quota")
			require.NoError(t, err)
			if tc.updated {
				require.Len(t, cp.GetGeneratedResources(), 1)
				require.Equal(t, "new", quota)
				require.Empty(t, actual.GetLabels()[common.GenerateRuleLabel])
			} else {
				require.Empty(t, cp.GetGeneratedResources())
				require.Equal(t, "old", quota)
				require.Equal(t, "generate-default-limitrange", actual.GetLabels()[common.GenerateRuleLabel])
			}
		})
	}
}
