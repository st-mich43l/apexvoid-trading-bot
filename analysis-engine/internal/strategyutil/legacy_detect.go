package strategyutil

// The helpers in this file reproduce, over the detector-contract frame the
// engine computes (context.LegacyFrame), the shared decision steps of the
// frozen detectors: zone candidacy and ranking, proximal entry clipping, entry
// and level validity, the liquidity-grab lookups, the reaction confirmation
// call, the fibonacci touch and the confluence qualification (_finish). They
// own no setup thesis; each strategy package still decides its own setup.

import (
	"fmt"
	"math"
	"sort"
	"strings"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/confluence"
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/fib"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/reaction"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/techniquezone"
)

// legacyEpsilon is the detectors' own comparison slack (_EPS).
const legacyEpsilon = 1e-9

// LegacyDetectorSettings are the detector-contract thresholds. Every value is
// read from the strategy's parameters, which the engine fills from
// config/analysis.yml (instrument-scale values are applied per instrument).
type LegacyDetectorSettings struct {
	PipSize                  float64
	MaximumEntryATR          float64
	MaximumZoneWidthATR      float64
	ProximalBandATR          float64
	FVGEntryMaxWidthPrice    float64
	ConfluenceFloor          int
	Confluence               confluence.Config
	FibonacciEnabled         bool
	FibonacciEpsilonATR      float64
	ReactionLookbackBars     int
	EngulfingMinimumRangeATR float64
	// RetestMaxTouches is technique_retest_max_touches: how many touches a
	// technique zone may have taken and still publish (optional, default 30).
	RetestMaxTouches int
	// HigherTimeframes lists the higher timeframes whose supply/demand zones a
	// technique publisher also evaluates against the execution frame's closed
	// bars (optional "higher_timeframes", default none). The frozen Python
	// publishers read the execution frame only, so this is the one deliberate
	// extension of the contract and is opt-in per strategy.
	HigherTimeframes []market.Timeframe
}

// LegacyDetectorParameterKeys documents the parameters ParseLegacyDetectorSettings
// reads; the engine injects the instrument-scale and shared leaves.
var LegacyDetectorParameterKeys = []string{
	"pip_size", "maximum_entry_atr", "maximum_zone_width_atr", "proximal_band_atr", "fvg_entry_max_width_price",
	"confluence_floor", "confluence_scoring_version", "confluence_star_three_ratio", "confluence_star_two_ratio",
	"confluence_zone_quality_weight", "confluence_mad_score_weight", "fibonacci_confluence_weight",
	"fibonacci_enabled", "fibonacci_epsilon_atr", "reaction_lookback_bars", "engulfing_minimum_range_atr",
}

