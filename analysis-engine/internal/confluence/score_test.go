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
