package replaycapture_test

// Trendline on M5 and M15: two confirmed anchors are a line, the reaction at the second anchor trades on the close that confirms it, each timeframe decides on its
// own closed candles. The fixture is a real EURUSD window (production M15 and H1 closed bars, 2-3 Nov 2026): a falling resistance through the swing highs of
// 02 Nov 19:00 and 03 Nov 00:15 UTC, rejected at the second anchor (a wick rejection on the second anchor's own candle). The same prices are read as M5 bars for the M5 cases, and mirrored (price -> 2.24 - price) for
// the BUY cases: geometry in bars is the same on both frames (docs/strategies/trendline.md), so one real path serves both.

import (
	"encoding/json"
	"strconv"
	"strings"
	"testing"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/strategy"
)

const scenarioJSON = `{"m15":[[1793344500,1.11852,1.11898,1.11835,1.11895],[1793345400,1.11896,1.11903,1.11835,1.11837],[1793346300,1.11842,1.11913,1.11829,1.11902],[1793347200,1.11898,1.12007,1.11898,1.11955],[1793348100,1.11956,1.12015,1.11942,1.12004],[1793349000,1.12003,1.12011,1.11928,1.11936],[1793349900,1.11935,1.11981,1.11921,1.11947],[1793350800,1.11943,1.11957,1.11876,1.11941],[1793351700,1.11942,1.11973,1.1192,1.1196],[1793352600,1.11961,1.11982,1.1192,1.11954],[1793353500,1.11955,1.11997,1.11955,1.11987],[1793354400,1.11984,1.12049,1.11983,1.12025],[1793355300,1.12026,1.12044,1.12,1.12035],[1793356200,1.12031,1.12059,1.11995,1.12],[1793357100,1.12001,1.12028,1.11996,1.12012],[1793358000,1.12011,1.12032,1.11968,1.11999],[1793358900,1.11998,1.12001,1.11932,1.11935],[1793359800,1.11934,1.11987,1.11909,1.11979],[1793360700,1.11978,1.11982,1.11919,1.11956],[1793361600,1.11959,1.11968,1.119,1.11933],[1793362500,1.11934,1.11957,1.11883,1.11893],[1793363400,1.11892,1.11905,1.11757,1.11765],[1793364300,1.11766,1.11783,1.11709,1.11763],[1793365200,1.11762,1.11763,1.11667,1.11668],[1793366100,1.11669,1.11672,1.11619,1.11657],[1793367000,1.11659,1.11678,1.11546,1.11553],[1793367900,1.11552,1.11563,1.11483,1.11496],[1793368800,1.11494,1.11494,1.11365,1.11445],[1793369700,1.11444,1.11465,1.114,1.11447],[1793370600,1.11451,1.11597,1.11451,1.11571],[1793371500,1.11568,1.11573,1.11502,1.11558],[1793372400,1.11559,1.11568,1.11404,1.11427],[1793373300,1.11423,1.11556,1.11423,1.11545],[1793374200,1.11544,1.11644,1.11533,1.11591],[1793375100,1.11593,1.11665,1.11538,1.1163],[1793376000,1.11631,1.11728,1.11618,1.11704],[1793376900,1.11702,1.11726,1.11627,1.11629],[1793377800,1.11631,1.11637,1.11549,1.11555],[1793378700,1.11554,1.11593,1.11532,1.11576],[1793379600,1.11577,1.11599,1.11555,1.1158],[1793380500,1.11583,1.11588,1.11514,1.11525],[1793381400,1.11529,1.11546,1.11503,1.1152],[1793382300,1.11518,1.11534,1.11506,1.11516],[1793383200,1.11517,1.11589,1.11511,1.11575],[1793384100,1.11576,1.11644,1.1157,1.11642],[1793385000,1.1164,1.11717,1.11638,1.11679],[1793385900,1.1168,1.11701,1.11652,1.11669],[1793386800,1.11668,1.11688,1.11658,1.11669],[1793387700,1.11668,1.11705,1.11664,1.1167],[1793388600,1.11671,1.11683,1.11646,1.11664],[1793389500,1.11666,1.11689,1.11635,1.11678],[1793390400,1.11679,1.11698,1.11664,1.11685],[1793391300,1.11686,1.11693,1.11643,1.1168],[1793392200,1.11679,1.117,1.11631,1.11649],[1793393100,1.11648,1.1167,1.11575,1.11581],[1793570400,1.11523,1.11584,1.1149,1.11584],[1793571300,1.11584,1.11592,1.11485,1.11518],[1793572200,1.1151,1.11548,1.11503,1.11536],[1793573100,1.11533,1.11548,1.1151,1.11515],[1793574000,1.11548,1.1164,1.11522,1.11629],[1793574900,1.11628,1.11628,1.11594,1.11612],[1793575800,1.11614,1.11627,1.11577,1.1159],[1793576700,1.11592,1.11593,1.11554,1.11565],[1793577600,1.11566,1.11621,1.11562,1.11618],[1793578500,1.11617,1.11639,1.11604,1.11622],[1793579400,1.11617,1.11653,1.11605,1.11622],[1793580300,1.11618,1.11674,1.11608,1.11673],[1793581200,1.11672,1.11704,1.11658,1.11702],[1793582100,1.11704,1.11717,1.11689,1.11699],[1793583000,1.11702,1.11713,1.1167,1.11702],[1793583900,1.11701,1.11713,1.11691,1.11703],[1793584800,1.11702,1.11708,1.1167,1.11679],[1793585700,1.11677,1.11678,1.11635,1.11639],[1793586600,1.11638,1.11657,1.11627,1.11636],[1793587500,1.11635,1.11664,1.11631,1.1166],[1793588400,1.11661,1.11696,1.11661,1.11676],[1793589300,1.11677,1.11695,1.11653,1.11694],[1793590200,1.11695,1.11696,1.11675,1.11683],[1793591100,1.11681,1.11708,1.11681,1.11707],[1793592000,1.11708,1.11746,1.11708,1.11718],[1793592900,1.11719,1.11753,1.11719,1.11744],[1793593800,1.11743,1.11749,1.11721,1.11743],[1793594700,1.11742,1.11757,1.11731,1.11756],[1793595600,1.11757,1.11829,1.11753,1.11827],[1793596500,1.11828,1.11846,1.11817,1.11823],[1793597400,1.11822,1.11846,1.11813,1.11822],[1793598300,1.11823,1.11848,1.11813,1.11829],[1793599200,1.11828,1.11869,1.11821,1.1183],[1793600100,1.11829,1.11842,1.11784,1.11787],[1793601000,1.11786,1.11841,1.11784,1.11834],[1793601900,1.11835,1.11881,1.11827,1.11853],[1793602800,1.11859,1.11894,1.11851,1.11875],[1793603700,1.11874,1.11938,1.11864,1.11927],[1793604600,1.11928,1.11963,1.11896,1.11942],[1793605500,1.11941,1.11979,1.11916,1.11969],[1793606400,1.11971,1.12019,1.11932,1.12018],[1793607300,1.12015,1.12039,1.11972,1.12038],[1793608200,1.12039,1.12174,1.12037,1.1217],[1793609100,1.12166,1.12247,1.12164,1.12222],[1793610000,1.12225,1.12369,1.12224,1.12368],[1793610900,1.1236,1.1237,1.12264,1.12278],[1793611800,1.12279,1.12279,1.12172,1.12174],[1793612700,1.12175,1.12222,1.12174,1.12181],[1793613600,1.12179,1.12207,1.12158,1.12168],[1793614500,1.12169,1.12184,1.12108,1.12114],[1793615400,1.12115,1.12115,1.1206,1.12064],[1793616300,1.12065,1.12078,1.12029,1.1203],[1793617200,1.12031,1.12104,1.1202,1.12033],[1793618100,1.12035,1.12115,1.12026,1.12107],[1793619000,1.12108,1.12126,1.1206,1.12092],[1793619900,1.12091,1.12103,1.12053,1.12068],[1793620800,1.12069,1.12109,1.12059,1.12088],[1793621700,1.12086,1.12164,1.12074,1.12159],[1793622600,1.12158,1.12211,1.12133,1.12186],[1793623500,1.12184,1.122,1.121,1.12117],[1793624400,1.12118,1.12176,1.12117,1.12174],[1793625300,1.12175,1.12177,1.12105,1.12141],[1793626200,1.12139,1.12297,1.12125,1.12234],[1793627100,1.12235,1.12241,1.12142,1.12142],[1793628000,1.12145,1.12184,1.1211,1.12112],[1793628900,1.12115,1.12187,1.12114,1.12184],[1793629800,1.1219,1.12223,1.12076,1.12092],[1793630700,1.12093,1.12177,1.12083,1.1213],[1793631600,1.12115,1.12139,1.12044,1.12048],[1793632500,1.12049,1.12102,1.12033,1.12033],[1793633400,1.12032,1.12036,1.11956,1.12014],[1793634300,1.12016,1.12021,1.1196,1.11968],[1793635200,1.1197,1.11988,1.119,1.11946],[1793636100,1.11947,1.12021,1.11947,1.12003],[1793637000,1.12001,1.12015,1.11971,1.1199],[1793637900,1.11991,1.12038,1.1196,1.12032],[1793638800,1.1204,1.12086,1.12032,1.12054],[1793639700,1.12056,1.12089,1.12048,1.12062],[1793640600,1.12061,1.12072,1.12033,1.12038],[1793641500,1.12039,1.12091,1.12015,1.1205],[1793642400,1.12051,1.12066,1.12012,1.12016],[1793643300,1.12017,1.12017,1.11919,1.11927],[1793644200,1.11926,1.11963,1.1192,1.11943],[1793645100,1.11944,1.11967,1.11932,1.11965],[1793646000,1.11966,1.12034,1.11951,1.12024],[1793646900,1.12023,1.12027,1.11968,1.11999],[1793647800,1.11997,1.12005,1.11937,1.11938],[1793648700,1.11939,1.1195,1.11906,1.11944],[1793649600,1.11945,1.12016,1.11931,1.11933],[1793650500,1.11929,1.11943,1.11905,1.11933],[1793651400,1.11932,1.11968,1.1193,1.11953],[1793652300,1.11954,1.11967,1.11932,1.11934],[1793653200,1.11933,1.11933,1.11905,1.11914],[1793654100,1.11915,1.11923,1.11893,1.11903],[1793655000,1.11904,1.11914,1.11894,1.1191],[1793655900,1.11914,1.11915,1.11872,1.11874],[1793656800,1.11866,1.11887,1.11798,1.11887],[1793657700,1.11883,1.11888,1.11877,1.11881],[1793658600,1.11882,1.11885,1.11871,1.11873],[1793659500,1.11875,1.11877,1.11855,1.11877],[1793660400,1.11877,1.11885,1.11846,1.11855],[1793661300,1.11856,1.11878,1.1185,1.11878],[1793662200,1.11877,1.11886,1.11876,1.11884],[1793663100,1.11883,1.11925,1.11879,1.11919],[1793664000,1.1192,1.11955,1.11918,1.11955],[1793664900,1.11954,1.11977,1.11947,1.11951],[1793665800,1.11952,1.11966,1.11839,1.11854],[1793666700,1.11857,1.11906,1.11857,1.11884],[1793667600,1.11886,1.11889,1.1184,1.11852],[1793668500,1.11851,1.11893,1.1185,1.11872],[1793669400,1.11876,1.11892,1.11851,1.11866]],"h1":[[1793131200,1.11365,1.11367,1.11222,1.11257],[1793134800,1.11236,1.11298,1.11198,1.11257],[1793138400,1.11258,1.11311,1.11258,1.11304],[1793142000,1.11303,1.11307,1.11266,1.11266],[1793145600,1.11267,1.11322,1.11243,1.11254],[1793149200,1.11252,1.11268,1.11202,1.1124],[1793152800,1.11241,1.11278,1.11215,1.11261],[1793156400,1.11263,1.11271,1.1122,1.11235],[1793160000,1.11236,1.11236,1.112,1.11215],[1793163600,1.11214,1.11221,1.1117,1.11187],[1793167200,1.11186,1.11224,1.11151,1.11155],[1793170800,1.11153,1.11234,1.11131,1.11227],[1793174400,1.11225,1.11282,1.11198,1.11246],[1793178000,1.11247,1.11296,1.11215,1.11271],[1793181600,1.11272,1.11297,1.11226,1.11233],[1793185200,1.11232,1.11314,1.11209,1.11305],[1793188800,1.11304,1.1137,1.11294,1.11345],[1793192400,1.11346,1.1139,1.11306,1.11342],[1793196000,1.11346,1.11364,1.11242,1.11353],[1793199600,1.1135,1.114,1.11316,1.11395],[1793203200,1.11396,1.11529,1.11384,1.11481],[1793206800,1.11482,1.11495,1.1135,1.11377],[1793210400,1.11375,1.11695,1.11225,1.11508],[1793214000,1.11507,1.1165,1.11489,1.11582],[1793217600,1.11583,1.1166,1.11517,1.11533],[1793221200,1.11523,1.11557,1.11506,1.11524],[1793224800,1.11535,1.11565,1.11489,1.11511],[1793228400,1.11513,1.11609,1.11513,1.11556],[1793232000,1.11559,1.1156,1.11339,1.11397],[1793235600,1.11396,1.11554,1.11351,1.11496],[1793239200,1.11492,1.11583,1.11485,1.11488],[1793242800,1.11487,1.11517,1.11443,1.11446],[1793246400,1.11447,1.1149,1.1141,1.1144],[1793250000,1.11441,1.11449,1.11354,1.11367],[1793253600,1.11368,1.11404,1.1131,1.11384],[1793257200,1.11385,1.11476,1.11337,1.1144],[1793260800,1.11442,1.11509,1.11311,1.11389],[1793264400,1.11391,1.11412,1.11254,1.11343],[1793268000,1.11344,1.11503,1.11334,1.11486],[1793271600,1.11487,1.1152,1.11396,1.1142],[1793275200,1.11419,1.11499,1.11272,1.11281],[1793278800,1.11278,1.11375,1.11047,1.11355],[1793282400,1.11356,1.11675,1.11273,1.1161],[1793286000,1.11609,1.11737,1.11561,1.11639],[1793289600,1.11634,1.11699,1.11583,1.11636],[1793293200,1.11635,1.11728,1.11633,1.11693],[1793296800,1.11692,1.11714,1.11608,1.11617],[1793300400,1.11622,1.11692,1.11612,1.11621],[1793304000,1.11624,1.11856,1.11612,1.11807],[1793307600,1.11756,1.11794,1.11704,1.11763],[1793311200,1.11763,1.11921,1.11763,1.11911],[1793314800,1.11912,1.11921,1.11817,1.11825],[1793318400,1.11827,1.11978,1.11795,1.11977],[1793322000,1.11976,1.12148,1.11936,1.11943],[1793325600,1.11942,1.11998,1.11925,1.11964],[1793329200,1.11963,1.12029,1.11879,1.1191],[1793332800,1.11913,1.11931,1.11853,1.11867],[1793336400,1.11868,1.11878,1.11809,1.11847],[1793340000,1.11848,1.11904,1.11761,1.11799],[1793343600,1.11798,1.11913,1.11792,1.11902],[1793347200,1.11898,1.12015,1.11898,1.11947],[1793350800,1.11943,1.11997,1.11876,1.11987],[1793354400,1.11984,1.12059,1.11983,1.12012],[1793358000,1.12011,1.12032,1.11909,1.11956],[1793361600,1.11959,1.11968,1.11709,1.11763],[1793365200,1.11762,1.11763,1.11483,1.11496],[1793368800,1.11494,1.11597,1.11365,1.11558],[1793372400,1.11559,1.11665,1.11404,1.1163],[1793376000,1.11631,1.11728,1.11532,1.11576],[1793379600,1.11577,1.11599,1.11503,1.11516],[1793383200,1.11517,1.11717,1.11511,1.11669],[1793386800,1.11668,1.11705,1.11635,1.11678],[1793390400,1.11679,1.117,1.11575,1.11581],[1793570400,1.11523,1.11592,1.11485,1.11515],[1793574000,1.11548,1.1164,1.11522,1.11565],[1793577600,1.11566,1.11674,1.11562,1.11673],[1793581200,1.11672,1.11717,1.11658,1.11703],[1793584800,1.11702,1.11708,1.11627,1.1166],[1793588400,1.11661,1.11708,1.11653,1.11707],[1793592000,1.11708,1.11757,1.11708,1.11756],[1793595600,1.11757,1.11848,1.11753,1.11829],[1793599200,1.11828,1.11881,1.11784,1.11853],[1793602800,1.11859,1.11979,1.11851,1.11969],[1793606400,1.11971,1.12247,1.11932,1.12222],[1793610000,1.12225,1.1237,1.12172,1.12181],[1793613600,1.12179,1.12207,1.12029,1.1203],[1793617200,1.12031,1.12126,1.1202,1.12068],[1793620800,1.12069,1.12211,1.12059,1.12117],[1793624400,1.12118,1.12297,1.12105,1.12142],[1793628000,1.12145,1.12223,1.12076,1.1213],[1793631600,1.12115,1.12139,1.11956,1.11968],[1793635200,1.1197,1.12038,1.119,1.12032],[1793638800,1.1204,1.12091,1.12015,1.1205],[1793642400,1.12051,1.12066,1.11919,1.11965],[1793646000,1.11966,1.12034,1.11906,1.11944],[1793649600,1.11945,1.12016,1.11905,1.11934],[1793653200,1.11933,1.11933,1.11872,1.11874],[1793656800,1.11866,1.11888,1.11798,1.11877],[1793660400,1.11877,1.11925,1.11846,1.11919],[1793664000,1.1192,1.11977,1.11839,1.11884],[1793667600,1.11886,1.11893,1.11837,1.11858]],"b":1793664900,"a":1793646000}`

