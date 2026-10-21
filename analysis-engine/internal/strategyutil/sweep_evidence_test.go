package strategyutil

import (
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/reaction"
	"testing"

	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/test/legacyfixture"
)

// Ten M5 bars open at 300, 600, ..., 3000; ATR 1; price 100. A finalized Asia low rests at 99.0 from 900.
func sweepDetector(t *testing.T, direction market.Direction, bars []market.Candle, sessions []techniquezone.SessionRef, grabs []techniquezone.Grab, frozen bool) *LegacyDetector {
	t.Helper()
	ctx := detectorWith(t, bars, func(f *analysiscontext.LegacyFrame) {
		f.Sessions, f.Grabs = sessions, grabs
	})
	settings := settings(t)
	settings.FrozenSessionContext = frozen
	d, ok := NewLegacyDetector(ctx, market.M5, direction, settings)
	if !ok {
		t.Fatal("detector must bind")
	}
	return d
}

func detectorWith(t *testing.T, bars []market.Candle, mutate func(*analysiscontext.LegacyFrame)) *analysiscontext.MarketContext {
	t.Helper()
	return legacyfixture.Context(bars, 1, func(f *analysiscontext.LegacyFrame, _ *analysiscontext.LegacyRead) { mutate(f) })
}

func asiaLow(availableAt int64) []techniquezone.SessionRef {
	return []techniquezone.SessionRef{{Name: "ASIA_L", Price: 99.0, AvailableAt: availableAt}}
}

func quiet() []market.Candle {
	bars := make([]market.Candle, 10)
	for i := range bars {
		bars[i] = market.Candle{Time: int64(i+1) * 300, Open: 100, High: 100.5, Low: 99.5, Close: 100}
	}
	return bars
}

func TestASessionLowSweptAndReclaimedIsEvidenceForABuy(t *testing.T) {
	bars := quiet()
	bars[5] = market.Candle{Time: 1800, Open: 99.6, High: 99.8, Low: 98.4, Close: 99.5} // runs through 99.0, closes back above
	d := sweepDetector(t, market.Buy, bars, asiaLow(900), nil, false)
	sweeps := d.SweepsSupporting(market.Buy, 0, 3000)
	if len(sweeps) != 1 || sweeps[0].Kind != SweepSession || sweeps[0].Name != "ASIA_L" || sweeps[0].SweepTime != 1800 || sweeps[0].ReclaimTime != 1800 {
		t.Fatalf("the sweep of the finalized Asia low must be one session sweep: %+v", sweeps)
	}
	if !d.SessionSweepBetween(0, 3000) {
		t.Fatal("a swept and reclaimed finalized session low is session context")
	}
	if d.SweepsSupporting(market.Sell, 0, 3000) != nil {
		t.Fatal("taking the stops below a low is no evidence for a SELL")
	}
}

func TestOnlyFinalizedLiquidityCanBeSwept(t *testing.T) {
	bars := quiet()
	bars[5] = market.Candle{Time: 1800, Open: 99.6, High: 99.8, Low: 98.4, Close: 99.5}
	// A developing extreme has no AvailableAt: it is not liquidity yet.
	if d := sweepDetector(t, market.Buy, bars, asiaLow(0), nil, false); d.SessionSweepBetween(0, 3000) {
		t.Fatal("a developing session extreme is not a finalized level")
	}
	// A level that only became knowable after the spike was not there to be swept.
	if d := sweepDetector(t, market.Buy, bars, asiaLow(2100), nil, false); d.SessionSweepBetween(0, 3000) {
		t.Fatal("a level that closed after the spike cannot have been swept by it")
	}
}

func TestAnAcceptedBreachConsumesTheLevel(t *testing.T) {
	bars := quiet()
	// The first breach closes below the level and stays there longer than the reaction window (3 bars): accepted.
	for i := 4; i <= 8; i++ {
		bars[i] = market.Candle{Time: int64(i+1) * 300, Open: 98.8, High: 98.9, Low: 98.2, Close: 98.5}
	}
	// A later wick-and-reclaim is a new episode on a level the market already accepted below, not a sweep of it.
	bars[9] = market.Candle{Time: 3000, Open: 98.6, High: 99.4, Low: 98.0, Close: 99.3}
	if d := sweepDetector(t, market.Buy, bars, asiaLow(900), nil, false); d.SessionSweepBetween(0, 3000) {
		t.Fatal("a level the market accepted below is consumed, not swept")
	}
}

func TestASweepIsNotVisibleBeforeItsReclaim(t *testing.T) {
	bars := quiet()
	bars[5] = market.Candle{Time: 1800, Open: 99.6, High: 99.7, Low: 98.4, Close: 98.9}
	bars[6] = market.Candle{Time: 2100, Open: 98.9, High: 99.6, Low: 98.8, Close: 99.5} // the reclaim
	d := sweepDetector(t, market.Buy, bars, asiaLow(900), nil, false)
	if got := d.SweepsSupporting(market.Buy, 0, 1800); len(got) != 0 {
		t.Fatalf("at the sweep bar the close had not come back: %+v", got)
	}
	if got := d.SweepsSupporting(market.Buy, 0, 2100); len(got) != 1 || got[0].ReclaimTime != 2100 {
		t.Fatalf("with the reclaim closed the sweep exists: %+v", got)
	}
}

