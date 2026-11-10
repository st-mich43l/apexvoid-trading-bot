package impulsepullback

import (
	"math"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
)

func inputs(rewardPips, corridorRoomPips float64) qualityInputs {
	return qualityInputs{
		displacementATR: 2, minDisplacementATR: 1.5, bodyDominance: 0.7, minBodyDominance: 0.5, pullbackRatio: 0.3, maxPullbackRatio: 0.6,
		referenceTouches: 3, buy: true, price: 4000.2, reference: 4000, atr: 2, rewardPips: rewardPips, corridorRoomPips: corridorRoomPips, stop: 30,
	}
}

// The corridor's room is the structural objective. The ladder rung the strategy selected is a reward:risk it chose and must not score as room.
func TestRoomIsTheCorridorsNotTheSelectedRung(t *testing.T) {
	score := func(in qualityInputs) float64 { return scoreQuality(in).Components()["score_room"] }
	// The same 2R rung (60 pips): with only 1R of corridor room it scores 0, with 3R it scores 1, with 2R half.
	for _, c := range []struct{ corridor, want float64 }{{30, 0}, {60, 0.5}, {90, 1}, {200, 1}, {10, 0}, {0, 0}, {-15, 0}} {
		if got := score(inputs(60, c.corridor)); math.Abs(got-c.want) > 1e-9 {
			t.Errorf("rung 60 corridor %.0f: room score %v, want %v", c.corridor, got, c.want)
		}
	}
	// The rung changing with the room unchanged does not move the score.
	if a, b := score(inputs(30, 60)), score(inputs(60, 60)); a != b {
		t.Errorf("room score moved with the selected rung (%v vs %v)", a, b)
	}
}

func TestQualityIsFiniteBoundedAndTheOverallIsTheMeanOfItsDimensions(t *testing.T) {
	for _, corridor := range []float64{-20, 0, 30, 60, 90, 400} {
		m := scoreQuality(inputs(60, corridor))
		sum, n := 0.0, 0
		for name, v := range m.Components() {
			if math.IsNaN(v) || math.IsInf(v, 0) {
				t.Errorf("corridor %v: %s = %v is not finite", corridor, name, v)
			}
			if strings.HasPrefix(name, quality.Prefix) {
				if v < 0 || v > 1 {
					t.Errorf("corridor %v: %s = %v outside [0, 1]", corridor, name, v)
				}
				sum += v
				n++
			}
		}
		if n != 6 || math.Abs(m.Overall()-sum/float64(n)) > 1e-9 {
			t.Errorf("corridor %v: overall %v is not the mean %v of six dimensions (%d)", corridor, m.Overall(), sum/float64(n), n)
		}
		if err := quality.Verify(m.Overall(), m.Components()); err != nil {
			t.Error(err)
		}
	}
}

func TestTheCorridorRoomIsPublishedAsAMeasurement(t *testing.T) {
	c := scoreQuality(inputs(60, 81)).Components()
	if c["corridor_room_pips"] != 81 || math.Abs(c["corridor_room_r"]-2.7) > 1e-9 || c["scalp_reward_risk"] != 2 {
		t.Errorf("corridor room %v pips %v R, selected rung %vR", c["corridor_room_pips"], c["corridor_room_r"], c["scalp_reward_risk"])
	}
}

func TestACorridorSharesTheUsableRoomFactor(t *testing.T) {
	if corridorUsableRoom <= 0 || corridorUsableRoom >= 1 {
		t.Errorf("corridorUsableRoom = %v, want a fraction in (0, 1): the last part of the corridor is its edge", corridorUsableRoom)
	}
}

func TestCorridorRoomIsMeasuredFromTheWorstPlannedEntry(t *testing.T) {
	const low, high, pip = 3990.0, 4010.0, 0.1
	// BUY: the zone's worst entry is its high. A context price anywhere else (here 3991, then 4009) does not change the room.
	if got := corridorRoomPips(market.Buy, 4002, low, high, pip); math.Abs(got-80) > 1e-9 {
		t.Errorf("BUY room from 4002 = %v, want 80", got)
	}
	if got := corridorRoomPips(market.Sell, 3998, low, high, pip); math.Abs(got-80) > 1e-9 {
		t.Errorf("SELL room from 3998 = %v, want 80", got)
	}
	// At and beyond the corridor edge there is none.
	for _, c := range []struct {
		direction market.Direction
		worst     float64
	}{{market.Buy, 4010}, {market.Buy, 4015}, {market.Sell, 3990}, {market.Sell, 3985}} {
		if got := corridorRoomPips(c.direction, c.worst, low, high, pip); got > 0 {
			t.Errorf("%s worst %v: room %v, want none", c.direction, c.worst, got)
		}
	}
}

// The location is direction-aware: a BUY deeper in the zone than its proximal edge is not chasing, one above it is; a SELL mirrors it.
func TestPullbackEntryLocationIsDirectionAware(t *testing.T) {
	at := func(buy bool, price float64) float64 {
		in := inputs(60, 90)
		in.buy, in.price, in.reference, in.atr = buy, price, 4000, 2
		return scoreQuality(in).Components()["score_entry_location"]
	}
	for _, c := range []struct {
		name string
		buy  bool
		px   float64
		want func(float64) bool
	}{
		{"BUY deeper in the zone", true, 3999, func(v float64) bool { return v == 1 }},
		{"BUY at the edge", true, 4000, func(v float64) bool { return v == 1 }},
		{"BUY above the edge", true, 4002, func(v float64) bool { return v < 1 }},
		{"SELL higher in the zone", false, 4001, func(v float64) bool { return v == 1 }},
		{"SELL at the edge", false, 4000, func(v float64) bool { return v == 1 }},
		{"SELL below the edge", false, 3998, func(v float64) bool { return v < 1 }},
	} {
		if got := at(c.buy, c.px); !c.want(got) {
			t.Errorf("%s: score %v", c.name, got)
		}
	}
	m := scoreQuality(inputs(60, 90)).Components()
	if m["quality_model_version"] != 3 {
		t.Errorf("model version %v, want 3", m["quality_model_version"])
	}
}
