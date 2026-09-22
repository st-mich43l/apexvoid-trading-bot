package market

import "testing"

func TestCandleWindowEvictsOldestPastCapacity(t *testing.T) {
	w := NewCandleWindow(3)
	for i := int64(1); i <= 5; i++ {
		w.Push(Candle{Time: i, Close: float64(i)})
	}
	if w.Len() != 3 {
		t.Fatalf("Len() = %d, want 3", w.Len())
	}
	snap := w.Snapshot()
	got := []int64{snap[0].Time, snap[1].Time, snap[2].Time}
	want := []int64{3, 4, 5}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("snapshot times = %v, want %v", got, want)
		}
	}
	if w.Last().Time != 5 {
		t.Fatalf("Last().Time = %d, want 5", w.Last().Time)
	}
	if w.At(0).Time != 3 {
		t.Fatalf("At(0).Time = %d, want 3 (oldest retained)", w.At(0).Time)
	}
}

func TestCandleWindowBelowCapacity(t *testing.T) {
	w := NewCandleWindow(10)
	w.Push(Candle{Time: 1})
	w.Push(Candle{Time: 2})
	if w.Len() != 2 {
		t.Fatalf("Len() = %d, want 2", w.Len())
	}
	if w.Capacity() != 10 {
		t.Fatalf("Capacity() = %d, want 10", w.Capacity())
	}
}

func TestCandleWindowAtPanicsOutOfRange(t *testing.T) {
	defer func() {
		if recover() == nil {
			t.Fatal("expected panic on out-of-range At()")
		}
	}()
	w := NewCandleWindow(2)
	w.Push(Candle{Time: 1})
	_ = w.At(5)
}

func TestNewCandleWindowPanicsOnNonPositiveCapacity(t *testing.T) {
	defer func() {
		if recover() == nil {
			t.Fatal("expected panic on non-positive capacity")
		}
	}()
	NewCandleWindow(0)
}