type scenario struct {
	M15, H1 [][]float64
	A, B    int64
}

func load(t *testing.T) scenario {
	t.Helper()
	var s struct {
		M15 [][]float64 `json:"m15"`
		H1  [][]float64 `json:"h1"`
		A   int64       `json:"a"`
		B   int64       `json:"b"`
	}
	if err := json.Unmarshal([]byte(scenarioJSON), &s); err != nil {
		t.Fatal(err)
	}
	return scenario{M15: s.M15, H1: s.H1, A: s.A, B: s.B}
}

// mirror reflects prices about 1.12 (a falling line becomes a rising one, a resistance a support).
func mirror(rows [][]float64) [][]float64 {
	out := make([][]float64, len(rows))
	for i, r := range rows {
		out[i] = []float64{r[0], 2.24 - r[1], 2.24 - r[3], 2.24 - r[2], 2.24 - r[4]}
	}
	return out
}

// reread keeps the prices and gives the bars a new timeline: start, start+step, ... (an M15 path read as M5 bars).
func reread(rows [][]float64, start, step int64) [][]float64 {
	out := make([][]float64, len(rows))
	for i, r := range rows {
		out[i] = append([]float64{float64(start + int64(i)*step)}, r[1:]...)
	}
	return out
}

func withVolume(rows [][]float64) [][]float64 {
	out := make([][]float64, len(rows))
	for i, r := range rows {
		out[i] = append(append([]float64(nil), r...), 0)
	}
	return out
}

