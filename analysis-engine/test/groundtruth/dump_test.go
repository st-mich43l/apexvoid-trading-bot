// Package groundtruth_test dumps what the engine believes about structure and zones at checkpoints
// of a committed capture, for algo-bot/tools/ae_ground_truth.py to score against an independent,
// definition-based labelling of the same candles.
//
//	GT_DUMP_DIR=/tmp/gt go test ./test/groundtruth -run TestDumpState
//
// The dump is a deterministic function of the capture and the committed configuration. It records
// only engine output; the labels never see it and the engine never sees the labels.
package groundtruth_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

// Checkpoints are the bars (by index of evaluated M5 bars) at which the full state is recorded.
const (
	warmupBars     = 600
	checkpointStep = 144 // twelve hours of M5
)

var captures = []string{
	"replay-xau-production-capture-20261014.json",
	"replay-eurusd-production-capture-20261014.json",
	"replay-gbpusd-production-capture-20261014.json",
	"replay-gbpjpy-production-capture-20261014.json",
	"replay-usdjpy-production-capture-20261014.json",
}

type swingRow struct {
	Kind         string  `json:"kind"`
	Layer        string  `json:"layer"`
	Time         int64   `json:"time"`
	Price        float64 `json:"price"`
	ConfirmedAt  int64   `json:"confirmed_at"`
	ExcursionATR float64 `json:"excursion_atr"`
}

type breakRow struct {
	Time           int64   `json:"time"`
	Direction      string  `json:"direction"`
	Layer          string  `json:"layer"`
	Type           string  `json:"type"`
	Event          string  `json:"event"`
	BrokenSwingID  string  `json:"broken_swing_id"`
	PenetrationATR float64 `json:"penetration_atr"`
	CloseBeyond    bool    `json:"close_beyond"`
	ConfirmedAt    int64   `json:"confirmed_at"`
}

type zoneRow struct {
	Kind       string  `json:"kind"`
	Side       string  `json:"side"`
	Low        float64 `json:"low"`
	High       float64 `json:"high"`
	OriginTime int64   `json:"origin_time"`
	CreatedAt  int64   `json:"created_at"`
	State      string  `json:"state"`
	Relevance  string  `json:"relevance"`
	TouchCount int     `json:"touch_count"`
	Layer      string  `json:"layer"`
}

type frameRow struct {
	LastCandleTime int64      `json:"last_candle_time"`
	ATR            float64    `json:"atr"`
	Swings         []swingRow `json:"swings"`
	Breaks         []breakRow `json:"breaks"`
	Zones          []zoneRow  `json:"zones"`
}

type snapshot struct {
	Capture string              `json:"capture"`
	Symbol  string              `json:"symbol"`
	BarTime int64               `json:"bar_time"`
	Frames  map[string]frameRow `json:"frames"`
}

func TestDumpState(t *testing.T) {
	dir := os.Getenv("GT_DUMP_DIR")
	if dir == "" {
		t.Skip("GT_DUMP_DIR not set")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	for _, name := range captures {
		wg.Add(1)
		go func(name string) {
			defer wg.Done()
			capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", name))
			if err != nil {
				t.Error(err)
				return
			}
			var snapshots []snapshot
			seen := 0
			_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
				seen++
				if seen < warmupBars || (seen-warmupBars)%checkpointStep != 0 || e.Context == nil {
					return
				}
				snap := snapshot{Capture: name, Symbol: string(e.Symbol), BarTime: e.BarTime, Frames: map[string]frameRow{}}
				for tf, frame := range e.Context.Timeframes {
					if tf != market.M5 && tf != market.M15 && tf != market.H1 {
						continue
					}
					row := frameRow{ATR: frame.Zones.ATR}
					if n := len(frame.Candles); n > 0 {
						row.LastCandleTime = frame.Candles[n-1].Time
					}
					for _, s := range frame.Structure.Swings {
						row.Swings = append(row.Swings, swingRow{Kind: s.Kind.String(), Layer: s.Layer.String(), Time: s.Time,
							Price: float64(s.Price), ConfirmedAt: s.ConfirmedAt, ExcursionATR: s.ExcursionATR})
					}
					for _, b := range frame.Structure.Breaks {
						row.Breaks = append(row.Breaks, breakRow{Time: b.Time, Direction: string(b.Direction), Layer: b.Layer.String(),
							Type: b.Type.String(), Event: b.Event.String(), BrokenSwingID: b.BrokenSwingID,
							PenetrationATR: b.PenetrationATR, CloseBeyond: b.CloseBeyond, ConfirmedAt: b.ConfirmedAt})
					}
					for _, z := range frame.Zones.Zones {
						row.Zones = append(row.Zones, zoneRow{Kind: z.Kind.String(), Side: z.Side.String(), Low: float64(z.Low),
							High: float64(z.High), OriginTime: z.OriginTime, CreatedAt: z.CreatedAt, State: z.State.String(),
							Relevance: relevanceName(z), TouchCount: z.TouchCount, Layer: z.Layer.String()})
					}
					snap.Frames[string(tf)] = row
				}
				snapshots = append(snapshots, snap)
			}})
			if err != nil {
				t.Error(err)
				return
			}
			sort.SliceStable(snapshots, func(i, j int) bool { return snapshots[i].BarTime < snapshots[j].BarTime })
			out, err := os.Create(filepath.Join(dir, name+".jsonl"))
			if err != nil {
				t.Error(err)
				return
			}
			defer out.Close()
			enc := json.NewEncoder(out)
			for _, s := range snapshots {
				if err := enc.Encode(s); err != nil {
					t.Error(err)
					return
				}
			}
		}(name)
	}
	wg.Wait()
}

func relevanceName(z zone.Zone) string {
	switch z.Relevance {
	case zone.Immediate:
		return "immediate"
	case zone.Nearby:
		return "nearby"
	case zone.Remote:
		return "remote"
	case zone.Dormant:
		return "dormant"
	}
	return "unknown"
}
