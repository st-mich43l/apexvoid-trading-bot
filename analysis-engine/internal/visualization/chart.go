// Package visualization is observability, not decision logic (source task
// §35/§46). It consumes candles plus an already-computed
// structure.StructureState/liquidity.LiquidityState and renders them — it
// must never compute structure itself, the same discipline
// internal/engine's own AnalysisSnapshot enforces at the service boundary.
//
// A leaf package (docs/architecture/dependency-rules.md): may import any
// core type; nothing core imports it.
//
// Deliberately stdlib-only (image/image/png), matching this module's
// established preference for adding a dependency only when it carries
// real weight (go.mod has exactly one, yaml.v3) — source task §45's own
// "does not need TradingView-level UI." No text rendering (Go's stdlib
// has no font rasterizer without a second dependency): BOS/CHoCH/swing
// layer are distinguished by marker shape and color, not by a label.
package visualization

import (
	"image"
	"image/color"
	"image/png"
	"io"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
)

// Options controls chart geometry.
type Options struct {
	Width, Height int
	Margin        int // pixels reserved on every edge so a marker at the price/time extremes is never clipped
}

// DefaultOptions is a reasonable default canvas size.
func DefaultOptions() Options {
	return Options{Width: 1200, Height: 600, Margin: 20}
}

var (
	colorBackground = color.RGBA{250, 250, 248, 255}
	colorBullish    = color.RGBA{38, 139, 84, 255}
	colorBearish    = color.RGBA{200, 60, 60, 255}
	colorWick       = color.RGBA{90, 90, 90, 255}
	colorPoolBuy    = color.RGBA{90, 140, 220, 120}
	colorPoolSell   = color.RGBA{230, 160, 60, 120}
	colorSwingHigh  = color.RGBA{170, 30, 30, 255}
	colorSwingLow   = color.RGBA{20, 110, 60, 255}
	colorBOS        = color.RGBA{40, 100, 220, 255}
	colorCHoCH      = color.RGBA{200, 30, 160, 255}
)

// Render draws candles plus struct's swings/breaks and liq's pools onto a
// PNG image and writes it to w. Render never computes anything analytical
// — every input is already-produced state (source task §46).
func Render(candles []market.Candle, struc structure.StructureState, liq liquidity.LiquidityState, opts Options, w io.Writer) error {
	img := image.NewRGBA(image.Rect(0, 0, opts.Width, opts.Height))
	fillRect(img, 0, 0, opts.Width, opts.Height, colorBackground)

	if len(candles) == 0 {
		return png.Encode(w, img)
	}
	proj := newProjection(candles, opts)

	for _, pool := range liq.Pools {
		drawPoolBand(img, proj, pool)
	}
	for i, c := range candles {
		drawCandle(img, proj, i, c)
	}
	for _, s := range struc.Swings {
		drawSwing(img, proj, candles, s)
	}
	for _, brk := range struc.Breaks {
		drawBreak(img, proj, candles, brk)
	}

	return png.Encode(w, img)
}

// projection maps a candle index and a price to pixel coordinates.
type projection struct {
	opts               Options
	n                  int
	minPrice, maxPrice float64
}

func newProjection(candles []market.Candle, opts Options) projection {
	minPrice, maxPrice := candles[0].Low, candles[0].High
	for _, c := range candles {
		if c.Low < minPrice {
			minPrice = c.Low
		}
		if c.High > maxPrice {
			maxPrice = c.High
		}
	}
	if maxPrice == minPrice {
		maxPrice += 1 // avoid a zero-height price range degenerating the projection
	}
	return projection{opts: opts, n: len(candles), minPrice: minPrice, maxPrice: maxPrice}
}

func (p projection) x(index int) int {
	usable := p.opts.Width - 2*p.opts.Margin
	if p.n <= 1 {
		return p.opts.Margin
	}
	return p.opts.Margin + index*usable/(p.n-1)
}

func (p projection) y(price float64) int {
	usable := p.opts.Height - 2*p.opts.Margin
	frac := (price - p.minPrice) / (p.maxPrice - p.minPrice)
	// Screen y grows downward; price grows upward — invert.
	return p.opts.Margin + int(float64(usable)*(1-frac))
}

func drawCandle(img *image.RGBA, proj projection, index int, c market.Candle) {
	col := colorBullish
	if c.Close < c.Open {
		col = colorBearish
	}
	x := proj.x(index)
	drawVLine(img, x, proj.y(c.High), proj.y(c.Low), colorWick)

	bodyTop, bodyBottom := c.Open, c.Close
	if bodyBottom < bodyTop {
		bodyTop, bodyBottom = bodyBottom, bodyTop
	}
	halfWidth := bodyHalfWidth(proj)
	fillRect(img, x-halfWidth, proj.y(bodyTop), x+halfWidth, proj.y(bodyBottom), col)
}

func bodyHalfWidth(proj projection) int {
	if proj.n <= 1 {
		return 3
	}
	w := (proj.opts.Width - 2*proj.opts.Margin) / proj.n / 3
	if w < 1 {
		w = 1
	}
	return w
}

func drawPoolBand(img *image.RGBA, proj projection, pool liquidity.Pool) {
	col := colorPoolBuy
	if pool.Side == liquidity.LiquiditySellSide {
		col = colorPoolSell
	}
	yTop, yBottom := proj.y(float64(pool.High)), proj.y(float64(pool.Low))
	fillRect(img, proj.opts.Margin, yTop, proj.opts.Width-proj.opts.Margin, yBottom, col)
}

func drawSwing(img *image.RGBA, proj projection, candles []market.Candle, s structure.Swing) {
	index := indexOfTime(candles, s.Time)
	if index < 0 {
		return
	}
	col := colorSwingHigh
	if s.Kind == structure.SwingLow {
		col = colorSwingLow
	}
	size := 2 + int(s.Layer) // bigger marker for a higher layer
	fillRect(img, proj.x(index)-size, proj.y(float64(s.Price))-size, proj.x(index)+size, proj.y(float64(s.Price))+size, col)
}

func drawBreak(img *image.RGBA, proj projection, candles []market.Candle, brk structure.StructureBreak) {
	if brk.Event == structure.EventNone {
		return
	}
	index := indexOfTime(candles, brk.Time)
	if index < 0 {
		return
	}
	col := colorBOS
	if brk.Event == structure.EventCHoCH {
		col = colorCHoCH
	}
	x := proj.x(index)
	drawVLine(img, x, 0, proj.opts.Height, col)
}

func indexOfTime(candles []market.Candle, t int64) int {
	for i, c := range candles {
		if c.Time == t {
			return i
		}
	}
	return -1
}

func fillRect(img *image.RGBA, x0, y0, x1, y1 int, col color.Color) {
	if x0 > x1 {
		x0, x1 = x1, x0
	}
	if y0 > y1 {
		y0, y1 = y1, y0
	}
	bounds := img.Bounds()
	for y := y0; y <= y1; y++ {
		if y < bounds.Min.Y || y >= bounds.Max.Y {
			continue
		}
		for x := x0; x <= x1; x++ {
			if x < bounds.Min.X || x >= bounds.Max.X {
				continue
			}
			img.Set(x, y, col)
		}
	}
}

func drawVLine(img *image.RGBA, x, y0, y1 int, col color.Color) {
	if y0 > y1 {
		y0, y1 = y1, y0
	}
	bounds := img.Bounds()
	if x < bounds.Min.X || x >= bounds.Max.X {
		return
	}
	for y := y0; y <= y1; y++ {
		if y < bounds.Min.Y || y >= bounds.Max.Y {
			continue
		}
		img.Set(x, y, col)
	}
}
