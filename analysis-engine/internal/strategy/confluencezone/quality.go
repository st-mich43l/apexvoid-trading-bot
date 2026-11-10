package confluencezone

import (
	analysiscontext "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/context"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/quality"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategyutil"
)

// QualityModel is the version of the scoring rule below.
const QualityModel = "confluence_zone.quality.v2"

// scoreQuality is Confluence Zone's technical quality: independent structures overlapping at one price, and a reaction there.
// Seven dimensions in [0, 1], averaged without weights. Structures that share an origin are one source (the setup already groups
// them), so the same evidence is never counted twice.
//
//	source_breadth      independent source groups supporting the region: Ramp(n; 1, 4).
//	overlap_precision   the true overlap's width in ATR: Peak(w; 0.1, 0.3, 1.2, 3). A hair is noise; a wide overlap is a region.
//	structure_integrity the mean of the overlap's and the sources' integrity (both measured in [0, 1] as the share not yet violated).
//	rejection           the confirming candle: half Ramp(close strength; 0.5, 0.9), half Ramp(refusal wick; 0.2, 0.6).
//	entry_location      how far the latest close has run from the proximal edge against the trade, in ATR: Fall(d; 0.25, 1.5); deeper in the band is not a chase (v2).
//	structure           the direction of the market, graded (quality.StructureScore).
//	room                reward:risk to the opposing liquidity against the risk the trade is exposed to (the structural stop, or the execution floor when wider), limited
//	                    by the nearest valid opposing barrier, 1R scoring 0 and 3R scoring 1 (v2).
func scoreQuality(m *quality.Model, setup Setup, d *strategyutil.LegacyDetector, choch, htfAligned bool, reactionLookback int, room strategyutil.QualityRoom, pipSize float64) {
	buy := setup.Direction == market.Buy
	reference := setup.EntryHigh
	if !buy {
		reference = setup.EntryLow
	}
	m.Score("source_breadth", quality.Ramp(float64(len(setup.Region.Groups)), 1, 4))
	m.Score("overlap_precision", quality.Peak(setup.OverlapWidthATR, 0.1, 0.3, 1.2, 3))
	m.Score("structure_integrity", 0.5*quality.Clamp01(setup.OverlapIntegrity)+0.5*quality.Clamp01(setup.SourceIntegrity))
	m.Score("rejection", 0.5*quality.Ramp(setup.Reaction.CloseStrength, 0.5, 0.9)+0.5*quality.Ramp(setup.Reaction.WickRatio, 0.2, 0.6))
	strategyutil.ScoreEntryLocation(m, buy, d.Price, reference, setup.ATR)
	m.Score("structure", quality.StructureScore(buy, d.Frame.Structure, choch, htfAligned))
	strategyutil.ScoreRoom(m, room, pipSize)
}

// qualityRoom measures the room of the entry from the executable proximal edge. The target is the nearest opposing liquidity: observed structure, limited by the
// nearest valid opposing barrier, against the structural stop or the execution floor when that is wider.
func (s *Strategy) qualityRoom(ctx *analysiscontext.MarketContext, setup Setup) strategyutil.QualityRoom {
	reference := setup.EntryHigh
	if setup.Direction == market.Sell {
		reference = setup.EntryLow
	}
	return strategyutil.MeasureQualityRoom(ctx, setup.Direction == market.Buy, reference, setup.Stop, setup.Target, true,
		strategyutil.StopBudget{MinPips: s.cfg.ExecutionStopMinPips, PipSize: s.cfg.PipSize}, s.cfg.Barrier.RoomParams(s.cfg.PipSize, strategyutil.ScalpRoomATR(ctx, setup.ATR)), 0)
}