func capture(frames map[string][][]float64) *replaycapture.Capture {
	c := &replaycapture.Capture{Version: 1, Symbol: "EURUSD", Columns: []string{"t", "open", "high", "low", "close", "volume"}, Timeframes: map[string][][]float64{}}
	for tf, rows := range frames {
		c.Timeframes[tf] = withVolume(rows)
	}
	return c
}

// replayTrendline replays a capture through the exact engine path with every other strategy off, and returns the Trendline opportunities in dispatch order.
func replayTrendline(t *testing.T, c *replaycapture.Capture, primary market.Timeframe, parameters map[string]any) []opportunity.Candidate {
	t.Helper()
	candidates, _ := replayWithOptions(t, c, primary, parameters)
	return candidates
}

func replayWithOptions(t *testing.T, c *replaycapture.Capture, primary market.Timeframe, parameters map[string]any) ([]opportunity.Candidate, replaycapture.EnvelopeOptions) {
	t.Helper()
	doc, err := config.ResolveDocument("../../../config/apexvoid.yml")
	if err != nil {
		t.Fatal(err)
	}
	var off []string
	for _, id := range strategy.KnownIDs() {
		if id != "trendline" {
			off = append(off, string(id))
		}
	}
	opts := replaycapture.Options{DisabledStrategies: off}
	if parameters != nil {
		opts.StrategyParameters = map[string]map[string]any{"trendline": parameters}
	}
	result, err := replaycapture.Replay(doc, c, primary, opts)
	if err != nil {
		t.Fatal(err)
	}
	var out []opportunity.Candidate
	for _, candidate := range result.Discovered {
		if candidate.Strategy == "trendline" {
			out = append(out, candidate)
		}
	}
	return out, result.Options
}

