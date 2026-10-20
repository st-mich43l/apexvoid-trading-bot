package crt

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
)

// targetFixture is a detector (bullish frame) over candles whose highs are given; only highs matter to the target.
func targetFixture(highs []float64, pivots []int) *detector {
	bars := make([]market.Candle, len(highs))
	for i, h := range highs {
		bars[i] = market.Candle{Time: fixtureHour + int64(i)*300, Open: h - 1, High: h, Low: h - 2, Close: h - 1}
	}
	return &detector{cfg: Config{StructurePivotBars: 2}, m5: bars, pivots: pivots, asOf: len(bars) - 1}
}

var rangeAnchor = Anchor{High: 4118, Low: 4100}

func TestTheTargetIsTheNearestStandingSwingBeforeTheOppositeEdge(t *testing.T) {
	// Swings at 4112 (index 2) and 4109 (index 5), both confirmed and untouched afterwards.
	d := targetFixture([]float64{4106, 4108, 4112, 4107, 4106, 4109, 4105, 4104, 4103}, []int{2, 5})
	if got, kind := d.target(rangeAnchor, 4106); got != 4109 || kind != TargetNearestSwingHigh {
		t.Fatalf("the nearer swing stands between the entry and the edge: %v %s", got, kind)
	}
}

func TestACrtTargetsTheOppositeEdgeOnlyWhenNothingStandsBetween(t *testing.T) {
	d := targetFixture([]float64{4106, 4108, 4112, 4107, 4106, 4105, 4104}, nil)
	if got, kind := d.target(rangeAnchor, 4106); got != 4118 || kind != TargetOppositeRange {
		t.Fatalf("no obstacle: the opposite range edge, got %v %s", got, kind)
	}
}

func TestASwingTheMarketAlreadyTookIsNotInTheWay(t *testing.T) {
	// The swing at 4109 (index 2) was traded through by index 5.
	d := targetFixture([]float64{4106, 4108, 4109, 4107, 4106, 4110, 4105}, []int{2})
	if got, kind := d.target(rangeAnchor, 4106); got != 4118 || kind != TargetOppositeRange {
		t.Fatalf("a taken swing is not an obstacle: %v %s", got, kind)
	}
}

func TestOnlyConfirmedSwingsAboveTheEntryAreObstacles(t *testing.T) {
	// Index 7 is a swing whose right-hand candles have not closed (asOf 8 < 7+2): unknown at decision time.
	// Index 2 is at 4105, below the entry top 4106: price is already above it.
	d := targetFixture([]float64{4104, 4103, 4105, 4103, 4102, 4101, 4100, 4111, 4108}, []int{2, 7})
	if got, kind := d.target(rangeAnchor, 4106); got != 4118 || kind != TargetOppositeRange {
		t.Fatalf("an unconfirmed swing and a swing below the entry are not obstacles: %v %s", got, kind)
	}
}

func TestASwingAtOrBeyondTheOppositeEdgeIsTheEdgeNotAnObstacle(t *testing.T) {
	d := targetFixture([]float64{4106, 4108, 4118, 4107, 4106, 4105, 4104}, []int{2})
	if got, kind := d.target(rangeAnchor, 4106); got != 4118 || kind != TargetOppositeRange {
		t.Fatalf("%v %s", got, kind)
	}
}
