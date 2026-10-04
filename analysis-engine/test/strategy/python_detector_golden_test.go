package strategy_test

import (
	"encoding/json"
	"os"
	"testing"
)

// TestPythonDetectorGoldenManifest keeps the detector-level oracle artifact
// visible to CI. The values were produced by calling the frozen Python
// detectors at oracle_commit, rather than by copying Go expectations into a
// fixture. Go strategy parity tests can consume the same bars as each port is
// completed.
func TestPythonDetectorGoldenManifest(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_detector_goldens.json")
	if err != nil {
		t.Fatal(err)
	}
	var manifest struct {
		OracleCommit string `json:"oracle_commit"`
		Oracle       string `json:"oracle"`
		Cases        []struct {
			Name     string      `json:"name"`
			Bars     [][]float64 `json:"bars"`
			Expected struct {
				Setup     string  `json:"setup"`
				Direction string  `json:"direction"`
				KeyLevel  float64 `json:"key_level"`
			} `json:"expected"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &manifest); err != nil {
		t.Fatal(err)
	}
	if manifest.OracleCommit != "1c9f323" || manifest.Oracle == "" {
		t.Fatalf("golden is missing frozen Python provenance: %+v", manifest)
	}
	if len(manifest.Cases) != 4 {
		t.Fatalf("expected Snap Back, Momentum, Fade, and Range Edge goldens; got %d", len(manifest.Cases))
	}
	for _, c := range manifest.Cases {
		if c.Name == "" || len(c.Bars) < 5 || c.Expected.Setup == "" || c.Expected.Direction == "" {
			t.Fatalf("incomplete detector golden case: %+v", c)
		}
	}
}