func ids(candidates []opportunity.Candidate) string {
	var out []string
	for _, c := range candidates {
		out = append(out, c.ID)
	}
	return strings.Join(out, ",")
}

// rising is a monotone drift with no swing: a frame whose structure is up and which has no line to trade.
func rising(from, to, step int64, base float64) [][]float64 {
	var out [][]float64
	p := base
	for t := from; t <= to; t += step {
		out = append(out, []float64{float64(t), p, p + 0.00003, p - 0.00001, p + 0.00002})
		p += 0.00002
	}
	return out
}

func frames(m map[string][][]float64) *replaycapture.Capture { return capture(m) }

func only(t *testing.T, got []opportunity.Candidate) opportunity.Candidate {
	t.Helper()
	if len(got) != 1 {
		t.Fatalf("got %d Trendline opportunities (%s), want exactly 1", len(got), ids(got))
	}
	return got[0]
}

func TestTwoAnchorsAreEnoughOnEachTimeframe(t *testing.T) {
	s := load(t)
	start := int64(1_800_000_000)
	// The second anchor is the 161st M15 bar of the fixture (the bar count, not the clock, is what the M5 reading keeps: the real window spans a weekend).
	secondB := start + 300*int64(indexOf(s.M15, s.B))
	cases := []struct {
		name      string
		frames    map[string][][]float64
		primary   market.Timeframe
		direction market.Direction
		kind      string
		secondB   int64 // open time of the second anchor
		bar       int64
	}{
		{"M15 SELL", map[string][][]float64{"M15": s.M15, "H1": s.H1}, market.M15, market.Sell, "resistance", s.B, 900},
		{"M15 BUY", map[string][][]float64{"M15": mirror(s.M15), "H1": mirror(s.H1)}, market.M15, market.Buy, "support", s.B, 900},
		{"M5 SELL", map[string][][]float64{"M5": reread(s.M15, start, 300), "H1": reread(s.H1, start-int64(len(s.H1))*3600, 3600)}, market.M5, market.Sell, "resistance", secondB, 300},
		{"M5 BUY", map[string][][]float64{"M5": mirror(reread(s.M15, start, 300)), "H1": mirror(reread(s.H1, start-int64(len(s.H1))*3600, 3600))}, market.M5, market.Buy, "support", secondB, 300},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, opts := replayWithOptions(t, capture(c.frames), c.primary, nil)
			o := only(t, got)
			tf := string(c.primary)
			if o.Direction != c.direction || o.ObservedTimeframe != c.primary || o.StructureTimeframe != c.primary {
				t.Errorf("direction/observed/structure = %s/%s/%s, want %s/%s/%s", o.Direction, o.ObservedTimeframe, o.StructureTimeframe, c.direction, c.primary, c.primary)
			}
			if want := "trendline:" + tf + ":" + c.kind + ":"; !strings.HasPrefix(o.StructuralID, want) || !strings.HasSuffix(o.StructuralID, ":"+strconv.FormatInt(c.secondB, 10)) {
				t.Errorf("thesis %q, want %q<anchor A>:%d (timeframe, kind and both anchor times)", o.StructuralID, want, c.secondB)
			}
			// Two anchors, no validation touch, and the reaction is the second anchor's own candle: a fresh second-touch reaction, no further visit.
			if v := o.Quality.Components["validation_touches"]; v != 0 {
				t.Errorf("validation_touches = %v, want 0", v)
			}
			if o.Quality.Components["reaction_at_second_anchor"] != 1 || o.Reaction.TouchBarTime != c.secondB {
				t.Errorf("touch %d at_second_anchor %v, want the second anchor %d", o.Reaction.TouchBarTime, o.Quality.Components["reaction_at_second_anchor"], c.secondB)
			}
			// Created on the bar that closes the pivot's confirmation (two bars after it), never earlier and never backdated to the anchor.
			if o.CreatedAt != c.secondB+2*c.bar || o.Reaction.ConfirmationBarTime > o.CreatedAt {
				t.Errorf("created %d confirmation %d, want creation at the second anchor + 2 bars (%d)", o.CreatedAt, o.Reaction.ConfirmationBarTime, c.secondB+2*c.bar)
			}
			if want := strings.ToLower(tf) + "_trendline_causal_anchors"; !hasEvidence(o, want) {
				t.Errorf("evidence lacks %q", want)
			}
			// Geometry: the stop is beyond the entry on the far side, the target on the trade side (a structural target is required: none is invented).
			buy := c.direction == market.Buy
			stop, target := float64(o.Invalidation.Price), float64(o.Targets[0].Price.Price)
			if buy && !(stop < float64(o.Entry.Low) && target > float64(o.Entry.High)) || !buy && !(stop > float64(o.Entry.High) && target < float64(o.Entry.Low)) {
				t.Errorf("geometry: entry %+v stop %v target %v", o.Entry, stop, target)
			}
			if o.Targets[0].Price.Label != "nearest_opposing_liquidity" {
				t.Errorf("target label %q, want nearest_opposing_liquidity", o.Targets[0].Price.Label)
			}
			// The envelope Kafka carries.
			raw, err := replaycapture.CreationEnvelope(o, opts)
			if err != nil {
				t.Fatal(err)
			}
			var env struct {
				Payload struct {
					Timeframe          string `json:"timeframe"`
					StructureTimeframe string `json:"structure_timeframe"`
					Strategy           string `json:"strategy"`
				} `json:"payload"`
			}
			if err := json.Unmarshal(raw, &env); err != nil {
				t.Fatal(err)
			}
			if env.Payload.Timeframe != tf || env.Payload.StructureTimeframe != tf || env.Payload.Strategy != "trendline" {
				t.Errorf("envelope timeframe/structure_timeframe = %s/%s, want %s/%s", env.Payload.Timeframe, env.Payload.StructureTimeframe, tf, tf)
			}
		})
	}
}

