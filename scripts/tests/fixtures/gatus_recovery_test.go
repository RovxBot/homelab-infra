// Run in the pinned upstream Gatus checkout; see docs/runbooks/gatus-recovery.md.
package main_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/TwiN/gatus/v5/config"
	"github.com/TwiN/gatus/v5/config/endpoint"
	"github.com/TwiN/gatus/v5/storage"
	"github.com/TwiN/gatus/v5/storage/store"
	"github.com/TwiN/gatus/v5/storage/store/common/paging"
	"github.com/TwiN/gatus/v5/watchdog"
	"gopkg.in/yaml.v3"
)

func TestHomelabRecovery(t *testing.T) {
	// Never start monitoring or send requests to a real endpoint or GitHub.
	raw, err := os.ReadFile(os.Getenv("HOMELAB_GATUS_CONFIG"))
	if err != nil {
		t.Fatal(err)
	}
	var candidate config.Config
	if err := yaml.Unmarshal(raw, &candidate); err != nil {
		t.Fatal(err)
	}
	if candidate.Storage.Type != storage.TypeSQLite || candidate.Storage.Path != "/data/data.db" {
		t.Fatal("the deployed SQLite storage configuration changed")
	}
	creates, closes := 0, 0
	title := "alert(gatus): cluster/cluster-health"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == "GET" && strings.HasSuffix(r.URL.Path, "/user"):
			_, _ = w.Write([]byte(`{"login":"fixture"}`))
		case r.Method == "POST" && strings.HasSuffix(r.URL.Path, "/issues"):
			creates++
			w.WriteHeader(http.StatusCreated)
			_, _ = w.Write([]byte(`{"number":1}`))
		case r.Method == "GET" && strings.HasSuffix(r.URL.Path, "/issues"):
			if r.URL.Query().Get("creator") != "fixture" || r.URL.Query().Get("state") != "open" {
				t.Error("native recovery lost its creator/open filters")
			}
			_ = json.NewEncoder(w).Encode([]map[string]any{{"number": 1, "title": title}})
		case r.Method == "PATCH" && strings.HasSuffix(r.URL.Path, "/issues/1"):
			var payload map[string]any
			if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
				t.Error(err)
			}
			if payload["state"] != "closed" {
				t.Error("expected a resolved notification")
			}
			if _, present := payload["body"]; present {
				t.Error("native closure overwrote diagnostics")
			}
			closes++
			_, _ = w.Write([]byte(`{"number":1}`))
		default:
			t.Errorf("unexpected API request: %s %s", r.Method, r.URL)
			w.WriteHeader(http.StatusBadRequest)
		}
	}))
	defer server.Close()
	candidate.Alerting.GitHub.DefaultConfig.RepositoryURL = server.URL + "/fixture/repo"
	candidate.Alerting.GitHub.DefaultConfig.Token = "synthetic"
	candidate.Storage.Path = filepath.Join(t.TempDir(), "gatus.db")
	raw, err = yaml.Marshal(candidate)
	if err != nil {
		t.Fatal(err)
	}
	configPath := filepath.Join(t.TempDir(), "config.yaml")
	if err := os.WriteFile(configPath, raw, 0600); err != nil {
		t.Fatal(err)
	}
	cfg, err := config.LoadConfiguration(configPath)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Alerting.GitHub == nil {
		t.Fatal("GitHub alert config was rejected")
	}
	if cfg.Storage.MaximumNumberOfResults != 100 {
		t.Fatal("unexpected endpoint history limit")
	}
	var cluster *endpoint.Endpoint
	for _, ep := range cfg.Endpoints {
		for _, a := range ep.Alerts {
			expected := 2
			if ep.Name == "cluster-health" && ep.Group == "cluster" {
				expected = 20
				cluster = ep
			}
			if a.FailureThreshold != 3 || a.SuccessThreshold != expected || !a.IsSendingOnResolved() {
				t.Fatalf("incorrect effective alert for %s: %+v", ep.DisplayName(), a)
			}
		}
	}
	if cluster == nil || cluster.Interval != time.Minute {
		t.Fatal("missing one-minute cluster endpoint")
	}
	if err := store.Initialize(cfg.Storage); err != nil {
		t.Fatal(err)
	}
	defer func() { store.Get().Close() }()
	step := 0
	observe := func(success bool) {
		step++
		result := &endpoint.Result{Success: success, Timestamp: time.Now().Add(time.Duration(step-100) * time.Minute)}
		if err := store.Get().InsertEndpointResult(cluster, result); err != nil {
			t.Fatal(err)
		}
		watchdog.HandleAlerting(cluster, result, cfg.Alerting)
	}
	for i := 1; i <= 3; i++ {
		observe(false)
		expected := 0
		if i == 3 {
			expected = 1
		}
		if creates != expected || closes != 0 {
			t.Fatalf("wrong failure detection at sample %d", i)
		}
	}
	for i := 1; i <= 19; i++ {
		observe(true)
		if creates != 1 || closes != 0 || !cluster.Alerts[0].Triggered {
			t.Fatalf("premature recovery after %d healthy samples", i)
		}
	}
	observe(false)
	if cluster.NumberOfSuccessesInARow != 0 {
		t.Fatal("failure did not reset recovery")
	}
	for i := 1; i <= 20; i++ {
		observe(true)
		if i == 10 {
			// The triggered incident and partial recovery survive an ordinary
			// restart with the same configuration and existing SQLite schema.
			store.Get().Close()
			if err := store.Initialize(cfg.Storage); err != nil {
				t.Fatal(err)
			}
			exists, _, count, err := store.Get().GetTriggeredEndpointAlert(cluster, cluster.Alerts[0])
			if err != nil || !exists || count != 10 {
				t.Fatalf("SQLite recovery persistence: %v %v %d", err, exists, count)
			}
			cluster.NumberOfSuccessesInARow = count
		}
		expected := 0
		if i == 20 {
			expected = 1
		}
		if creates != 1 || closes != expected {
			t.Fatalf("wrong recovery notification after %d samples", i)
		}
	}
	statuses, err := store.Get().GetAllEndpointStatuses(paging.NewEndpointStatusParams().WithResults(1, 100))
	if err != nil || len(statuses) != 1 || len(statuses[0].Results) < 20 {
		t.Fatalf("SQLite API history cannot satisfy recovery: %v", err)
	}
}