// ParseLegacyDetectorSettings reads the settings from a strategy's parameters.
func ParseLegacyDetectorSettings(params map[string]any) (LegacyDetectorSettings, error) {
	var s LegacyDetectorSettings
	floats := map[string]*float64{
		"pip_size": &s.PipSize, "maximum_entry_atr": &s.MaximumEntryATR, "maximum_zone_width_atr": &s.MaximumZoneWidthATR,
		"proximal_band_atr": &s.ProximalBandATR, "fvg_entry_max_width_price": &s.FVGEntryMaxWidthPrice,
		"confluence_star_three_ratio": &s.Confluence.StarThreeRatio, "confluence_star_two_ratio": &s.Confluence.StarTwoRatio,
		"confluence_zone_quality_weight": &s.Confluence.ZoneQualityWeight, "confluence_mad_score_weight": &s.Confluence.MADScoreWeight,
		"fibonacci_confluence_weight": &s.Confluence.FibonacciWeight, "fibonacci_epsilon_atr": &s.FibonacciEpsilonATR,
		"engulfing_minimum_range_atr": &s.EngulfingMinimumRangeATR,
	}
	for key, dst := range floats {
		value, err := Float(params, key)
		if err != nil {
			return s, err
		}
		*dst = value
	}
	var err error
	if s.ConfluenceFloor, err = Int(params, "confluence_floor"); err != nil {
		return s, err
	}
	if s.ReactionLookbackBars, err = Int(params, "reaction_lookback_bars"); err != nil {
		return s, err
	}
	s.RetestMaxTouches = 30
	if _, present := params["technique_retest_max_touches"]; present {
		if s.RetestMaxTouches, err = Int(params, "technique_retest_max_touches"); err != nil {
			return s, err
		}
	}
	if raw, present := params["higher_timeframes"]; present {
		if s.HigherTimeframes, err = parseHigherTimeframes(raw); err != nil {
			return s, err
		}
	}
	version, ok := params["confluence_scoring_version"].(string)
	if !ok || version == "" {
		return s, fmt.Errorf("parameter %q must be a string", "confluence_scoring_version")
	}
	s.Confluence.ScoringVersion = version
	if s.FibonacciEnabled, ok = params["fibonacci_enabled"].(bool); !ok {
		return s, fmt.Errorf("parameter %q must be boolean", "fibonacci_enabled")
	}
	if s.PipSize <= 0 || s.MaximumEntryATR <= 0 || s.ProximalBandATR < 0 || s.MaximumZoneWidthATR < 0 || s.FVGEntryMaxWidthPrice < 0 ||
		s.ConfluenceFloor < 1 || s.ReactionLookbackBars < 1 || s.EngulfingMinimumRangeATR < 0 || s.FibonacciEpsilonATR < 0 {
		return s, fmt.Errorf("invalid legacy detector parameters")
	}
	return s, nil
}

// parseHigherTimeframes reads the optional higher_timeframes list: distinct,
// recognised timeframes strictly above the execution timeframe (M5).
func parseHigherTimeframes(raw any) ([]market.Timeframe, error) {
	items, ok := raw.([]any)
	if !ok {
		if typed, isStrings := raw.([]string); isStrings {
			items = make([]any, len(typed))
			for i, item := range typed {
				items[i] = item
			}
		} else {
			return nil, fmt.Errorf("parameter %q must be a list of timeframes", "higher_timeframes")
		}
	}
	seen := map[market.Timeframe]bool{}
	var out []market.Timeframe
	for _, item := range items {
		name, isString := item.(string)
		tf := market.Timeframe(name)
		minutes, known := tf.Minutes()
		if !isString || !known || minutes <= 5 || seen[tf] {
			return nil, fmt.Errorf("parameter %q must list distinct timeframes above M5, got %v", "higher_timeframes", item)
		}
		seen[tf] = true
		out = append(out, tf)
	}
	return out, nil
}

// LegacyDetector is one detector evaluation's read-only inputs.
type LegacyDetector struct {
	Frame     *analysiscontext.LegacyFrame
	Read      analysiscontext.LegacyRead
	Direction market.Direction
	Price     float64
	ATR       float64
	Settings  LegacyDetectorSettings
}

// NewLegacyDetector binds the frame and the evaluated direction. The price is
// the latest closed bar's close, as the frozen scanner passes no spot price.
func NewLegacyDetector(ctx *analysiscontext.MarketContext, tf market.Timeframe, direction market.Direction, settings LegacyDetectorSettings) (*LegacyDetector, bool) {
	if ctx == nil || ctx.Legacy == nil {
		return nil, false
	}
	frame := ctx.Timeframes[tf]
	if frame == nil || frame.Legacy == nil || len(frame.Legacy.Bars) < 5 {
		return nil, false
	}
	bars := frame.Legacy.Bars
	atr := frame.Legacy.DetectorATR
	if !(atr > 0) {
		atr = 1
	}
	return &LegacyDetector{Frame: frame.Legacy, Read: *ctx.Legacy, Direction: direction, Price: bars[len(bars)-1].Close, ATR: atr, Settings: settings}, true
}

func (d *LegacyDetector) side() string {
	if d.Direction == market.Buy {
		return "demand"
	}
	return "supply"
}