func indexOf(rows [][]float64, t int64) int {
	for i, r := range rows {
		if int64(r[0]) == t {
			return i
		}
	}
	return -1
}

func hasEvidence(o opportunity.Candidate, code string) bool {
	for _, e := range o.Evidence {
		if e.Code == code {
			return true
		}
	}
	return false
}

// No future candle recognises the second anchor early: a swing high is a swing only once its confirming bars have closed. The second anchor B is the bar at index
// len-6 of the fixture and its confirmation closes with the bar at len-4 (B + 2 bars): with the data up to B + 1 there is no line and no opportunity, with the
// confirming close there is exactly one.
func TestNoLookaheadAroundPivotConfirmation(t *testing.T) {
	s := load(t)
	last := len(s.M15)
	for keep, want := range map[int]int{last - 5: 0, last - 4: 0, last - 3: 1} {
		got := replayTrendline(t, capture(map[string][][]float64{"M15": s.M15[:keep], "H1": s.H1}), market.M15, nil)
		if len(got) != want {
			t.Errorf("with %d of %d bars: %d opportunities (%s), want %d", keep, last, len(got), ids(got), want)
		}
	}
}

// Reaction evidence older than the lookback does not execute: with a two-bar window the reaction at B (two bars before the close that confirms B) is already stale,
// and nothing is backdated or published later.
func TestStaleSecondTouchEvidenceDoesNotExecute(t *testing.T) {
	s := load(t)
	c := capture(map[string][][]float64{"M15": s.M15, "H1": s.H1})
	if n := len(replayTrendline(t, c, market.M15, nil)); n != 1 {
		t.Fatalf("default lookback: %d opportunities, want 1", n)
	}
	stale := map[string]any{"timeframes": map[string]any{"M15": map[string]any{"reaction_lookback_bars": 2}}}
	if got := replayTrendline(t, c, market.M15, stale); len(got) != 0 {
		t.Errorf("a two-bar window still published %s from reaction evidence that is three bars old", ids(got))
	}
}

