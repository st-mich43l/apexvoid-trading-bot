package brreplay_test

import (
	"fmt"
	"os"
	"sort"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/breakretest"
)

type variant struct {
	name   string
	adjust func(*breakretest.Config)
}

// TestBreakRetestFunnelAcrossConfigurationVariants prints, for every capture,
// how many distinct break episodes reach each stage and how they end, under a
// few explicit configurations. It exists to show WHERE the contract is selective
// (and to choose defaults from geometry and evidence); it asserts nothing.
//
//	BR_FUNNEL=1 go test ./test/brreplay -run Funnel -v
func TestBreakRetestFunnelAcrossConfigurationVariants(t *testing.T) {
	if os.Getenv("BR_FUNNEL") == "" {
		t.Skip("BR_FUNNEL not set")
	}
	doc := resolveDoc(t)
	variants := []variant{
		{"production", func(c *breakretest.Config) {}},
		{"protected=break_origin", func(c *breakretest.Config) { c.ProtectedStructure = breakretest.ProtectedBreakOrigin }},
		{"no envelope cap", func(c *breakretest.Config) { c.ExecutionStopMaxPips = 0 }},
		{"origin + no cap", func(c *breakretest.Config) {
			c.ProtectedStructure, c.ExecutionStopMaxPips = breakretest.ProtectedBreakOrigin, 0
		}},
		{"accept=1 close", func(c *breakretest.Config) { c.BreakoutAcceptBars = 1 }},
		{"accept=3 closes", func(c *breakretest.Config) { c.BreakoutAcceptBars = 3 }},
		{"level touches=3", func(c *breakretest.Config) { c.LevelMinTouches = 3 }},
		{"no break-force gates", func(c *breakretest.Config) {
			c.MinBreakBodyRatio, c.MinBreakCloseStrength, c.MinBreakDisplacement = 0, 0, 0
		}},
		{"target lookback 288", func(c *breakretest.Config) { c.TargetLookbackBars = 288 }},
		{"target swing 0.5 ATR", func(c *breakretest.Config) { c.TargetSwingATR = 0.5 }},
		{"origin + lookback 288", func(c *breakretest.Config) {
			c.ProtectedStructure, c.TargetLookbackBars = breakretest.ProtectedBreakOrigin, 288
		}},
		{"confirmation window 5", func(c *breakretest.Config) { c.ConfirmationWindowBars = 5 }},
	}
	var out strings.Builder
	for _, c := range captures {
		data := loadCapture(t, doc, c.File)
		fmt.Fprintf(&out, "\n== %s\n", c.Label)
		for _, v := range variants {
			cfg := data.cfg
			v.adjust(&cfg)
			final := map[string]string{}
			for i := range data.m5 {
				for _, ep := range breakretest.DetectAsOf(cfg, data.m5, i).Episodes {
					outcome := string(ep.State)
					if ep.Reason != "" {
						outcome += ":" + ep.Reason
					}
					if ep.State == breakretest.StateCandidate {
						outcome = "PUBLISHED"
					}
					if final[ep.Key()] != "PUBLISHED" {
						final[ep.Key()] = outcome
					}
				}
			}
			counts := map[string]int{}
			for _, o := range final {
				counts[o]++
			}
			keys := make([]string, 0, len(counts))
			for k := range counts {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			parts := make([]string, len(keys))
			for i, k := range keys {
				parts[i] = fmt.Sprintf("%s=%d", k, counts[k])
			}
			fmt.Fprintf(&out, "  %-26s episodes=%-4d published=%-3d %s\n", v.name, len(final), counts["PUBLISHED"], strings.Join(parts, " "))
		}
	}
	t.Log("\n" + out.String())
}