// CandidateZones mirrors _candidate_zones: the scored zones and the order-block
// view on the trade's side, de-duplicated by band and source.
func (d *LegacyDetector) CandidateZones() []techniquezone.Zone {
	type key struct {
		low, high float64
		source    string
	}
	seen := map[key]bool{}
	var out []techniquezone.Zone
	for _, group := range [][]techniquezone.Zone{d.Frame.Zones, d.Frame.OrderBlocks} {
		for _, zone := range group {
			if zone.Side != d.side() {
				continue
			}
			k := key{math.Round(zone.Low()*1e6) / 1e6, math.Round(zone.High()*1e6) / 1e6, zone.Source}
			if seen[k] {
				continue
			}
			seen[k] = true
			out = append(out, zone)
		}
	}
	return out
}

// EntryValid mirrors _entry_valid_for_settings.
func (d *LegacyDetector) EntryValid(zone techniquezone.Zone) bool {
	maxDistance := math.Max(0, d.ATR) * math.Max(0, d.Settings.MaximumEntryATR)
	distance := 0.0
	if d.Direction == market.Sell {
		if d.Price > zone.High()+legacyEpsilon {
			return false
		}
		if !(zone.Low() <= d.Price && d.Price <= zone.High()) {
			distance = zone.Low() - d.Price
		}
	} else {
		if d.Price < zone.Low()-legacyEpsilon {
			return false
		}
		if !(zone.Low() <= d.Price && d.Price <= zone.High()) {
			distance = d.Price - zone.High()
		}
	}
	return distance <= maxDistance+legacyEpsilon
}

// LevelValid mirrors _level_valid.
func (d *LegacyDetector) LevelValid(level float64) bool {
	if d.Direction == market.Buy {
		return level <= d.Price+legacyEpsilon
	}
	return level >= d.Price-legacyEpsilon
}

// ZoneDistance mirrors _zone_distance.
func (d *LegacyDetector) ZoneDistance(zone techniquezone.Zone) float64 {
	if zone.Low() <= d.Price && d.Price <= zone.High() {
		return 0
	}
	if d.Direction == market.Buy {
		return math.Abs(d.Price - zone.High())
	}
	return math.Abs(zone.Low() - d.Price)
}

// ZoneKey mirrors _zone_key.
func (d *LegacyDetector) ZoneKey(zone techniquezone.Zone) float64 {
	if d.Direction == market.Buy {
		if zone.High() <= d.Price+legacyEpsilon {
			return zone.High()
		}
		return zone.Low()
	}
	if zone.Low() >= d.Price-legacyEpsilon {
		return zone.Low()
	}
	return zone.High()
}

// BestValidZone mirrors _best_valid_zone: the highest-scored entry-valid zone,
// nearest then lowest on ties, clipped to its proximal band when wide.
func (d *LegacyDetector) BestValidZone(zones []techniquezone.Zone) (techniquezone.Zone, bool, bool) {
	var valid []techniquezone.Zone
	for _, zone := range zones {
		if d.EntryValid(zone) {
			valid = append(valid, zone)
		}
	}
	if len(valid) == 0 {
		return techniquezone.Zone{}, false, false
	}
	best := valid[0]
	for _, zone := range valid[1:] {
		switch {
		case zone.Score != best.Score:
			if zone.Score > best.Score {
				best = zone
			}
		case d.ZoneDistance(zone) != d.ZoneDistance(best):
			if d.ZoneDistance(zone) < d.ZoneDistance(best) {
				best = zone
			}
		case zone.Low() < best.Low():
			best = zone
		}
	}
	zone, proximal := d.proximalIfWide(best)
	return zone, proximal, true
}

func (d *LegacyDetector) proximalIfWide(zone techniquezone.Zone) (techniquezone.Zone, bool) {
	zone, fvgClipped := OptimizeImbalanceEntryZone(zone, d.Direction, d.Settings.FVGEntryMaxWidthPrice, "")
	width := zone.High() - zone.Low()
	maxWidth := math.Max(0, d.Settings.MaximumZoneWidthATR) * math.Max(0, d.ATR)
	if maxWidth <= 0 || width <= maxWidth {
		return zone, fvgClipped
	}
	band := math.Max(legacyEpsilon, d.Settings.ProximalBandATR*math.Max(0, d.ATR))
	if d.Direction == market.Sell {
		zone.Top = math.Min(zone.High(), zone.Low()+band)
		return zone, true
	}
	zone.Bottom = math.Max(zone.Low(), zone.High()-band)
	return zone, true
}