// A line with no structural target room is not published and no target is made up.
func TestInsufficientTargetRoomIsRefused(t *testing.T) {
	s := load(t)
	c := capture(map[string][][]float64{"M15": s.M15, "H1": s.H1})
	if got := replayTrendline(t, c, market.M15, map[string]any{"minimum_target_distance_atr": 60.0}); len(got) != 0 {
		t.Errorf("published %s with no structural target 60 ATR away", ids(got))
	}
}

// M5 and M15 are independent setups of one strategy: each is published when its own frame is valid, whatever the other frame shows.
func TestEachTimeframeDecidesOnItsOwnFrame(t *testing.T) {
	s := load(t)
	start := int64(1_800_000_000)
	m5 := reread(s.M15, start, 300)
	h1 := reread(s.H1, start-int64(len(s.H1))*3600, 3600)
	first, last := int64(m5[0][0]), int64(m5[len(m5)-1][0])

	alone := only(t, replayTrendline(t, capture(map[string][][]float64{"M5": m5, "H1": h1}), market.M5, nil))
	// The M15 frame disagrees (a clean rising trend, no line to trade): the M5 setup is still published, unchanged, and nothing is published on M15.
	withM15 := replayTrendline(t, capture(map[string][][]float64{"M5": m5, "M15": rising(first-900*200, last, 900, 1.10), "H1": h1}), market.M5, nil)
	if o := only(t, withM15); o.ID != alone.ID || o.ObservedTimeframe != market.M5 {
		t.Errorf("M5 setup changed when M15 disagreed: %s on %s, want %s on M5", o.ID, o.ObservedTimeframe, alone.ID)
	}
	// `alone` above had no M15 frame at all: the M5 setup does not wait for one.

	m15Alone := only(t, replayTrendline(t, capture(map[string][][]float64{"M15": s.M15, "H1": s.H1}), market.M15, nil))
	// The M5 frame disagrees (a clean rising trend, no line to trade): the M15 setup is still published, unchanged, and nothing is published on M5.
	f15, l15 := int64(s.M15[0][0]), int64(s.M15[len(s.M15)-1][0])
	withM5 := replayTrendline(t, capture(map[string][][]float64{"M15": s.M15, "M5": rising(f15-300*200, l15+600, 300, 1.10), "H1": s.H1}), market.M15, nil)
	if o := only(t, withM5); o.ID != m15Alone.ID || o.ObservedTimeframe != market.M15 {
		t.Errorf("M15 setup changed when M5 disagreed: %s on %s, want %s on M15", o.ID, o.ObservedTimeframe, m15Alone.ID)
	}
	// Independent identities: the same prices read as M5 and as M15 are two theses.
	if alone.ID == m15Alone.ID || alone.StructuralID == m15Alone.StructuralID {
		t.Errorf("M5 and M15 setups share an identity: %s / %s", alone.StructuralID, m15Alone.StructuralID)
	}
}

// Replay and restart: the same bars give the same opportunities, and a restart that starts from later history (fewer bars before the line) gives the same ids.
func TestIdentityIsStableAcrossReplayAndRestart(t *testing.T) {
	s := load(t)
	full := replayTrendline(t, capture(map[string][][]float64{"M15": s.M15, "H1": s.H1}), market.M15, nil)
	again := replayTrendline(t, capture(map[string][][]float64{"M15": s.M15, "H1": s.H1}), market.M15, nil)
	restarted := replayTrendline(t, capture(map[string][][]float64{"M15": s.M15[40:], "H1": s.H1}), market.M15, nil)
	if ids(full) == "" || ids(full) != ids(again) || ids(full) != ids(restarted) {
		t.Errorf("ids differ: full %s, replay %s, restart %s", ids(full), ids(again), ids(restarted))
	}
	if full[0].StructuralID != restarted[0].StructuralID {
		t.Errorf("thesis changed across a restart: %s vs %s", full[0].StructuralID, restarted[0].StructuralID)
	}
}
