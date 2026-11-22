package services

import (
    "context"
    "fmt"
    "testing"
    "time"

    "github.com/kubescape/kubevuln/core/domain"
    "github.com/kubescape/kubevuln/repositories"
    "github.com/stretchr/testify/assert"
    "github.com/stretchr/testify/require"
    metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// Default callers retain upstream caching. Only explicit boolean true performs
// new matching; it must preserve the existing persisted object's identity.
func TestScanCVEHomelabForceRescan(t *testing.T) {
    for _, value := range []interface{}{nil, false, "true", true} {
        t.Run(fmt.Sprintf("%v-%T", value, value), func(t *testing.T) {
            repo := repositories.NewFakeAPIServerStorage("storage")
            scanner := &timestampTestScanner{dbVersion: "unchanged-db", reportTime: reportTimeForTest()}
            service, sbomVersion, cveVersion, dbVersion, ctx := newScanCVETestService(t, &recordingPlatform{}, repo, scanner)
            require.NoError(t, service.ScanCVE(ctx))
            require.Equal(t, 1, scanner.calls)
            original, err := repo.StorageClient.VulnerabilityManifests("storage").Get(ctx, "imageSlug", metav1.GetOptions{})
            require.NoError(t, err)
            original.UID = "existing-report"
            original.CreationTimestamp = metav1.NewTime(reportTimeForTest().Add(-time.Hour))
            _, err = repo.StorageClient.VulnerabilityManifests("storage").Update(ctx, original, metav1.UpdateOptions{})
            require.NoError(t, err)

            scanner.reportTime = reportTimeForTest().Add(time.Hour)
            workload := ctx.Value(domain.WorkloadKey{}).(domain.ScanCommand)
            workload.Args = map[string]interface{}{"forceRescan": value}
            ctx = context.WithValue(ctx, domain.WorkloadKey{}, workload)
            require.NoError(t, service.ScanCVE(ctx))
            stored, err := repo.GetCVE(ctx, "imageSlug", sbomVersion, cveVersion, dbVersion)
            require.NoError(t, err)
            raw, err := repo.StorageClient.VulnerabilityManifests("storage").Get(ctx, "imageSlug", metav1.GetOptions{})
            require.NoError(t, err)
            assert.Equal(t, original.UID, raw.UID)
            assert.Equal(t, original.CreationTimestamp, raw.CreationTimestamp)
            assert.Equal(t, dbVersion, raw.Spec.Metadata.Tool.DatabaseVersion)
            if value == true {
                assert.Equal(t, 2, scanner.calls)
                assert.Equal(t, scanner.reportTime, stored.ReportCreatedAt)
            } else {
                assert.Equal(t, 1, scanner.calls)
                assert.Equal(t, original.Spec.Metadata.Report.CreatedAt.Time, stored.ReportCreatedAt)
            }
        })
    }
}