// OptimizeImbalanceEntryZone mirrors technique_geometry.optimize_imbalance_entry_zone.
func OptimizeImbalanceEntryZone(zone techniquezone.Zone, direction market.Direction, maxWidthPrice float64, structuralKind string) (techniquezone.Zone, bool) {
	if !isFVGImbalance(zone, structuralKind) {
		return zone, false
	}
	low, high := zone.Low(), zone.High()
	if math.IsNaN(low) || math.IsNaN(high) || high <= low || math.IsNaN(maxWidthPrice) || maxWidthPrice <= 0 {
		return zone, false
	}
	if high-low <= maxWidthPrice+1e-12 {
		return zone, false
	}
	if direction == market.Sell || zone.Side == "supply" {
		zone.Bottom, zone.Top = low, low+maxWidthPrice
	} else {
		zone.Bottom, zone.Top = high-maxWidthPrice, high
	}
	return zone, true
}

func isFVGImbalance(zone techniquezone.Zone, structuralKind string) bool {
	tokens := []string{zone.Source}
	tokens = append(tokens, zone.Sources...)
	if structuralKind != "" {
		tokens = append(tokens, structuralKind)
	}
	for _, token := range tokens {
		text := strings.ToLower(strings.TrimSpace(token))
		text = strings.NewReplacer("-", "_", "+", "_").Replace(text)
		if text == "" {
			continue
		}
		switch text {
		case "fvg", "ifvg", "imbalance", "bullish_fvg", "bearish_fvg", "fvg_ifvg":
			return true
		}
		for _, part := range strings.Split(text, "_") {
			if part == "fvg" || part == "ifvg" || part == "imbalance" {
				return true
			}
		}
		if strings.HasSuffix(text, "_fvg") {
			return true
		}
	}
	return false
}

// NearestLevel mirrors _nearest_level over the frame's key levels.
func (d *LegacyDetector) NearestLevel() (techniquezone.Level, bool) {
	var best techniquezone.Level
	found := false
	for _, level := range d.Frame.Levels {
		if d.Direction == market.Buy && level.Price > d.Price+legacyEpsilon || d.Direction == market.Sell && level.Price < d.Price-legacyEpsilon {
			continue
		}
		if !found || math.Abs(level.Price-d.Price) < math.Abs(best.Price-d.Price) {
			best, found = level, true
		}
	}
	return best, found
}

// LevelsByDistance returns the key levels ordered nearest first.
func (d *LegacyDetector) LevelsByDistance() []techniquezone.Level {
	levels := append([]techniquezone.Level(nil), d.Frame.Levels...)
	sort.SliceStable(levels, func(i, j int) bool {
		return math.Abs(levels[i].Price-d.Price) < math.Abs(levels[j].Price-d.Price)
	})
	return levels
}

// EntryZone mirrors structure.entry_zone for this frame's bars.
func (d *LegacyDetector) EntryZone(level float64) techniquezone.Zone {
	return techniquezone.EntryZone(d.Frame.Bars, level, string(d.Direction), d.Settings.PipSize, d.Frame.Compat)
}

// ZoneGrab mirrors _zone_grab: the latest same-direction grab whose pool points
// into the zone.
func (d *LegacyDetector) ZoneGrab(zone techniquezone.Zone) *techniquezone.Grab {
	wantDirection, wantSide := d.grabKinds()
	for i := len(d.Frame.Grabs) - 1; i >= 0; i-- {
		grab := d.Frame.Grabs[i]
		if grab.Direction != wantDirection || grab.Pool.Side != wantSide {
			continue
		}
		width := math.Max(zone.High()-zone.Low(), 0)
		tolerance := math.Max(grab.Pool.Band, math.Max(width, d.Settings.PipSize))
		if zone.Side == "demand" && grab.Pool.Side == "sell" && zone.Low()-tolerance <= grab.Pool.Level && grab.Pool.Level <= zone.High() ||
			zone.Side == "supply" && grab.Pool.Side == "buy" && zone.Low() <= grab.Pool.Level && grab.Pool.Level <= zone.High()+tolerance {
			return &grab
		}
	}
	return nil
}

