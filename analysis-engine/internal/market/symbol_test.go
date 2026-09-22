package market

import "testing"

func TestNewGeometryFailsClosedOnBadInput(t *testing.T) {
	cases := []struct {
		name        string
		symbol      string
		pipSize     float64
		priceDigits int
	}{
		{"empty symbol", "", 0.1, 2},
		{"zero pip size", "XAU", 0, 2},
		{"negative pip size", "XAU", -0.1, 2},
		{"negative digits", "XAU", 0.1, -1},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if _, err := NewGeometry(c.symbol, c.symbol, c.pipSize, c.priceDigits); err == nil {
				t.Fatalf("expected an error, got none")
			}
		})
	}
}

func TestGeometryPipsBetween(t *testing.T) {
	xau, err := NewGeometry("XAU", "XAUUSD", 0.1, 2)
	if err != nil {
		t.Fatalf("NewGeometry: %v", err)
	}
	// 4360.55 -> 4358.38 is 2.17 price, i.e. 21.7 pips at pip_size 0.1 —
	// matches the live XAU key-level band width seen in production
	// (docs/go-analysis-migration-audit.md's own worked ATR example).
	got := xau.PipsBetween(4360.55, 4358.38)
	want := 21.7
	if diff := got - want; diff > 1e-9 || diff < -1e-9 {
		t.Fatalf("PipsBetween = %v, want %v", got, want)
	}
}

func TestGeometryRoundToTick(t *testing.T) {
	xau, err := NewGeometry("XAU", "XAUUSD", 0.1, 2)
	if err != nil {
		t.Fatalf("NewGeometry: %v", err)
	}
	got := xau.RoundToTick(4354.7649)
	want := 4354.76
	if diff := got - want; diff > 1e-9 || diff < -1e-9 {
		t.Fatalf("RoundToTick = %v, want %v", got, want)
	}
}

func TestParseTimeframeUnknownFailsClosed(t *testing.T) {
	if _, err := ParseTimeframe("M7"); err == nil {
		t.Fatal("expected an error for an unrecognized timeframe")
	}
}

func TestParseTimeframeKnown(t *testing.T) {
	tf, err := ParseTimeframe("M5")
	if err != nil {
		t.Fatalf("ParseTimeframe: %v", err)
	}
	minutes, ok := tf.Minutes()
	if !ok || minutes != 5 {
		t.Fatalf("Minutes() = (%d, %v), want (5, true)", minutes, ok)
	}
}
