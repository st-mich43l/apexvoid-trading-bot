package keylevel_test

import (
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/keylevel"
)

// TestRoleExplicitSupportStaysSupportUntilAcceptedBreak ports
// classify_key_level_role's explicit-kind branch: a "support"-labeled
// level stays ROLE_SUPPORT even while individual closes dip below
// band_low, until `breakoutAcceptBars` CONSECUTIVE closes (from the most
// recent, backward) accept beyond it.
func TestRoleExplicitSupportStaysSupportUntilAcceptedBreak(t *testing.T) {
	// bandLow=100, bandHigh=110. breakoutAcceptBars=3.
	notEnough := []float64{99, 101, 99} // only the last close is below band_low; not 3 consecutive
	if r := keylevel.Role("support", 100, 110, notEnough, 3); r != keylevel.RoleSupport {
		t.Errorf("got %s, want support (only 1 consecutive close below band_low, need 3)", r)
	}

	accepted := []float64{99, 99, 99} // 3 consecutive closes below band_low
	if r := keylevel.Role("support", 100, 110, accepted, 3); r != keylevel.RoleBrokenSupport {
		t.Errorf("got %s, want broken_support (3 consecutive accepted closes below band_low)", r)
	}
}

// TestRoleExplicitResistanceStaysResistanceUntilAcceptedBreak mirrors the
// support case for the resistance side.
func TestRoleExplicitResistanceStaysResistanceUntilAcceptedBreak(t *testing.T) {
	accepted := []float64{111, 111} // 2 consecutive closes above band_high
	if r := keylevel.Role("resistance", 100, 110, accepted, 2); r != keylevel.RoleBrokenResistance {
		t.Errorf("got %s, want broken_resistance", r)
	}
	stillHolding := []float64{111, 105} // most recent close (105) is back inside the band
	if r := keylevel.Role("resistance", 100, 110, stillHolding, 2); r != keylevel.RoleResistance {
		t.Errorf("got %s, want resistance (most recent close is not an accepted break)", r)
	}
}

// TestRoleKindSubstringMatchIsCaseInsensitiveAndSubstringBased ports the
// exact matching rule: "support"/"low" anywhere in kind (case-folded)
// means explicit support; "resist"/"high" means explicit resistance.
func TestRoleKindSubstringMatchIsCaseInsensitiveAndSubstringBased(t *testing.T) {
	cases := []struct {
		kind string
		want keylevel.RoleKind
	}{
		{"Support", keylevel.RoleSupport},
		{"SWING_LOW", keylevel.RoleSupport}, // "low" substring
		{"Resistance", keylevel.RoleResistance},
		{"SWING_HIGH", keylevel.RoleResistance}, // "high" substring
	}
	for _, tc := range cases {
		if r := keylevel.Role(tc.kind, 100, 110, nil, 2); r != tc.want {
			t.Errorf("kind=%q: got %s, want %s", tc.kind, r, tc.want)
		}
	}
}

// TestRoleFallsBackToAcceptedBreakDirectionWithoutExplicitKind ports the
// no-explicit-kind branches: reaction/round (or any kind naming neither
// support/low nor resistance/high) falls through to whichever side, if
// any, has an accepted break.
func TestRoleFallsBackToAcceptedBreakDirectionWithoutExplicitKind(t *testing.T) {
	if r := keylevel.Role("reaction", 100, 110, []float64{111, 111}, 2); r != keylevel.RoleBrokenResistance {
		t.Errorf("got %s, want broken_resistance (accepted break above band_high)", r)
	}
	if r := keylevel.Role("round", 100, 110, []float64{99, 99}, 2); r != keylevel.RoleBrokenSupport {
		t.Errorf("got %s, want broken_support (accepted break below band_low)", r)
	}
	if r := keylevel.Role("reaction", 100, 110, []float64{105, 106}, 2); r != keylevel.RoleAmbiguous {
		t.Errorf("got %s, want ambiguous (no explicit kind, no accepted break)", r)
	}
}

// TestRoleBreakoutAcceptBarsClampsToAtLeastOne ports `required = max(1,
// int(breakout_accept_bars))` — a zero or negative accept-bars config
// must not make every single close an instant accepted break... actually
// max(1,...) means EVEN ONE close beyond the edge is enough when
// breakoutAcceptBars <= 1. Verifies the clamp, not a stricter gate.
func TestRoleBreakoutAcceptBarsClampsToAtLeastOne(t *testing.T) {
	if r := keylevel.Role("reaction", 100, 110, []float64{111}, 0); r != keylevel.RoleBrokenResistance {
		t.Errorf("got %s, want broken_resistance (breakoutAcceptBars<=0 clamps to 1, one close beyond band_high is enough)", r)
	}
}

func TestRoleWithNoCloses(t *testing.T) {
	if r := keylevel.Role("support", 100, 110, nil, 2); r != keylevel.RoleSupport {
		t.Errorf("got %s, want support (no closes at all -> no accepted break)", r)
	}
	if r := keylevel.Role("reaction", 100, 110, nil, 2); r != keylevel.RoleAmbiguous {
		t.Errorf("got %s, want ambiguous", r)
	}
}
