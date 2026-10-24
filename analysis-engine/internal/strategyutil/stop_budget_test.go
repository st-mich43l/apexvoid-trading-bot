package strategyutil

import "testing"

func TestStopBudgetClassifiesTheStructuralStop(t *testing.T) {
	const pip = 0.1 // XAU
	budget := StopBudget{MaxPips: 70, MinPips: 50, PipSize: pip}
	cases := []struct {
		name          string
		reference     float64
		invalidation  float64
		wantCode      string
		wantPublished bool
	}{
		{"BUY below the floor widens at execution", 4100.0, 4100.0 - 30*pip, StopBudgetFloorEvidence, true},
		{"BUY exactly at the floor is structural", 4100.0, 4100.0 - 50*pip, StopBudgetStructuralEvidence, true},
		{"BUY inside the budget", 4100.0, 4100.0 - 60*pip, StopBudgetStructuralEvidence, true},
		{"BUY exactly at the cap publishes", 4100.0, 4100.0 - 70*pip, StopBudgetStructuralEvidence, true},
		{"BUY just above the cap is refused", 4100.0, 4100.0 - 70.1*pip, "", false},
		{"SELL below the floor", 4100.0, 4100.0 + 20*pip, StopBudgetFloorEvidence, true},
		{"SELL inside the budget", 4100.0, 4100.0 + 65*pip, StopBudgetStructuralEvidence, true},
		{"SELL above the cap is refused", 4100.0, 4100.0 + 90*pip, "", false},
	}
	for _, c := range cases {
		code, published := budget.Apply(c.reference, c.invalidation)
		if code != c.wantCode || published != c.wantPublished {
			t.Errorf("%s: got (%q, %v), want (%q, %v)", c.name, code, published, c.wantCode, c.wantPublished)
		}
	}
}

func TestStopBudgetWithoutABudgetIsToday(t *testing.T) {
	for _, b := range []StopBudget{{}, {PipSize: 0.1}, {MaxPips: 70, MinPips: 50}} { // the last has no pip size: inactive
		if b.Active() {
			t.Errorf("%+v must be inactive", b)
		}
		if code, published := b.Apply(4100, 4000); code != "" || !published {
			t.Errorf("%+v: got (%q, %v), want no evidence and published", b, code, published)
		}
	}
}

func TestStopBudgetCountsOnlyRefusals(t *testing.T) {
	var counter RefusalCounter
	b := StopBudget{MaxPips: 20, MinPips: 12, PipSize: 0.0001, Refusals: &counter}
	b.Apply(1.1000, 1.1000-0.0030)  // 30 pips: refused
	b.Apply(1.1000, 1.1000-0.0015)  // 15 pips: structural
	b.Apply(1.1000, 1.1000+0.0050)  // 50 pips: refused
	b.Apply(1.1000, 1.1000-0.00005) // 0.5 pip: floor
	if got := counter.TakeStopBudgetRefusals(); got != 2 {
		t.Fatalf("refusals %d, want 2", got)
	}
	if got := counter.TakeStopBudgetRefusals(); got != 0 {
		t.Fatalf("the count must reset after it is taken, got %d", got)
	}
}

func TestParseStopBudget(t *testing.T) {
	got, err := ParseStopBudget(map[string]any{"execution_stop_max_pips": 70.0, "execution_stop_min_pips": 50.0, "execution_pip_size": 0.1})
	if err != nil || got.MaxPips != 70 || got.MinPips != 50 || got.PipSize != 0.1 {
		t.Fatalf("got %+v, %v", got, err)
	}
	if got, err := ParseStopBudget(map[string]any{"minimum_touches": 2}); err != nil || got.Active() {
		t.Fatalf("absent keys mean no budget: %+v, %v", got, err)
	}
	for name, params := range map[string]map[string]any{
		"zero cap":          {"execution_stop_max_pips": 0.0, "execution_pip_size": 0.1},
		"negative floor":    {"execution_stop_min_pips": -1.0, "execution_pip_size": 0.1},
		"floor above cap":   {"execution_stop_max_pips": 20.0, "execution_stop_min_pips": 30.0, "execution_pip_size": 0.1},
		"cap without a pip": {"execution_stop_max_pips": 20.0},
		"not a number":      {"execution_stop_max_pips": "20", "execution_pip_size": 0.1},
	} {
		if _, err := ParseStopBudget(params); err == nil {
			t.Errorf("%s must be rejected", name)
		}
	}
}