// LevelGrab mirrors _level_grab.
func (d *LegacyDetector) LevelGrab(levelPrice, levelBand float64) *techniquezone.Grab {
	wantDirection, wantSide := d.grabKinds()
	for i := len(d.Frame.Grabs) - 1; i >= 0; i-- {
		grab := d.Frame.Grabs[i]
		if grab.Direction != wantDirection || grab.Pool.Side != wantSide {
			continue
		}
		if math.Abs(grab.Pool.Level-levelPrice) <= math.Max(grab.Pool.Band, math.Max(levelBand, legacyEpsilon)) {
			return &grab
		}
	}
	return nil
}

func (d *LegacyDetector) grabKinds() (direction, side string) {
	if d.Direction == market.Buy {
		return "bull", "sell"
	}
	return "bear", "buy"
}

// RecentCHoCH mirrors _recent_choch_flag.
func (d *LegacyDetector) RecentCHoCH(lookback int) bool {
	earliest := maxInt(0, len(d.Frame.Bars)-maxInt(1, lookback)-1)
	wanted := "down"
	if d.Direction == market.Buy {
		wanted = "up"
	}
	for _, brk := range d.Frame.Breaks {
		if brk.Kind == "CHoCH" && brk.Direction == wanted && brk.Index >= earliest {
			return true
		}
	}
	return false
}

// LegacyConfirmation is a confirmed reaction on the frame's bars.
type LegacyConfirmation struct {
	Type              string
	TouchIndex        int
	ConfirmationIndex int
	TouchTime         int64
	ConfirmationTime  int64
}

// Reaction mirrors evaluate_structural_reaction over the frame's bars with the
// optional grab the caller found; the touch and confirmation windows are the
// detector's reaction lookback.
func (d *LegacyDetector) Reaction(low, high float64, grab *techniquezone.Grab) *LegacyConfirmation {
	lookback := maxInt(1, d.Settings.ReactionLookbackBars)
	return d.ReactionWithLookbacks(low, high, grab, lookback, lookback)
}

// ReactionWithLookbacks is Reaction with separate touch and confirmation
// windows; the CHoCH flag always uses the confirmation window.
func (d *LegacyDetector) ReactionWithLookbacks(low, high float64, grab *techniquezone.Grab, touchLookback, confirmationLookback int) *LegacyConfirmation {
	params := reaction.Params{
		Direction: string(d.Direction), Low: low, High: high, TouchLookback: maxInt(1, touchLookback), ConfirmLookback: maxInt(1, confirmationLookback),
		HasCHoCH: d.RecentCHoCH(confirmationLookback), ATR: d.ATR, EngulfingMinimumRangeATR: d.Settings.EngulfingMinimumRangeATR,
	}
	if grab != nil {
		params.Grabs = []reaction.Grab{{Index: grab.Index, Grade: grab.Grade}}
	}
	confirmation := reaction.Evaluate(d.Frame.Bars, params)
	if confirmation == nil {
		return nil
	}
	return &LegacyConfirmation{
		Type: confirmation.Type, TouchIndex: confirmation.TouchIndex, ConfirmationIndex: confirmation.ConfirmationIndex,
		TouchTime: d.Frame.Bars[confirmation.TouchIndex].Time, ConfirmationTime: d.Frame.Bars[confirmation.ConfirmationIndex].Time,
	}
}

