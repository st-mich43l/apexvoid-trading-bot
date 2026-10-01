package confluence

import "math"

// The values below are the constants used by the Python detector's
// ConfluenceFactors scorer.  Keep the arithmetic here deliberately boring:
// this package is a technical-fact calculator, not an execution gate.
const (
	factorHTFWeight          = 4.0
	factorTouchWeight        = 1.0
	factorTouchCap           = 3
	factorWickWeight         = 3.0
	factorDisplacementWeight = 3.0
	factorSessionWeight      = 2.0
	factorStructureWeight    = 3.0
	factorCHoCHWeight        = 2.0
	factorFibWeight          = 2.5
	factorScoreMax           = factorHTFWeight + factorTouchCap*factorTouchWeight + factorWickWeight + factorDisplacementWeight + factorSessionWeight + factorStructureWeight + factorFibWeight
	zoneScoreMax             = 24.5
)

// Config is the versioned technical confluence contract.  It mirrors the
// Python analysis.confluence controls.  The engine owns these values; the
// consumer receives the resulting facts and must not recompute them.
type Config struct {
	ScoringVersion    string
	StarThreeRatio    float64
	StarTwoRatio      float64
	ZoneQualityWeight float64
	MADScoreWeight    float64
	FibonacciWeight   float64
}

// DefaultConfig is useful for isolated package tests. Production always
// supplies the resolved configuration through engine.LoadSettings.
func DefaultConfig() Config {
	return Config{
		ScoringVersion: "v1", StarThreeRatio: 0.585, StarTwoRatio: 0.390,
		ZoneQualityWeight: 4.0, MADScoreWeight: 2.0, FibonacciWeight: factorFibWeight,
	}
}

func normalizeConfig(cfg Config) Config {
	// Only the completely zero value receives package defaults. Once a caller
	// supplies a version/config object, zero weights remain deliberate valid
	// controls (Python uses max(0, weight) and does not silently replace them).
	if cfg == (Config{}) {
		return DefaultConfig()
	}
	return cfg
}

// Factors are named, independently observable technical facts.  Touches are
// intentionally retained as a count because Python caps them at three.
type Factors struct {
	HTFAligned          bool
	Touches             int
	WickRejection       bool
	DisplacementGrade   bool
	SessionContext      bool
	StructuralAgreement bool
	FibTouch            bool
	CHoCH               bool
}

// ZoneQuality is the source-zone score and lifecycle touch count used by the
// V1/V2 scorer. A zero Score is meaningful: Python then scores from factors.
type ZoneQuality struct {
	Score   float64
	Touches int
}

// Score is the complete Go-owned technical confluence result. SelectedStars
// is the configured version used by the historical Python detector; V1/V2
// and the raw components remain available for audit and replay.
type Score struct {
	Version          string
	SelectedStars    int
	V1Stars          int
	V2Stars          int
	V2Raw            float64
	RawFactorScore   float64
	ZoneQualityScore float64
	MADBonus         float64
	Factors          Factors
}

// Evaluate ports _confluence_from_zone, _confluence_v2_score and their star
// thresholds. It never rejects a candidate; eligibility remains strategy and
// execution-policy work outside this package.
func Evaluate(zone ZoneQuality, factors Factors, cfg Config, madBonus float64) Score {
	cfg = normalizeConfig(cfg)
	raw := rawFactorScore(factors, cfg.FibonacciWeight)
	v1 := starsFromRatio(raw/factorScoreMax, 8.0/factorScoreMax, 12.0/factorScoreMax)
	if zone.Score > 0 {
		zoneScore := zone.Score
		if factors.FibTouch {
			zoneScore += cfg.FibonacciWeight
		}
		v1 = starsFromRatio(zoneScore/zoneScoreMax, 8.0/zoneScoreMax, 12.0/zoneScoreMax)
	}
	if zone.Touches > 0 {
		if v1 > 2 {
			v1 = 2
		}
	}
	if v1 < 1 {
		v1 = 1
	}

	zoneQuality := cfg.ZoneQualityWeight * math.Min(1, math.Max(0, zone.Score)/zoneScoreMax)
	bonus := math.Max(0, madBonus)
	v2Raw := raw + zoneQuality + bonus*cfg.MADScoreWeight
	v2Max := factorScoreMax + cfg.ZoneQualityWeight + cfg.MADScoreWeight
	v2 := starsFromRatio(v2Raw/v2Max, cfg.StarTwoRatio, cfg.StarThreeRatio)
	selected := v1
	if cfg.ScoringVersion == "v2" {
		selected = v2
	}
	return Score{
		Version: cfg.ScoringVersion, SelectedStars: selected,
		V1Stars: v1, V2Stars: v2, V2Raw: v2Raw,
		RawFactorScore: raw, ZoneQualityScore: zoneQuality,
		MADBonus: bonus, Factors: factors,
	}
}

func rawFactorScore(f Factors, fibWeight float64) float64 {
	score := 0.0
	if f.HTFAligned {
		score += factorHTFWeight
	}
	touches := f.Touches
	if touches < 0 {
		touches = 0
	}
	if touches > factorTouchCap {
		touches = factorTouchCap
	}
	score += float64(touches) * factorTouchWeight
	if f.WickRejection {
		score += factorWickWeight
	}
	if f.DisplacementGrade {
		score += factorDisplacementWeight
	}
	if f.SessionContext {
		score += factorSessionWeight
	}
	if f.StructuralAgreement {
		score += factorStructureWeight
	}
	if f.CHoCH {
		score += factorCHoCHWeight
	}
	if f.FibTouch {
		score += math.Max(0, fibWeight)
	}
	return score
}

func starsFromRatio(ratio, two, three float64) int {
	if ratio >= three {
		return 3
	}
	if ratio >= two {
		return 2
	}
	return 1
}