func TestOneEventIsOneSweepWhateverNumberOfLevelsItTook(t *testing.T) {
	bars := quiet()
	bars[5] = market.Candle{Time: 1800, Open: 99.6, High: 99.8, Low: 98.4, Close: 99.5}
	// An equal-low pool at 99.05 swept by the same candle describes the same price movement.
	pool := techniquezone.Pool{Side: "sell", Level: 99.05, Band: .1, Touches: 3, LastIndex: 2}
	grab := techniquezone.Grab{Index: 5, Direction: "bull", Grade: "A", Pool: pool}
	d := sweepDetector(t, market.Buy, bars, asiaLow(900), []techniquezone.Grab{grab}, false)
	sweeps := d.SweepsSupporting(market.Buy, 0, 3000)
	if len(sweeps) != 1 || sweeps[0].Kind != SweepSession {
		t.Fatalf("the session low and the equal pool are one event, kept as the more authoritative session sweep: %+v", sweeps)
	}
	// A pool the session level does not coincide with is a separate, independent observation.
	far := techniquezone.Pool{Side: "sell", Level: 98.2, Band: .1, Touches: 2, LastIndex: 2}
	d = sweepDetector(t, market.Buy, bars, asiaLow(900), []techniquezone.Grab{{Index: 5, Direction: "bull", Grade: "B", Pool: far}}, false)
	if got := d.SweepsSupporting(market.Buy, 0, 3000); len(got) != 2 {
		t.Fatalf("two different levels are two observations: %+v", got)
	}
}

func TestThePoolOfTheSweepCandleItselfIsNotYetLiquidity(t *testing.T) {
	bars := quiet()
	bars[5] = market.Candle{Time: 1800, Open: 99.6, High: 99.8, Low: 98.4, Close: 99.5}
	pool := techniquezone.Pool{Side: "sell", Level: 98.9, Band: .1, Touches: 1, LastIndex: 5}
	d := sweepDetector(t, market.Buy, bars, nil, []techniquezone.Grab{{Index: 5, Direction: "bull", Grade: "A", Pool: pool}}, false)
	if got := d.SweepsSupporting(market.Buy, 0, 3000); len(got) != 0 {
		t.Fatalf("a swing confirmed by the sweep candle cannot be the liquidity it swept: %+v", got)
	}
}

func TestTheFrozenDetectorsSetTheSessionContextOnEveryDecision(t *testing.T) {
	d := sweepDetector(t, market.Buy, quiet(), nil, nil, true)
	if !d.SessionSweepBetween(0, 3000) {
		t.Fatal("the frozen switch reproduces the oracle's constant factor")
	}
	if d := sweepDetector(t, market.Buy, quiet(), nil, nil, false); d.SessionSweepBetween(0, 3000) {
		t.Fatal("without a sweep there is no session context")
	}
}

// The sweep of the reaction's own candle is the reaction, which its pattern already credits: it is not a second, independent fact.
func TestASweepOnTheReactionCandleIsTheReactionNotSessionContext(t *testing.T) {
	bars := quiet()
	bars[5] = market.Candle{Time: 1800, Open: 99.6, High: 99.8, Low: 98.4, Close: 99.5}
	d := sweepDetector(t, market.Buy, bars, asiaLow(900), nil, false)
	same := &LegacyConfirmation{TouchIndex: 5, ConfirmationIndex: 5, TouchTime: 1800, ConfirmationTime: 1800}
	if d.SessionSweepContext(same) {
		t.Fatal("the candle that swept the level is the reaction's touch: one event")
	}
	// The reaction touches the zone one candle after the sweep completed: the sweep led to it, an independent earlier fact.
	later := &LegacyConfirmation{TouchIndex: 6, ConfirmationIndex: 6, TouchTime: 2100, ConfirmationTime: 2100}
	if !d.SessionSweepContext(later) {
		t.Fatal("a sweep that completed before the touch is session context")
	}
	// Three candles earlier is outside the reaction lookback.
	far := &LegacyConfirmation{TouchIndex: 9, ConfirmationIndex: 9, TouchTime: 3000, ConfirmationTime: 3000}
	if d.SessionSweepContext(far) {
		t.Fatal("a sweep older than the reaction lookback is not part of this move")
	}
}

// A rejection_choch is a wick rejection candle plus a change of character: two events, credited as a wick and a choch. The structural
// agreement is not set from the same break (the frozen mapping named the break twice and the wick not at all).
func TestARejectionWithACHoCHIsAWickAndAChoCHNotAStructuralAgreement(t *testing.T) {
	got := FactorsForConfirmation(confluence.Factors{HTFAligned: true}, reaction.TypeRejectionCHoCH)
	if !got.WickRejection || !got.CHoCH || got.StructuralAgreement || !got.HTFAligned {
		t.Fatalf("%+v", got)
	}
	if plain := FactorsForConfirmation(confluence.Factors{}, reaction.TypeWickRejection); plain.CHoCH || plain.WickRejection {
		t.Fatalf("a plain confirmation adds nothing: %+v", plain)
	}
	// The total is what the frozen mapping paid (3 + 2): the stars of every oracle decision are unchanged.
	score := confluence.Evaluate(confluence.ZoneQuality{}, got, confluence.DefaultConfig(), 0)
	if score.RawFactorScore != 4+3+2 {
		t.Fatalf("raw %v", score.RawFactorScore)
	}
}
