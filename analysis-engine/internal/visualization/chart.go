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
	"fmt"
	"image"
	"image/color"
	"image/png"
	"io"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/zone"
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
	colorDemandZone = color.RGBA{110, 200, 170, 110}
	colorSupplyZone = color.RGBA{245, 170, 100, 110}
	colorProtected  = color.RGBA{85, 85, 185, 255}
	colorEntry      = color.RGBA{35, 110, 210, 145}
	colorStop       = color.RGBA{220, 40, 50, 255}
	colorTarget     = color.RGBA{20, 145, 70, 255}
)

// Render draws candles plus struct's swings/breaks and liq's pools onto a
// PNG image and writes it to w. Render never computes anything analytical
// — every input is already-produced state (source task §46).
func Render(candles []market.Candle, struc structure.StructureState, liq liquidity.LiquidityState, opts Options, w io.Writer) error {
	return render(candles, struc, liq, nil, nil, opts, w)
}

// RenderSnapshot draws a canonical AnalysisSnapshot for one timeframe. It is
// intentionally a pure consumer of already-computed state: zones, protected
// levels, and opportunity geometry are drawn exactly as supplied and are never
// recalculated by visualization. candidate may be nil when research wants a
// general market-state image rather than one setup-focused image.
func RenderSnapshot(candles []market.Candle, snapshot engine.AnalysisSnapshot, timeframe market.Timeframe, candidate *opportunity.Candidate, opts Options, w io.Writer) error {
	struc, structureOK := snapshot.Structure[timeframe]
	liq, liquidityOK := snapshot.Liquidity[timeframe]
	if !structureOK || !liquidityOK {
		return fmt.Errorf("visualization: snapshot has no structure/liquidity state for %s", timeframe)
	}
	if candidate != nil && candidate.Symbol != snapshot.Symbol {
		return fmt.Errorf("visualization: opportunity %q belongs to %s, snapshot belongs to %s", candidate.ID, candidate.Symbol, snapshot.Symbol)
	}
	zoneState := snapshot.Zones[timeframe]
	return render(candles, struc, liq, zoneState.Zones, candidate, opts, w)
}

func render(candles []market.Candle, struc structure.StructureState, liq liquidity.LiquidityState, zones []zone.Zone, candidate *opportunity.Candidate, opts Options, w io.Writer) error {
	img := image.NewRGBA(image.Rect(0, 0, opts.Width, opts.Height))
	fillRect(img, 0, 0, opts.Width, opts.Height, colorBackground)

	if len(candles) == 0 {
		return png.Encode(w, img)
	}
	proj := newProjection(candles, opts, overlayPrices(struc, zones, candidate)...)

	for _, z := range zones {
		drawZoneBand(img, proj, z)
	}
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
	drawProtectedLevels(img, proj, struc)
	if candidate != nil {
		drawCandidate(img, proj, *candidate)
	}

	return png.Encode(w, img)
}

// projection maps a candle index and a price to pixel coordinates.
type projection struct {
	opts               Options
	n                  int
	minPrice, maxPrice float64
}

func newProjection(candles []market.Candle, opts Options, overlayPrices ...float64) projection {
	minPrice, maxPrice := candles[0].Low, candles[0].High
	for _, c := range candles {
		if c.Low < minPrice {
			minPrice = c.Low
		}
		if c.High > maxPrice {
			maxPrice = c.High
		}
	}
	for _, price := range overlayPrices {
		if price < minPrice {
			minPrice = price
		}
		if price > maxPrice {
			maxPrice = price
		}
	}
	if maxPrice == minPrice {
		maxPrice += 1 // avoid a zero-height price range degenerating the projection
	}
	return projection{opts: opts, n: len(candles), minPrice: minPrice, maxPrice: maxPrice}
}

func overlayPrices(struc structure.StructureState, zones []zone.Zone, candidate *opportunity.Candidate) []float64 {
	prices := make([]float64, 0, len(zones)*2+12)
	for _, z := range zones {
		prices = append(prices, float64(z.Low), float64(z.High))
	}
	for _, layer := range []structure.LayerState{struc.Micro, struc.Internal, struc.Intermediate, struc.Major} {
		if layer.ProtectedHigh != nil {
			prices = append(prices, float64(layer.ProtectedHigh.Price))
		}
		if layer.ProtectedLow != nil {
			prices = append(prices, float64(layer.ProtectedLow.Price))
		}
	}
	if candidate != nil {
		prices = append(prices, candidate.Entry.Low, candidate.Entry.High, float64(candidate.Invalidation.Price))
		for _, target := range candidate.Targets {
			prices = append(prices, float64(target.Price.Price))
		}
	}
	return prices
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

func drawZoneBand(img *image.RGBA, proj projection, z zone.Zone) {
	col := colorDemandZone
	if z.Side == zone.Supply {
		col = colorSupplyZone
	}
	yTop, yBottom := proj.y(float64(z.High)), proj.y(float64(z.Low))
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

func drawProtectedLevels(img *image.RGBA, proj projection, struc structure.StructureState) {
	for _, layer := range []structure.LayerState{struc.Micro, struc.Internal, struc.Intermediate, struc.Major} {
		if layer.ProtectedHigh != nil {
			drawHLine(img, proj, float64(layer.ProtectedHigh.Price), colorProtected)
		}
		if layer.ProtectedLow != nil {
			drawHLine(img, proj, float64(layer.ProtectedLow.Price), colorProtected)
		}
	}
}

func drawCandidate(img *image.RGBA, proj projection, candidate opportunity.Candidate) {
	yTop, yBottom := proj.y(candidate.Entry.High), proj.y(candidate.Entry.Low)
	fillRect(img, proj.opts.Margin, yTop, proj.opts.Width-proj.opts.Margin, yBottom, colorEntry)
	drawHLine(img, proj, float64(candidate.Invalidation.Price), colorStop)
	for _, target := range candidate.Targets {
		drawHLine(img, proj, float64(target.Price.Price), colorTarget)
	}
}

func indexOfTime(candles []market.Candle, t int64) int {
	for i, c := range candles {
		if c.Time == t {
			return i
		}
	}
	return -1
}

// fillRect alpha-composites translucent analytical areas onto the existing
// image. Snapshot overlays commonly overlap; replacing pixels would make the
// final zone drawn opaque and hide both candles and earlier facts.
func fillRect(img *image.RGBA, x0, y0, x1, y1 int, col color.RGBA) {
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
			img.SetRGBA(x, y, over(img.RGBAAt(x, y), col))
		}
	}
}

func over(dst, src color.RGBA) color.RGBA {
	if src.A == 255 {
		return src
	}
	alpha := uint32(src.A)
	inverse := 255 - alpha
	return color.RGBA{
		R: uint8((uint32(src.R)*alpha + uint32(dst.R)*inverse) / 255),
		G: uint8((uint32(src.G)*alpha + uint32(dst.G)*inverse) / 255),
		B: uint8((uint32(src.B)*alpha + uint32(dst.B)*inverse) / 255),
		A: uint8(alpha + uint32(dst.A)*inverse/255),
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

func drawHLine(img *image.RGBA, proj projection, price float64, col color.Color) {
	y := proj.y(price)
	for x := proj.opts.Margin; x < proj.opts.Width-proj.opts.Margin; x++ {
		if y >= 0 && y < proj.opts.Height {
			img.Set(x, y, col)
		}
	}
}