// LegacyResult is a detector decision that passed the shared qualification.
type LegacyResult struct {
	Direction      market.Direction
	Level          float64
	Zone           techniquezone.Zone // entry zone after proximal clipping
	StructuralLow  float64
	StructuralHigh float64
	Confluence     confluence.Score
	Stars          int
	FibTouch       bool
}

// FactorsForConfirmation mirrors _factors_for_confirmation: a CHoCH
// confirmation adds the structural-agreement and choch factors.
func FactorsForConfirmation(factors confluence.Factors, confirmationType string) confluence.Factors {
	if confirmationType != reaction.TypeRejectionCHoCH {
		return factors
	}
	factors.StructuralAgreement, factors.CHoCH = true, true
	return factors
}

// HTFAligned reports the htf_aligned factor for the evaluated direction.
func (d *LegacyDetector) HTFAligned() bool { return d.Read.AlignedWithHTF(d.Direction) }

// Finish mirrors _finish: proximal clipping, level and entry validity, the
// fibonacci touch, the confluence stars and the confluence floor. It returns
// nil when the frozen detector would have returned None.
func (d *LegacyDetector) Finish(level float64, zone techniquezone.Zone, factors confluence.Factors, structuralKind string, structuralLow, structuralHigh *float64) *LegacyResult {
	rawLow, rawHigh := zone.Low(), zone.High()
	if structuralLow != nil {
		rawLow = *structuralLow
	}
	if structuralHigh != nil {
		rawHigh = *structuralHigh
	}
	clipped, wasClipped := OptimizeImbalanceEntryZone(zone, d.Direction, d.Settings.FVGEntryMaxWidthPrice, structuralKind)
	zone = clipped
	outLow, outHigh := zone.Low(), zone.High()
	if wasClipped {
		outLow, outHigh = rawLow, rawHigh
	} else if structuralLow != nil && structuralHigh != nil {
		outLow, outHigh = *structuralLow, *structuralHigh
	}
	if !d.LevelValid(level) || !d.EntryValid(zone) {
		return nil
	}
	fibTouch := d.fibTouch(level)
	if fibTouch {
		factors.FibTouch = true
	}
	score := confluence.Evaluate(confluence.ZoneQuality{Score: zone.Score, Touches: zone.Touches}, factors, d.Settings.Confluence, 0)
	if score.SelectedStars < d.Settings.ConfluenceFloor {
		return nil
	}
	return &LegacyResult{Direction: d.Direction, Level: level, Zone: zone, StructuralLow: outLow, StructuralHigh: outHigh, Confluence: score, Stars: score.SelectedStars, FibTouch: fibTouch}
}

// fibTouch mirrors _resolve_fib_touch.
func (d *LegacyDetector) fibTouch(level float64) bool {
	if !d.Settings.FibonacciEnabled || d.ATR <= 0 {
		return false
	}
	ladder := d.Frame.FibLadder
	if len(ladder) == 0 {
		swings := make([]structure.Swing, 0, len(d.Frame.Swings))
		for _, swing := range d.Frame.Swings {
			kind := structure.SwingHigh
			if swing.Kind == "low" {
				kind = structure.SwingLow
			}
			swings = append(swings, structure.Swing{Kind: kind, Price: market.Price(swing.Price)})
		}
		ladder = fib.LadderForPrice(swings, level)
	}
	_, ok := fib.NearestLevel(ladder, market.Price(level), market.Price(d.ATR), d.Settings.FibonacciEpsilonATR)
	return ok
}

// StrongBodyBreak mirrors _strong_body_break on the frame's latest bar.
func (d *LegacyDetector) StrongBodyBreak(bodyFraction float64) bool {
	bars := d.Frame.Bars
	if len(bars) == 0 {
		return false
	}
	bar := bars[len(bars)-1]
	span := bar.High - bar.Low
	if span <= 0 {
		return false
	}
	bodyOK := math.Abs(bar.Close-bar.Open) >= math.Max(0, bodyFraction)*span
	directionOK := bar.Close < bar.Open
	if d.Direction == market.Buy {
		directionOK = bar.Close > bar.Open
	}
	if !bodyOK || !directionOK {
		return false
	}
	kind := "low"
	if d.Direction == market.Buy {
		kind = "high"
	}
	last, found := 0.0, false
	for _, swing := range d.Frame.Swings {
		if swing.Kind == kind {
			last, found = swing.Price, true
		}
	}
	if !found {
		return true
	}
	if d.Direction == market.Buy {
		return bar.Close > last
	}
	return bar.Close < last
}

