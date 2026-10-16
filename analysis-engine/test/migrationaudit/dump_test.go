// Package migrationaudit_test dumps what the Go engine computes at hourly checkpoints of a committed capture,
// in the same schema as oracle_dump.py (the historical Python oracle), for oracle_compare.py.
//
//	AUDIT_DUMP_DIR=/tmp/audit go test ./test/migrationaudit -run TestDumpFrames
//
// The dump is a deterministic function of the capture and the committed configuration. It records only Go output; the
// oracle never sees it and Go never sees the oracle.
package migrationaudit_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	redistransport "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/redis"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
)

type m map[string]any

func tAt(bars []market.Candle, i int) any {
	if i >= 0 && i < len(bars) {
		return bars[i].Time
	}
	return nil
}

func zonesOut(zs []techniquezone.Zone, bars []market.Candle) []m {
	out := make([]m, 0, len(zs))
	for _, z := range zs {
		out = append(out, m{"bottom": z.Bottom, "top": z.Top, "side": z.Side, "origin_index": z.OriginIndex, "origin_index_t": tAt(bars, z.OriginIndex),
			"touches": z.Touches, "mitigated": z.Mitigated, "source": z.Source, "sources": z.Sources, "score": z.Score, "score_reasons": z.ScoreReasons,
			"break_kind": nilIfEmpty(z.BreakKind), "break_index": z.BreakIndex, "break_index_t": tAt(bars, z.BreakIndex)})
	}
	return out
}

