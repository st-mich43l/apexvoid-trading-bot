package confluence

import "testing"

func TestEvaluatePortsFactorAndV2Scores(t *testing.T) {
	factors := Factors{
		HTFAligned: true, Touches: 2, WickRejection: true,
		DisplacementGrade: true, SessionContext: true,
		StructuralAgreement: true, FibTouch: true, CHoCH: true,
	}
	score := Evaluate(ZoneQuality{Score: 8, Touches: 0}, factors, DefaultConfig(), 0.75)
	// Raw = 4 + 2 + 3 + 3 + 2 + 3 + 2 + 2.5 = 21.5.
	if score.RawFactorScore != 21.5 {
		t.Fatalf("raw factor score = %v, want 21.5", score.RawFactorScore)
	}
	if score.V1Stars != 2 || score.V2Stars != 3 || score.SelectedStars != 2 {
		t.Fatalf("stars = v1:%d v2:%d selected:%d, want 2/3/2", score.V1Stars, score.V2Stars, score.SelectedStars)
	}
	if score.MADBonus != 0.75 {
		t.Fatalf("MAD bonus = %v, want 0.75", score.MADBonus)
	}
}

func TestEvaluateV1CapsTouchedZoneAtTwoStars(t *testing.T) {
	score := Evaluate(
		ZoneQuality{Score: 24.5, Touches: 1},
		Factors{HTFAligned: true, Touches: 3, WickRejection: true, DisplacementGrade: true, SessionContext: true, StructuralAgreement: true},
		DefaultConfig(), 0,
	)
	if score.V1Stars != 2 || score.SelectedStars != 2 {
		t.Fatalf("touched stars = %d/%d, want 2/2", score.V1Stars, score.SelectedStars)
	}
}

func TestEvaluateDoesNotTurnNegativeMADIntoBonus(t *testing.T) {
	score := Evaluate(ZoneQuality{}, Factors{}, DefaultConfig(), -2)
	if score.MADBonus != 0 || score.V2Raw != 0 {
		t.Fatalf("negative MAD changed score: bonus=%v raw=%v", score.MADBonus, score.V2Raw)
	}
}

// Python normalises a zone score by _ZONE_SCORE_MAX (24.5) but cuts it at the
// factor-scale star ratios 8/20.5 and 12/20.5 (_STAR_TWO/THREE_RATIO), the same
// ratios the factor branch uses. A cut at 8/24.5 and 12/24.5 over-awards stars.
func TestEvaluateZoneScoreUsesFactorScaleStarRatios(t *testing.T) {
	cfg := DefaultConfig()
	cases := []struct {
		name  string
		zone  ZoneQuality
		fib   bool
		stars int
	}{
		{"below the two-star cut", ZoneQuality{Score: 9.55}, false, 1}, // 9.55/24.5 = .390 < 8/20.5 = .3902
		{"fib touch lifts it over", ZoneQuality{Score: 9.55}, true, 2}, // 12.05/24.5 = .492
		{"just below three stars", ZoneQuality{Score: 14.3}, false, 2}, // .5837 < 12/20.5 = .5854
		{"three stars", ZoneQuality{Score: 14.35}, false, 3},           // .5857
		{"a touched zone is capped at two", ZoneQuality{Score: 20, Touches: 1}, false, 2},
	}
	for _, tc := range cases {
		got := Evaluate(tc.zone, Factors{FibTouch: tc.fib}, cfg, 0).V1Stars
		if got != tc.stars {
			t.Fatalf("%s: stars = %d, want %d", tc.name, got, tc.stars)
		}
	}
}
