package visualization_test

import (
	"bytes"
	"image/png"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/visualization"
)

func TestRender_ProducesADecodablePNGAtTheRequestedSize(t *testing.T) {
	candles := []market.Candle{
		{Time: 1, Open: 100, High: 101, Low: 99, Close: 100.5},
		{Time: 2, Open: 100.5, High: 103, Low: 100, Close: 102},
		{Time: 3, Open: 102, High: 102.2, Low: 98, Close: 99},
	}
	struc := structure.StructureState{
		Swings: []structure.Swing{{Kind: structure.SwingHigh, Layer: structure.StructureMajor, Time: 2, Price: 103}},
		Breaks: []structure.StructureBreak{{Time: 3, Event: structure.EventCHoCH}},
	}
	liq := liquidity.LiquidityState{
		Pools: []liquidity.Pool{{Side: liquidity.LiquidityBuySide, Low: 102.8, High: 103.2}},
	}
	opts := visualization.Options{Width: 400, Height: 200, Margin: 10}

	var buf bytes.Buffer
	if err := visualization.Render(candles, struc, liq, opts, &buf); err != nil {
		t.Fatalf("Render failed: %v", err)
	}
	img, err := png.Decode(&buf)
	if err != nil {
		t.Fatalf("output is not a valid PNG: %v", err)
	}
	if img.Bounds().Dx() != 400 || img.Bounds().Dy() != 200 {
		t.Errorf("expected 400x200, got %dx%d", img.Bounds().Dx(), img.Bounds().Dy())
	}

	// Not blank: a bullish/bearish candle body must have painted at least
	// one non-background pixel somewhere in the image.
	nonBackground := 0
	bg := img.At(0, 0)
	for y := 0; y < img.Bounds().Dy(); y++ {
		for x := 0; x < img.Bounds().Dx(); x++ {
			if img.At(x, y) != bg {
				nonBackground++
			}
		}
	}
	if nonBackground == 0 {
		t.Error("expected the chart to actually draw something, got an entirely uniform image")
	}
}

func TestRender_EmptyCandlesStillProducesAValidImage(t *testing.T) {
	var buf bytes.Buffer
	err := visualization.Render(nil, structure.StructureState{}, liquidity.LiquidityState{}, visualization.DefaultOptions(), &buf)
	if err != nil {
		t.Fatalf("Render with no candles must not error, got: %v", err)
	}
	if _, err := png.Decode(&buf); err != nil {
		t.Fatalf("output is not a valid PNG: %v", err)
	}
}