func nilIfEmpty(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func frameOut(f *analysiscontext.LegacyFrame) m {
	bars := f.Bars
	out := m{"n": len(bars), "last_t": bars[len(bars)-1].Time, "structure": f.Structure, "detector_atr": f.DetectorATR}
	if n := len(f.ATR); n > 0 {
		out["atr_simple_last"] = f.ATR[n-1]
		tail := f.ATR
		if n > 5 {
			tail = f.ATR[n-5:]
		}
		out["atr_simple_tail"] = tail
	}
	out["atr_scalar_median"] = techniquezone.ATRScalar(f.ATR, 1)
	sw := []m{}
	for _, s := range f.Swings {
		sw = append(sw, m{"index": s.Index, "index_t": tAt(bars, s.Index), "kind": s.Kind, "price": s.Price, "label": s.Label, "confirmed_index": s.ConfirmedIndex, "confirmed_index_t": tAt(bars, s.ConfirmedIndex)})
	}
	out["swings"] = sw
	br := []m{}
	for _, b := range f.Breaks {
		br = append(br, m{"kind": b.Kind, "direction": b.Direction, "level": b.Level, "index": b.Index, "index_t": tAt(bars, b.Index)})
	}
	out["breaks"] = br
	lv := func(ls []techniquezone.Level) []m {
		o := []m{}
		for _, l := range ls {
			o = append(o, m{"price": l.Price, "kind": l.Kind, "touches": l.Touches, "band": l.Band, "strength": l.Strength})
		}
		return o
	}
	out["levels"] = lv(f.Levels)
	out["swing_levels"] = lv(f.SwingLevels)
	legs := []m{}
	for _, l := range f.Legs {
		legs = append(legs, m{"start": l.Start, "start_t": tAt(bars, l.Start), "end": l.End, "end_t": tAt(bars, l.End), "direction": l.Direction, "size": l.Size})
	}
	out["legs"] = legs
	out["zones"] = zonesOut(f.Zones, bars)
	out["contract_zones"] = zonesOut(f.ContractZones, bars)
	out["technique_zones"] = zonesOut(f.TechniqueZones, bars)
	out["ob"] = zonesOut(f.OrderBlocks, bars)
	pools := []m{}
	for _, p := range f.Pools {
		pools = append(pools, m{"side": p.Side, "level": p.Level, "band": p.Band, "touches": p.Touches})
	}
	out["pools"] = pools
	grabs := []m{}
	for _, g := range f.Grabs {
		grabs = append(grabs, m{"index": g.Index, "index_t": tAt(bars, g.Index), "direction": g.Direction, "grade": g.Grade, "displacement": g.ReactionDisplacement, "inducement": g.Inducement,
			"pool": m{"side": g.Pool.Side, "level": g.Pool.Level, "band": g.Pool.Band, "touches": g.Pool.Touches}})
	}
	out["grabs"] = grabs
	sess := []m{}
	for _, s := range f.Sessions {
		sess = append(sess, m{"name": s.Name, "price": s.Price, "swept": s.Swept})
	}
	out["sessions"] = sess
	if f.Range != nil {
		out["dealing_range"] = m{"high": float64(f.Range.High), "low": float64(f.Range.Low), "eq": float64(f.Range.Equilibrium), "position": f.Range.Position, "zone": f.Range.Zone, "fib_zone": f.Range.FibZone}
	}
	out["regime"] = m{"kind": f.Regime.Kind, "range_high": f.Regime.RangeHigh, "range_low": f.Regime.RangeLow, "height_atr": f.Regime.HeightATR, "reasons": f.Regime.Reasons,
		"coiling": f.Regime.Coiling, "legacy_kind": f.Regime.LegacyKind, "new_kind": f.Regime.NewKind}
	if bb := f.Regime.BoxBreak; bb != nil {
		out["box_break"] = m{"box_high": bb.BoxHigh, "box_low": bb.BoxLow, "direction": bb.Direction, "accept_index": bb.AcceptBar, "accept_index_t": tAt(bars, bb.AcceptBar), "coiling": bb.Coiling, "acceptance": bb.Acceptance}
	}
	out["momentum"] = string(f.Momentum)
	out["momentum_velocity"] = f.MomentumVelocity
	out["momentum_acceleration"] = f.MomentumAcceleration
	barriers := []m{}
	for _, b := range f.Barriers {
		barriers = append(barriers, m{"side": b.Side, "level": b.Level, "low": b.Low, "high": b.High, "touches": b.Touches, "wick_rejections": b.WickRejections, "accepted_closes": b.AcceptedCloses,
			"last_touch_index": b.LastTouchIndex, "last_touch_index_t": tAt(bars, b.LastTouchIndex), "first_touch_index": b.FirstTouchIndex, "score": b.Score, "grade": b.Grade, "tags": b.Tags, "sources": b.Sources})
	}
	out["scalp_barriers"] = barriers
	if r := f.ScalpRange; r != nil {
		out["scalp_range"] = m{"eq": r.Equilibrium, "width_atr": r.WidthATR, "quality": r.Quality, "state": r.State, "lower_level": r.Lower.Level, "upper_level": r.Upper.Level, "inside_closes": r.InsideCloses, "one_sided": r.OneSided, "post_impulse": r.PostImpulse}
	}
	if f.Techniques != nil {
		inst := []m{}
		for _, in := range f.Techniques() {
			inst = append(inst, m{"technique": in.Technique, "side": in.Side, "low": in.Low, "high": in.High, "sources": in.Sources, "origin_index": in.OriginIndex, "origin_index_t": tAt(bars, in.OriginIndex),
				"touches": in.Touches, "mitigated": in.Mitigated, "entry_clipped": in.EntryClipped, "structural_low": in.StructuralLow, "structural_high": in.StructuralHigh,
				"body_frac": in.BodyFrac, "has_bos": in.HasBOS, "score": in.Score, "zone_score": in.ZoneScore, "h1_time": in.H1Time, "timeframe": in.Timeframe})
		}
		out["technique_instances"] = inst
	}
	return out
}

func contextZones(zs zone.ZoneState) []m {
	out := []m{}
	for _, z := range zs.Zones {
		out = append(out, m{"id": z.ID, "kind": z.Kind.String(), "side": z.Side.String(), "low": float64(z.Low), "high": float64(z.High), "origin_time": z.OriginTime, "created_at": z.CreatedAt,
			"touch_count": z.TouchCount, "state": z.State.String(), "relevance": int(z.Relevance), "strength": z.Strength, "legacy_score": z.LegacyScore, "layer": z.Layer.String(), "timeframe": string(z.Timeframe),
			"break_index": z.BreakIndex, "structure_ref": z.StructureRef, "displacement_ref": z.DisplacementRef, "last_touched_at": z.LastTouchedAt})
	}
	return out
}

func TestDumpFrames(t *testing.T) {
	dir := os.Getenv("AUDIT_DUMP_DIR")
	if dir == "" {
		t.Skip("AUDIT_DUMP_DIR not set")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	doc, err := config.ResolveDocument(filepath.Join("..", "..", "..", "config", "apexvoid.yml"))
	if err != nil {
		t.Fatal(err)
	}
	captures := strings.Split(os.Getenv("AUDIT_CAPTURES"), ",")
	every := int64(3600)
	var wg sync.WaitGroup
	for _, name := range captures {
		if name == "" {
			continue
		}
		wg.Add(1)
		go func(name string) {
			defer wg.Done()
			capture, err := replaycapture.Load(filepath.Join("..", "..", "testdata", name))
			if err != nil {
				t.Error(err)
				return
			}
			settings, err := engine.LoadSettings(doc, market.M5, false)
			if err != nil {
				t.Error(err)
				return
			}
			if err := engine.ApplyInstrument(&settings, doc, string(capture.Symbol)); err != nil {
				t.Error(err)
				return
			}
			var records []m
			_, err = replaycapture.Replay(doc, capture, market.M5, replaycapture.Options{OnEvaluation: func(e engine.Evaluation) {
				if e.Timeframe != market.M5 || e.BarTime%every != 0 || e.Context == nil {
					return
				}
				m5 := e.Context.Timeframes[market.M5]
				if m5 == nil || m5.Legacy == nil || len(m5.Legacy.Bars) < 150 {
					return
				}
				rec := m{"symbol": string(e.Symbol), "bar": e.BarTime, "frames": m{}, "production_zones": m{}, "htf_bias": e.Context.Legacy.HTFBias}
				// What the production adapter reads as bias: Context.Bias and the closed higher-timeframe biases
				// (the legacy HTFBias above is what the detector parity tests compare).
				hts := []m{}
				for _, h := range engine.ClosedHigherTimeframeBiases(e.Context, market.M5, e.BarTime, settings.Momentum) {
					hts = append(hts, m{"timeframe": string(h.Timeframe), "direction": string(h.Direction), "layer": h.Layer})
				}
				rec["production_htf"] = hts
				rec["production_bias"] = m{"direction": string(e.Context.Bias.Direction), "layer": e.Context.Bias.Layer.String()}
				frames := rec["frames"].(m)
				for tf, ctx := range e.Context.Timeframes {
					if ctx == nil || ctx.Legacy == nil || len(ctx.Legacy.Bars) == 0 {
						continue
					}
					frames[string(tf)] = frameOut(ctx.Legacy)
					rec["production_zones"].(m)[string(tf)] = contextZones(ctx.Zones)
				}
				// The V2 zone book of the primary timeframe, before the technique population replaces its supply,
				// demand, order block and FVG kinds (what zone.Update produced from the full retained history).
				candles := m5.Candles
				if atr, err := indicator.CanonicalATR(candles, settings.ATR.Length, settings.ATR.Algorithm); err == nil {
					ss := structure.Update(candles, atr, market.M5, settings.Structure)
					v2m5 := zone.Update(candles, atr, ss.Swings, ss.Breaks, market.M5, settings.Zone)
					rec["v2_zones_m5"] = contextZones(v2m5)
					// What the execution-time target-room recheck reads: the zone book Go publishes, built from the V2
					// zone state of every timeframe (the snapshot is not the technique-overridden Context population).
					books := map[market.Timeframe]zone.ZoneState{market.M5: v2m5}
					for _, tf := range []market.Timeframe{market.M15, market.H1} {
						if ctx := e.Context.Timeframes[tf]; ctx != nil {
							books[tf] = ctx.Zones
						}
					}
					barrierCfg, bErr := engine.BarrierConfigFromConfig(doc, m5.Geometry)
					if bErr != nil {
						t.Error(bErr)
						return
					}
					bookDTO := redistransport.BuildZoneBook(e.Symbol, books, e.BarTime, &barrierCfg)
					rec["barriers"] = bookDTO.Barriers
					rec["price"] = candles[len(candles)-1].Close
					rec["canonical_atr_last_m5"] = atr[len(atr)-1]
					rec["canonical_atr_algorithm"] = string(settings.ATR.Algorithm)
					rec["canonical_history_bars_m5"] = len(candles)
				}
				records = append(records, rec)
			}})
			if err != nil {
				t.Error(err)
				return
			}
			sort.SliceStable(records, func(i, j int) bool { return records[i]["bar"].(int64) < records[j]["bar"].(int64) })
			out, err := os.Create(filepath.Join(dir, name+".go.jsonl"))
			if err != nil {
				t.Error(err)
				return
			}
			defer out.Close()
			enc := json.NewEncoder(out)
			for _, r := range records {
				if err := enc.Encode(r); err != nil {
					t.Error(err)
					return
				}
			}
		}(name)
	}
	wg.Wait()
}
