package crtreplay_test

import (
	"fmt"
	"os"
	"sort"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy/crt"
)

type variant struct {
	name   string
	adjust func(*crt.Config)
}

// TestCRTFunnelAcrossConfigurationVariants prints, for every capture, how many
// distinct technical episodes each stage of the CRT contract keeps or refuses
// under a few explicit configurations. It exists to show WHERE the contract is
// selective (and to choose defaults from evidence); it asserts nothing.
//
//	CRT_FUNNEL=1 go test ./test/crtreplay -run Funnel -v
func TestCRTFunnelAcrossConfigurationVariants(t *testing.T) {
	if os.Getenv("CRT_FUNNEL") == "" {
		t.Skip("CRT_FUNNEL not set")
	}
	doc := resolveDoc(t)
	variants := []variant{
		{"production (mss, reclaim_retest)", func(c *crt.Config) {}},
		{"mss, mss_retest", func(c *crt.Config) { c.EntryModel = crt.EntryMSSRetest }},
		{"mss, reclaim_retest, no envelope cap", func(c *crt.Config) { c.ExecutionStopMaxPips = 0 }},
		{"mss, mss_retest, no envelope cap", func(c *crt.Config) { c.EntryModel, c.ExecutionStopMaxPips = crt.EntryMSSRetest, 0 }},
		{"baseline sweep_reclaim, reclaim_retest, no cap", func(c *crt.Config) {
			c.ConfirmationMode, c.EntryModel, c.ExecutionStopMaxPips = crt.ConfirmationSweepReclaim, crt.EntryReclaimRetest, 0
		}},
		{"mss, reclaim_retest, sweep window 3 H1", func(c *crt.Config) { c.SweepWindowH1Periods = 3 }},
	}
	var out strings.Builder
	for _, c := range captures {
		data := loadCapture(t, doc, c.File)
		fmt.Fprintf(&out, "\n== %s\n", c.Label)
		for _, v := range variants {
			cfg := data.cfg
			v.adjust(&cfg)
			setups := map[[4]int64]bool{}
			rej := map[crt.Rejection]bool{}
			counts := map[string]int{}
			for i := firstEvaluable; i < len(data.m5); i++ {
				a := crt.DetectAsOf(cfg, crt.Input{H1: data.h1, M5: data.m5}, i)
				for _, s := range a.Setups {
					d := int64(1)
					if s.Direction == market.Sell {
						d = -1
					}
					setups[[4]int64{d, s.Anchor.OpenTime, s.Sweep.BarTime, s.ConfirmedAt}] = true
				}
				for _, r := range a.Rejections {
					if !rej[r] {
						rej[r] = true
						counts[r.Reason]++
					}
				}
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
			fmt.Fprintf(&out, "  %-48s setups=%-3d %s\n", v.name, len(setups), strings.Join(parts, " "))
		}
	}
	t.Log("\n" + out.String())
}
