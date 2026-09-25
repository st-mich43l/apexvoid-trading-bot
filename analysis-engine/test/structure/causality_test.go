package structure_test

import (
	"math/rand"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// These tests are the actual proof behind every causality claim made in
// this package's doc comments (source task §10, §48; DoD item 34) — not
// an assertion by construction. See docs/analysis/market-structure-v2.md's
// causality section for the exact contract being proven here.

func randomCandles(n int, seed int64) []market.Candle {
	r := rand.New(rand.NewSource(seed))
	price := 100.0
	out := make([]market.Candle, n)
	for i := 0; i < n; i++ {
		move := (r.Float64() - 0.5) * 2.0
		open := price
		cl := price + move
		hi := open
		if cl > hi {
			hi = cl
		}
		lo := open
		if cl < lo {
			lo = cl
		}
		hi += r.Float64() * 0.5
		lo -= r.Float64() * 0.5
		out[i] = market.Candle{Time: int64(i + 1), Open: open, High: hi, Low: lo, Close: cl, Volume: 1}
		price = cl
	}
	return out
}

func TestDetectPivots_NeverConfirmsBeyondGivenData(t *testing.T) {
	full := randomCandles(200, 42)
	atr := flatATR(len(full), 2.0)
	for _, T := range []int{10, 20, 50, 100, 150, 200} {
		pivots := structure.DetectPivots(full[:T], 3, 3, atr[:T])
		lastTime := full[T-1].Time
		for _, p := range pivots {
			if p.ConfirmedAt > lastTime {
				t.Fatalf("T=%d: pivot ConfirmedAt=%d exceeds the last given candle's time=%d — lookahead violation", T, p.ConfirmedAt, lastTime)
			}
		}
	}
}

func TestDetectPivots_ConfirmedPivotsAreStableAsMoreDataArrives(t *testing.T) {
	full := randomCandles(200, 42)
	atr := flatATR(len(full), 2.0)
	fullPivots := structure.DetectPivots(full, 3, 3, atr)
	byBar := make(map[int]structure.Pivot, len(fullPivots))
	for _, p := range fullPivots {
		byBar[p.BarIndex] = p
	}

	for _, T := range []int{20, 50, 100, 150} {
		truncated := structure.DetectPivots(full[:T], 3, 3, atr[:T])
		for _, p := range truncated {
			want, ok := byBar[p.BarIndex]
			if !ok {
				t.Fatalf("T=%d: pivot at bar %d appears in the truncated run but never in the full run", T, p.BarIndex)
			}
			if p != want {
				t.Fatalf("T=%d: pivot at bar %d changed between the truncated and full run:\n  truncated=%+v\n  full=%+v", T, p.BarIndex, p, want)
			}
		}
	}
}

// TestDetectBreak_ResolvedClassificationIsStableAsMoreDataArrives is the
// break-side counterpart: once DetectBreak resolves a classification
// strictly before the given candles run out (ConfirmedAt is earlier than
// the last given candle — proving the window genuinely elapsed, not that
// data simply ran out), extending the candle slice with MORE bars past
// that point must return byte-identical results. This is the honest
// causality contract for DetectBreak documented in break.go: it does NOT
// claim the classification never changes before resolution (a shorter
// prefix may legitimately report a different, still-provisional read —
// that's correct behavior, not a bug) — only that a genuinely resolved
// read never gets revised by data that arrives after it resolved.
func TestDetectBreak_ResolvedClassificationIsStableAsMoreDataArrives(t *testing.T) {
	level := breakLevel()
	cfg := breakCfg(100)
	base := []market.Candle{
		c(1, 99.8, 100.6, 99.7, 100.5), c(2, 100.5, 100.7, 100.3, 100.6),
		c(3, 100.6, 100.8, 100.4, 100.7), c(4, 100.7, 100.9, 100.5, 100.8),
		c(5, 100.8, 100.9, 100.6, 100.85), c(6, 100.85, 100.95, 100.7, 100.9),
	}
	atrBase := flatATR(len(base), 1.0)
	short := structure.DetectBreak(base, 0, level, atrBase, cfg)
	if short == nil {
		t.Fatal("expected a break")
	}
	if short.ConfirmedAt >= base[len(base)-1].Time {
		t.Fatalf("fixture setup error: resolution must happen strictly before the given data ends (ConfirmedAt=%d, last=%d)", short.ConfirmedAt, base[len(base)-1].Time)
	}

	extended := append(append([]market.Candle{}, base...),
		c(7, 100.9, 101.0, 100.8, 100.95), c(8, 100.95, 101.05, 100.85, 101.0),
		c(9, 101.0, 101.1, 100.9, 101.05), c(10, 101.05, 101.15, 100.95, 101.1),
	)
	atrExtended := flatATR(len(extended), 1.0)
	long := structure.DetectBreak(extended, 0, level, atrExtended, cfg)
	if long == nil || *long != *short {
		t.Fatalf("break classification changed after adding data PAST the resolution point:\n  short (%d bars)=%+v\n  long (%d bars)=%+v", len(base), short, len(extended), long)
	}
}
