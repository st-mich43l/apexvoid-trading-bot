package mad

import (
	"math"
	"strconv"
	"testing"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// The fixtures use the fixed-hour fallback window (Asia 22:00 -> London 07:00
// UTC) unless a test supplies a calendar. Prices are XAU-like (pip 0.1) or
// FX-like (pip 0.0001) through the same ATR-normalised measures.

const (
	day0   = int64(1_700_000_000) // arbitrary; windows below are derived from it
	minute = int64(60)
)

func testConfig() Config {
	return Config{
		AsiaStartHour: 22, LondonStartHour: 7,
		AccumMinimumRQ: 0.8, AccumMaximumRQ: 6, ExpandBreakATR: 0.35, ExpandDisplacementATR: 1.25, ExpandAcceptCloses: 2,
		ManipMinimumPenetrationATR: 0.05, ManipMinimumReclaimATR: 0.05, PipSize: 0.1, SequenceValidityHours: 8,
	}
}

// at returns the Unix time of hh:mm UTC on the given day offset from a fixed Monday.
func at(dayOffset, hour, min int) int64 {
	return time.Date(2026, time.November, 9+dayOffset, hour, min, 0, 0, time.UTC).Unix()
}

// bar is one closed M5 bar.
type bar struct {
	t               int64
	open, hi, lo, c float64
}

func candles(bars []bar) []market.Candle {
	out := make([]market.Candle, len(bars))
	for i, b := range bars {
		out[i] = market.Candle{Time: b.t, Open: b.open, High: b.hi, Low: b.lo, Close: b.c}
	}
	return out
}

// flatBars lists M5 bars from start (inclusive) to end (exclusive) oscillating inside [low, high].
func flatBars(start, end int64, low, high float64) []bar {
	var out []bar
	mid := (low + high) / 2
	for t, i := start, 0; t < end; t, i = t+5*minute, i+1 {
		if i%2 == 0 {
			out = append(out, bar{t, mid, high, mid - 0.2, mid + 0.2})
		} else {
			out = append(out, bar{t, mid, mid + 0.2, low, mid - 0.2})
		}
	}
	return out
}

// asiaDay builds the history up to the Asia window's close: lead-in bars before
// it, then the Asia range [4300, 4310].
func asiaDay() []bar {
	bars := flatBars(at(0, 20, 0), at(0, 22, 0), 4301, 4309) // before the window opens (history reaches it)
	bars = append(bars, flatBars(at(0, 22, 0), at(1, 7, 0), 4300, 4310)...)
	return bars
}

func lastTime(b []bar) int64 { return b[len(b)-1].t }

func classify(t *testing.T, bars []bar, cfg Config) Snapshot {
	t.Helper()
	return Classify(Input{Candles: candles(bars), Timeframe: market.M5, ATR: 2, Structure: "range", Config: cfg})
}

// bullishSequence is accumulation -> sell-side sweep -> reclaim -> expansion up.
func bullishSequence() []bar {
	bars := asiaDay()
	t := at(1, 7, 0)
	bars = append(bars,
		bar{t, 4302, 4303, 4297, 4302},              // sweeps the low (4300) by 3.0 = 1.5 ATR and closes back inside: reclaim in the same bar
		bar{t + 5*minute, 4302, 4304, 4301, 4303.5}, // follows through, still under the displacement threshold (0.75 ATR)
		bar{t + 10*minute, 4304, 4309, 4303, 4308},  // displacement from the reclaim close: 6/2 = 3 ATR >= 1.25
	)
	return bars
}

func TestBullishSequenceLinksSweepReclaimExpansion(t *testing.T) {
	bars := bullishSequence()
	s := classify(t, bars, testConfig())
	if s.Sequence == nil || s.Sequence.Status != SequenceComplete {
		t.Fatalf("want a complete sequence, got %+v (reason %s)", s.Sequence, s.ReasonCode)
	}
	q := s.Sequence
	if q.Sweep.Side != "low" || q.Sweep.Direction != "BUY" || q.Expansion.Direction != "BUY" {
		t.Fatalf("direction/side wrong: %+v %+v", q.Sweep, q.Expansion)
	}
	if got := q.Stages; len(got) != 4 || got[0] != StageRange || got[1] != StageSweep || got[2] != StageReclaim || got[3] != StageExpansion {
		t.Fatalf("stages = %v", got)
	}
	if q.Sweep.At != at(1, 7, 0) || q.Sweep.ReclaimAt != at(1, 7, 0) || q.Expansion.StartedAt != at(1, 7, 5) || q.Expansion.ConfirmedAt != at(1, 7, 10) {
		t.Fatalf("event times wrong: %+v %+v", q.Sweep, q.Expansion)
	}
	if err := q.OrderingError(s.Asia.End); err != nil {
		t.Fatal(err)
	}
	if s.Phase != PhaseExpand || !s.Active || s.ExpansionDirection != "BUY" || s.ManipulationDirection != "BUY" || !s.Reclaim || s.SweepSide != "low" {
		t.Fatalf("summary wrong: %+v", s)
	}
	if math.Abs(q.Sweep.PenetrationATR-1.5) > 1e-9 {
		t.Fatalf("penetration normalised by ATR: %v", q.Sweep.PenetrationATR)
	}
	if want := "asia:" + itoa(at(0, 22, 0)) + ":low:" + itoa(at(1, 7, 0)); q.ID != want {
		t.Fatalf("sequence id = %s, want %s", q.ID, want)
	}
	if s.Asia.ID != "asia:"+itoa(at(0, 22, 0)) || !s.Asia.Sealed || s.Asia.High != 4310 || s.Asia.Low != 4300 {
		t.Fatalf("range wrong: %+v", s.Asia)
	}
}

func itoa(v int64) string { return strconv.FormatInt(v, 10) }

func TestBearishSequenceIsTheMirror(t *testing.T) {
	bars := asiaDay()
	t0 := at(1, 7, 0)
	bars = append(bars,
		bar{t0, 4308, 4313, 4307, 4308},
		bar{t0 + 5*minute, 4308, 4309, 4305, 4305},
		bar{t0 + 10*minute, 4305, 4306, 4301, 4302},
	)
	s := classify(t, bars, testConfig())
	q := s.Sequence
	if q == nil || q.Status != SequenceComplete || q.Sweep.Side != "high" || q.Sweep.Direction != "SELL" || q.Expansion.Direction != "SELL" {
		t.Fatalf("bearish sequence wrong: %+v", q)
	}
	if err := q.OrderingError(s.Asia.End); err != nil {
		t.Fatal(err)
	}
}

func TestPartialSequencesAreNeverCompleted(t *testing.T) {
	cfg := testConfig()
	t0 := at(1, 7, 0)

	// A reclaimed sweep with no expansion yet is manipulation, partial.
	sweepOnly := append(asiaDay(), bar{t0, 4302, 4303, 4297, 4302})
	s := classify(t, sweepOnly, cfg)
	if s.Phase != PhaseManip || s.Sequence.Status != SequencePartial || s.Sequence.Expansion != nil || len(s.Sequence.Stages) != 3 {
		t.Fatalf("lone sweep must stay partial: %+v / %+v", s, s.Sequence)
	}

	// A sweep whose reclaim has not happened: a dated sweep, no reclaim, no phase claimed.
	pending := append(asiaDay(), bar{t0, 4300, 4300, 4297, 4298})
	s = classify(t, pending, cfg)
	if s.Sequence == nil || s.Sequence.Sweep == nil || s.Sequence.Sweep.Reclaimed() || s.Phase != PhaseUnclear || s.ReasonCode != "asia_sweep_pending_reclaim" || s.Sequence.Direction != "" {
		t.Fatalf("an unreclaimed sweep must not be manipulation: %+v / %+v", s, s.Sequence)
	}

	// Expansion with no manipulation: partial, no sweep stage invented.
	exp := asiaDay()
	exp = append(exp, bar{t0, 4310, 4312.5, 4310, 4312}, bar{t0 + 5*minute, 4312, 4314, 4311.5, 4313.5})
	s = classify(t, exp, cfg)
	if s.Phase != PhaseExpand || s.Sequence.Sweep != nil || s.Sequence.Status != SequencePartial || s.ManipulationDirection != "" || s.Reclaim {
		t.Fatalf("expansion without manipulation: %+v / %+v", s, s.Sequence)
	}
	for _, stage := range s.Sequence.Stages {
		if stage == StageSweep || stage == StageReclaim {
			t.Fatalf("a missing phase was manufactured: %v", s.Sequence.Stages)
		}
	}
}

func TestNoManipulationNoFalseBreakout(t *testing.T) {
	bars := append(asiaDay(), flatBars(at(1, 7, 0), at(1, 8, 0), 4301, 4309)...)
	s := classify(t, bars, testConfig())
	if s.Sequence != nil {
		t.Fatalf("no event happened, so no sequence: %+v", s.Sequence)
	}
	if s.Phase != PhaseAccum || !s.Active {
		t.Fatalf("a sealed box still being respected is accumulation: %+v", s)
	}
}

func TestTwoWayAndContradictorySweepsInvalidate(t *testing.T) {
	cfg := testConfig()
	t0 := at(1, 7, 0)
	// Both sides in one bar.
	both := append(asiaDay(), bar{t0, 4305, 4313, 4297, 4305})
	s := classify(t, both, cfg)
	if s.Sequence == nil || s.Sequence.Status != SequenceInvalidated || s.Sequence.InvalidationReason != InvalidTwoSidedSweep || s.Active || s.Phase != PhaseUnclear {
		t.Fatalf("double sweep: %+v / %+v", s, s.Sequence)
	}
	// Low swept and reclaimed, then the high swept: the first sweep's thesis is contradicted.
	bars := append(asiaDay(), bar{t0, 4302, 4303, 4297, 4302}, bar{t0 + 5*minute, 4305, 4313, 4304, 4306})
	s = classify(t, bars, cfg)
	if s.Sequence.Status != SequenceInvalidated || s.Sequence.InvalidationReason != InvalidConflictingSweep || s.Active {
		t.Fatalf("contradictory sweeps: %+v", s.Sequence)
	}
	// The invalidated facts are kept for audit.
	if s.Sequence.Sweep == nil || s.Sequence.Sweep.Side != "low" {
		t.Fatalf("history dropped: %+v", s.Sequence)
	}
}

func TestSweepExtremeLostInvalidates(t *testing.T) {
	t0 := at(1, 7, 0)
	bars := append(asiaDay(), bar{t0, 4302, 4303, 4297, 4302}, bar{t0 + 5*minute, 4301, 4301, 4294, 4295})
	s := classify(t, bars, testConfig())
	if s.Sequence.Status != SequenceInvalidated || s.Sequence.InvalidationReason != InvalidSweepExtremeLost || s.Active {
		t.Fatalf("closing beyond the sweep extreme kills the thesis: %+v", s.Sequence)
	}
}

func TestAnOldInvalidationAgesOutAndIsDirectionAware(t *testing.T) {
	cfg := testConfig()
	t0 := at(1, 7, 0)
	lost := append(asiaDay(), bar{t0, 4302, 4303, 4297, 4302}, bar{t0 + 5*minute, 4301, 4301, 4294, 4295})
	s := classify(t, lost, cfg)
	setup := func(dir string) Setup {
		return Setup{Strategy: "key_level", Direction: dir, ObservedAt: lastTime(lost), ObservedTimeframe: market.M5}
	}
	if r := Relate(s, setup("BUY"), cfg); r.Status != RelationConflicting {
		t.Fatalf("the failed bullish thesis opposes a BUY: %+v", r)
	}
	if r := Relate(s, setup("SELL"), cfg); r.Status != RelationPartial || r.Reasons[0] != "opposing_sequence_invalidated" {
		t.Fatalf("a failed bullish thesis neither supports nor contradicts a SELL: %+v", r)
	}
	// Hours later the same invalidation is old context, not a live contradiction.
	later := append(append([]bar(nil), lost...), bar{at(1, 17, 0), 4295, 4296, 4294, 4295})
	ls := classify(t, later, cfg)
	if ls.Sequence.Status != SequenceStale || ls.Sequence.InvalidationReason != InvalidSweepExtremeLost {
		t.Fatalf("aged invalidation: %+v", ls.Sequence)
	}
	if r := Relate(ls, Setup{Strategy: "key_level", Direction: "BUY", ObservedAt: at(1, 17, 0), ObservedTimeframe: market.M5}, cfg); r.Status != RelationUnavailable {
		t.Fatalf("stale evidence is unavailable: %+v", r)
	}
}

func TestSweepAcceptedBeyondTheLevelIsABreakoutNotASweep(t *testing.T) {
	t0 := at(1, 7, 0)
	// Two consecutive closes above the range high: accepted, so not a stop run.
	bars := append(asiaDay(), bar{t0, 4309, 4313, 4309, 4312}, bar{t0 + 5*minute, 4312, 4316, 4311, 4315})
	s := classify(t, bars, testConfig())
	if s.Sequence == nil || s.Sequence.Sweep != nil || s.Sequence.Expansion == nil || s.Sequence.Expansion.Direction != "BUY" {
		t.Fatalf("an accepted break is expansion without manipulation: %+v", s.Sequence)
	}
	if s.Sequence.Note != "sweep_accepted_beyond_level" {
		t.Fatalf("the reclassification is recorded: %q", s.Sequence.Note)
	}
}

func TestStaleEvidenceIsNotCurrent(t *testing.T) {
	cfg := testConfig()
	bars := bullishSequence()
	// Quiet bars far later: the same sequence is past its validity.
	late := append([]bar(nil), bars...)
	late = append(late, bar{at(1, 17, 0), 4308, 4309, 4307, 4308})
	s := classify(t, late, cfg)
	if s.Active || s.Sequence == nil || s.Sequence.Status != SequenceStale || s.Phase != PhaseUnclear || s.ReasonCode != "mad_evidence_stale" {
		t.Fatalf("stale: %+v / %+v", s, s.Sequence)
	}
	if s.Sequence.Sweep == nil || s.Sequence.Expansion == nil {
		t.Fatal("stale facts must be preserved for audit")
	}
	if a := Affinity(s, "BUY", "crt"); a.Final != 0 {
		t.Fatalf("stale evidence must not lift a setup: %+v", a)
	}
}

func TestNewRangeReplacesTheOldEvidence(t *testing.T) {
	cfg := testConfig()
	// The next Asia session opens: yesterday's sequence is not reported, the box is forming.
	bars := bullishSequence()
	bars = append(bars, flatBars(at(1, 8, 0), at(1, 22, 0), 4305, 4312)...)
	bars = append(bars, flatBars(at(1, 22, 0), at(1, 23, 0), 4306, 4309)...)
	s := classify(t, bars, cfg)
	if s.Sequence != nil {
		t.Fatalf("a new range must not inherit the old sequence: %+v", s.Sequence)
	}
	if s.Asia == nil || s.Asia.ID != "asia:"+itoa(at(1, 22, 0)) || s.Asia.Sealed {
		t.Fatalf("the forming box is the new range: %+v", s.Asia)
	}
}

func TestUnsealedRangeCannotBeSwept(t *testing.T) {
	// While Asia is still forming a bar beyond the developing box extends it; it is not a sweep.
	bars := flatBars(at(0, 20, 0), at(0, 22, 0), 4301, 4309)
	bars = append(bars, flatBars(at(0, 22, 0), at(0, 23, 0), 4300, 4310)...)
	bars = append(bars, bar{at(0, 23, 0), 4305, 4318, 4304, 4305})
	s := classify(t, bars, testConfig())
	if s.Sequence != nil || s.SweepSide != "" || s.Phase == PhaseManip {
		t.Fatalf("an unsealed box has no edge to sweep: %+v / %+v", s, s.Sequence)
	}
	if s.Asia.Sealed || s.Asia.High != 4318 {
		t.Fatalf("the developing range includes the bar: %+v", s.Asia)
	}
}

func TestIncompleteHistoryAndCoarseTimeframeGiveNoRange(t *testing.T) {
	cfg := testConfig()
	// History that starts inside the window cannot prove the box start.
	late := flatBars(at(0, 23, 0), at(1, 7, 0), 4300, 4310)
	late = append(late, bar{at(1, 7, 0), 4305, 4306, 4304, 4305})
	s := classify(t, late, cfg)
	if s.Asia != nil || s.ReasonCode != "asia_range_incomplete_history" || s.Active {
		t.Fatalf("missing start of window: %+v", s)
	}
	// An H4 bar straddles the session open.
	h4 := Classify(Input{Candles: candles(asiaDay()), Timeframe: market.H4, ATR: 2, Config: cfg})
	if h4.Asia != nil || h4.ReasonCode != "asia_timeframe_too_coarse" {
		t.Fatalf("H4 must not read an Asia range: %+v", h4)
	}
	// A feed gap that leaves under half the window.
	gap := flatBars(at(0, 20, 0), at(0, 22, 0), 4301, 4309)
	gap = append(gap, flatBars(at(1, 3, 0), at(1, 7, 0), 4300, 4310)...)
	gap = append(gap, bar{at(1, 7, 0), 4305, 4306, 4304, 4305})
	if s := classify(t, gap, cfg); s.Asia != nil || s.ReasonCode != "asia_range_incomplete_history" {
		t.Fatalf("gap: %+v", s)
	}
}

func TestCalendarWindowFollowsDaylightSaving(t *testing.T) {
	// In winter London opens 08:00 UTC. The fixed hours would end the box an hour early,
	// and the session label (still Asia) used to leave it unsealed for that hour.
	cfg := testConfig()
	start, end := at(0, 22, 0), at(1, 8, 0)
	cfg.AsiaWindow = func(int64) (int64, int64, bool) { return start, end, true }
	bars := flatBars(at(0, 20, 0), at(0, 22, 0), 4301, 4309)
	bars = append(bars, flatBars(start, end, 4300, 4310)...)
	for i := range bars {
		if bars[i].t == at(1, 7, 30) { // inside the winter Asia window, outside the fixed-hour one
			bars[i] = bar{at(1, 7, 30), 4305, 4325, 4300, 4305}
		}
	}
	forming := Classify(Input{Candles: candles(bars), Timeframe: market.M5, ATR: 2, Structure: "range", Config: cfg})
	if forming.Asia == nil || forming.Asia.Sealed || forming.Asia.High != 4325 {
		t.Fatalf("before 08:00 the winter range is forming and includes the 07:30 bar: %+v", forming.Asia)
	}
	sealed := Classify(Input{Candles: candles(append(bars, bar{at(1, 8, 0), 4305, 4306, 4304, 4305})), Timeframe: market.M5, ATR: 2, Structure: "range", Config: cfg})
	if sealed.Asia == nil || sealed.Asia.End != end || !sealed.Asia.Sealed || sealed.Asia.SealedAt != end {
		t.Fatalf("the first bar at 08:00 seals the range, by time and not by label: %+v", sealed.Asia)
	}
	// The same bars on the fixed-hour fallback seal an hour early and exclude the 07:30 bar.
	fixed := testConfig()
	early := Classify(Input{Candles: candles(append(bars, bar{at(1, 8, 0), 4305, 4306, 4304, 4305})), Timeframe: market.M5, ATR: 2, Structure: "range", Config: fixed})
	if early.Asia == nil || early.Asia.High == 4325 {
		t.Fatalf("control: the fixed-hour window is the one that would be wrong: %+v", early.Asia)
	}
}

func TestTimeframesAgreeAndDoNotLeakTheFuture(t *testing.T) {
	cfg := testConfig()
	m5 := bullishSequence()
	// The same market as M15 bars: aggregate the M5 bars.
	var m15 []bar
	for i := 0; i < len(m5); {
		start := m5[i].t - m5[i].t%(15*minute)
		agg := bar{t: start, open: m5[i].open, hi: m5[i].hi, lo: m5[i].lo, c: m5[i].c}
		j := i + 1
		for j < len(m5) && m5[j].t-m5[j].t%(15*minute) == start {
			agg.hi, agg.lo, agg.c = math.Max(agg.hi, m5[j].hi), math.Min(agg.lo, m5[j].lo), m5[j].c
			j++
		}
		m15 = append(m15, agg)
		i = j
	}
	a := Classify(Input{Candles: candles(m5), Timeframe: market.M5, ATR: 2, Structure: "range", Config: cfg})
	b := Classify(Input{Candles: candles(m15), Timeframe: market.M15, ATR: 2, Structure: "range", Config: cfg})
	if a.Asia.ID != b.Asia.ID || a.Asia.High != b.Asia.High || a.Asia.Low != b.Asia.Low {
		t.Fatalf("the range is the same session range on every timeframe: %+v vs %+v", a.Asia, b.Asia)
	}
	if b.Sequence == nil || b.Sequence.Sweep == nil || b.Sequence.Sweep.Side != a.Sequence.Sweep.Side {
		t.Fatalf("M15 must see the same sweep: %+v", b.Sequence)
	}

	// No future leakage: the read at every prefix equals the read from only that prefix, and
	// appending later bars never changes an event already dated.
	var prevSweepAt int64
	for n := len(asiaDay()) + 1; n <= len(m5); n++ {
		s := Classify(Input{Candles: candles(m5[:n]), Timeframe: market.M5, ATR: 2, Structure: "range", Config: cfg})
		if s.Sequence == nil || s.Sequence.Sweep == nil {
			continue
		}
		if prevSweepAt != 0 && s.Sequence.Sweep.At != prevSweepAt {
			t.Fatalf("a dated event moved as bars were appended: %d -> %d", prevSweepAt, s.Sequence.Sweep.At)
		}
		prevSweepAt = s.Sequence.Sweep.At
		if s.Sequence.LastEventAt > m5[n-1].t {
			t.Fatalf("an event is dated after the last closed bar: %d > %d", s.Sequence.LastEventAt, m5[n-1].t)
		}
	}
}

func TestNormalisationIsSharedByGoldAndFX(t *testing.T) {
	cfg := testConfig()
	// The same shape scaled to FX prices: ATR-normalised measures must be identical.
	gold := classify(t, bullishSequence(), cfg)
	scale := 0.0001 / 0.1
	var fx []bar
	for _, b := range bullishSequence() {
		fx = append(fx, bar{b.t, 1.0 + (b.open-4300)*scale, 1.0 + (b.hi-4300)*scale, 1.0 + (b.lo-4300)*scale, 1.0 + (b.c-4300)*scale})
	}
	fxCfg := cfg
	fxCfg.PipSize = 0.0001
	s := Classify(Input{Candles: candles(fx), Timeframe: market.M5, ATR: 2 * scale, Structure: "range", Config: fxCfg})
	if s.Sequence == nil || s.Sequence.Status != SequenceComplete {
		t.Fatalf("FX: %+v", s.Sequence)
	}
	if math.Abs(s.Sequence.Sweep.PenetrationATR-gold.Sequence.Sweep.PenetrationATR) > 1e-6 ||
		math.Abs(s.Sequence.Expansion.DisplacementATR-gold.Sequence.Expansion.DisplacementATR) > 1e-6 {
		t.Fatalf("measures must be volatility-normalised: gold %+v, fx %+v", gold.Sequence.Sweep, s.Sequence.Sweep)
	}
}

func TestAccumulationAffinityIsNeutralNotAnAccident(t *testing.T) {
	bars := append(asiaDay(), flatBars(at(1, 7, 0), at(1, 7, 30), 4301, 4309)...)
	s := classify(t, bars, testConfig())
	if s.Phase != PhaseAccum || !s.Active {
		t.Fatalf("setup: %+v", s)
	}
	for _, direction := range []string{"BUY", "SELL"} {
		for _, strategy := range []string{"key_level", "impulse_pullback", "trendline"} {
			a := Affinity(s, direction, strategy)
			if a.Final != 0 || !a.Neutral || a.DirectionScore != 0 {
				t.Fatalf("accumulation is neutral for %s/%s: %+v", direction, strategy, a)
			}
		}
	}
}

func TestAffinityRewardsOnlyDirectionalFamilyAgreement(t *testing.T) {
	s := classify(t, bullishSequence()[:len(asiaDay())+1], testConfig()) // manip phase, BUY
	if s.Phase != PhaseManip {
		t.Fatalf("setup: %+v", s)
	}
	if a := Affinity(s, "BUY", "key_level"); a.Final <= 0 || a.Neutral {
		t.Fatalf("aligned structural reaction: %+v", a)
	}
	if a := Affinity(s, "SELL", "key_level"); a.Final != 0 {
		t.Fatalf("opposite direction earns nothing: %+v", a)
	}
	if a := Affinity(s, "BUY", "impulse_pullback"); a.Final != 0 {
		t.Fatalf("a continuation scalp is not a structural reaction: %+v", a)
	}
}

func TestRelationPerStrategy(t *testing.T) {
	cfg := testConfig()
	full := classify(t, bullishSequence(), cfg)
	setup := func(strategy, direction string, formed int64) Setup {
		return Setup{Strategy: strategy, Direction: direction, FormedAt: formed, ObservedAt: lastTime(bullishSequence()), ObservedTimeframe: market.M5}
	}
	check := func(name string, got Relation, want string, reason string) {
		t.Helper()
		if got.Status != want {
			t.Fatalf("%s: status %s, want %s (%v)", name, got.Status, want, got.Reasons)
		}
		if reason != "" {
			found := false
			for _, r := range got.Reasons {
				found = found || r == reason
			}
			if !found {
				t.Fatalf("%s: reasons %v lack %s", name, got.Reasons, reason)
			}
		}
	}
	check("crt buy", Relate(full, setup("crt", "BUY", 0), cfg), RelationAligned, "sweep_reclaim_in_setup_direction")
	check("crt sell", Relate(full, setup("crt", "SELL", 0), cfg), RelationConflicting, "sweep_direction_opposes_setup")
	check("key level buy", Relate(full, setup("key_level", "BUY", 0), cfg), RelationAligned, "expansion_confirmed_in_setup_direction")
	check("trendline buy", Relate(full, setup("trendline", "BUY", 0), cfg), RelationAligned, "expansion_continuation")
	check("trendline sell", Relate(full, setup("trendline", "SELL", 0), cfg), RelationConflicting, "expansion_opposes_setup")
	check("fade against expansion", Relate(full, setup("range_scalp", "SELL", 0), cfg), RelationConflicting, "")
	// iFVG: the sweep must precede the setup's own formation.
	check("ifvg formed after sweep", Relate(full, setup("ifvg", "BUY", at(1, 7, 5)), cfg), RelationAligned, "")
	check("ifvg formed before sweep", Relate(full, setup("ifvg", "BUY", at(1, 6, 0)), cfg), RelationPartial, "sweep_after_setup_formation")
	check("ifvg unknown formation", Relate(full, setup("ifvg", "BUY", 0), cfg), RelationPartial, "setup_formation_time_unavailable")
	// Irrelevant strategies get no AMD read at all.
	for _, strategy := range []string{"impulse_pullback", "impulse_retrace", "asia_range", "supply", "shock_retest"} {
		r := Relate(full, setup(strategy, "BUY", 0), cfg)
		if r.Status != RelationIrrelevant || len(r.EvidenceIDs) != 0 {
			t.Fatalf("%s must be irrelevant with no evidence: %+v", strategy, r)
		}
	}
	// Missing evidence is unavailable, never a positive.
	none := classify(t, flatBars(at(0, 23, 0), at(1, 7, 0), 4300, 4310), cfg)
	if r := Relate(none, setup("crt", "BUY", 0), cfg); r.Status != RelationUnavailable {
		t.Fatalf("no range: %+v", r)
	}
	// An invalidated sequence is not support.
	killed := classify(t, append(asiaDay(), bar{at(1, 7, 0), 4305, 4313, 4297, 4305}), cfg)
	if r := Relate(killed, setup("crt", "BUY", 0), cfg); r.Status != RelationConflicting {
		t.Fatalf("invalidated: %+v", r)
	}
	// A weak manipulation (below the configured minimum measures) is partial, not aligned.
	weak := append(asiaDay(), bar{at(1, 7, 0), 4301, 4300.3, 4299.7, 4300.1})
	strict := cfg
	strict.ManipMinimumPenetrationATR = 0.5
	ws := classify(t, weak, strict)
	if ws.Sequence == nil || ws.Sequence.Sweep == nil {
		t.Fatalf("weak sweep not seen: %+v", ws)
	}
	if r := Relate(ws, Setup{Strategy: "crt", Direction: "BUY", ObservedAt: at(1, 7, 0), ObservedTimeframe: market.M5}, strict); r.Status == RelationAligned {
		t.Fatalf("a sweep under the minimum measures must not be aligned: %+v", r)
	}
}

func TestRelationRefusesEvidenceAfterObservation(t *testing.T) {
	cfg := testConfig()
	full := classify(t, bullishSequence(), cfg)
	early := Setup{Strategy: "crt", Direction: "BUY", ObservedAt: at(1, 7, 0), ObservedTimeframe: market.M5}
	if r := Relate(full, early, cfg); r.Status != RelationUnavailable || r.Reasons[0] != "evidence_after_observation" {
		t.Fatalf("evidence that closes after the observation bar is hindsight: %+v", r)
	}
}

func TestOrderingErrorDetectsOutOfOrderEvents(t *testing.T) {
	q := Sequence{Sweep: &Sweep{At: 100, ReclaimAt: 90}}
	if q.OrderingError(50) == nil {
		t.Fatal("reclaim before sweep must be rejected")
	}
	q = Sequence{Sweep: &Sweep{At: 40}}
	if q.OrderingError(50) == nil {
		t.Fatal("sweep before the range sealed must be rejected")
	}
	q = Sequence{Sweep: &Sweep{At: 60, ReclaimAt: 60}, Expansion: &Expansion{StartedAt: 65, ConfirmedAt: 70}}
	if err := q.OrderingError(50); err != nil {
		t.Fatal(err)
	}
}