// Rejection mirrors _rejection on the frame's latest bar.
func (d *LegacyDetector) Rejection() bool {
	bars := d.Frame.Bars
	if len(bars) == 0 {
		return false
	}
	bar := bars[len(bars)-1]
	span := bar.High - bar.Low
	if span <= 0 {
		return false
	}
	body := math.Abs(bar.Close - bar.Open)
	upper := bar.High - math.Max(bar.Open, bar.Close)
	lower := math.Min(bar.Open, bar.Close) - bar.Low
	lowerThird := bar.Low + span/3
	upperThird := bar.High - span/3
	if d.Direction == market.Sell {
		return upper >= body && bar.Close < bar.Open && bar.Close <= lowerThird
	}
	return lower >= body && bar.Close > bar.Open && bar.Close >= upperThird
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// ConfluenceContext converts a qualified detector result into the
// opportunity model's confluence, the authoritative value for the candidate.
func (r *LegacyResult) ConfluenceContext() *opportunity.ConfluenceContext {
	score := r.Confluence
	return &opportunity.ConfluenceContext{
		Version: score.Version, SelectedStars: score.SelectedStars, V1Stars: score.V1Stars, V2Stars: score.V2Stars,
		V2Raw: score.V2Raw, RawFactorScore: score.RawFactorScore, ZoneQualityScore: score.ZoneQualityScore, MADBonus: score.MADBonus,
		Factors: opportunity.ConfluenceFactors{
			HTFAligned: score.Factors.HTFAligned, Touches: score.Factors.Touches, WickRejection: score.Factors.WickRejection,
			DisplacementGrade: score.Factors.DisplacementGrade, SessionContext: score.Factors.SessionContext,
			StructuralAgreement: score.Factors.StructuralAgreement, FibTouch: score.Factors.FibTouch, CHoCH: score.Factors.CHoCH,
		},
	}
}

// ZoneID is the stable identity of a frame zone: its side, source and origin
// bar. The band is deliberately excluded because a merged zone's band can move
// as members join, while the thesis it names does not.
func (d *LegacyDetector) ZoneID(zone techniquezone.Zone) string {
	origin := int64(0)
	if zone.OriginIndex >= 0 && zone.OriginIndex < len(d.Frame.Bars) {
		origin = d.Frame.Bars[zone.OriginIndex].Time
	}
	return fmt.Sprintf("zone:%s:%s:%d", zone.Side, zone.Source, origin)
}

// LevelID is the stable identity of a key level: its kind and price.
func LevelID(level techniquezone.Level) string {
	return fmt.Sprintf("level:%s:%.8f", level.Kind, level.Price)
}

// OriginTime is the open time of the zone's origin bar, or fallback when the
// zone has no origin inside the frame's window.
func (d *LegacyDetector) OriginTime(zone techniquezone.Zone, fallback int64) int64 {
	if zone.OriginIndex >= 0 && zone.OriginIndex < len(d.Frame.Bars) {
		return d.Frame.Bars[zone.OriginIndex].Time
	}
	return fallback
}

// WithDirection returns a copy of the detector evaluated for another direction.
func (d *LegacyDetector) WithDirection(direction market.Direction) *LegacyDetector {
	copy := *d
	copy.Direction = direction
	return &copy
}

// NewLegacyDetectorForFrame binds the frame without a direction, for detectors
// (range edge) that evaluate both sides of one structure.
func NewLegacyDetectorForFrame(ctx *analysiscontext.MarketContext, tf market.Timeframe, settings LegacyDetectorSettings) (*LegacyDetector, bool) {
	return NewLegacyDetector(ctx, tf, "", settings)
}
