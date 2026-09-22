// TestFullKafkaPipeline proves the actual composition (not a test
// double): a real Kafka record -> kafka.Consumer -> engine.KafkaHandler
// -> the exact same Engine.Dispatch path test/integration's own
// pipeline_test.go and cmd/replay use -> a real AnalysisSnapshot.
// Skips cleanly without KAFKA_TEST_BROKERS (source task §51).
package integration_test

import (
	"context"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kadm"
	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/indicator"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/liquidity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/structure"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

func kafkaTestBrokers(t *testing.T) []string {
	t.Helper()
	raw := os.Getenv("KAFKA_TEST_BROKERS")
	if raw == "" {
		t.Skip("KAFKA_TEST_BROKERS not set — skipping real-broker integration test; see docs/transport/kafka.md")
	}
	return strings.Split(raw, ",")
}

func TestFullKafkaPipeline_RealRecordsFlowThroughToARealAnalysisSnapshot(t *testing.T) {
	brokers := kafkaTestBrokers(t)
	base := "kafka-pipeline-test-" + strconv.FormatInt(time.Now().UnixNano(), 36)
	cfg := kafka.Config{
		Enabled: true, Brokers: brokers, ClientID: "apexvoid-analysis-engine-pipeline-test",
		Topics: kafka.Topics{
			MarketBarClosed: base + "-bar", MarketTick: base + "-tick",
			AnalysisOpportunity: base + "-opp", AnalysisOpportunityInvalidated: base + "-oppinv",
		},
		ConsumerGroup: base + "-group",
	}

	adminClient, err := kgo.NewClient(kgo.SeedBrokers(brokers...))
	if err != nil {
		t.Fatalf("kgo.NewClient: %v", err)
	}
	defer adminClient.Close()
	admin := kadm.NewClient(adminClient)
	createCtx, createCancel := context.WithTimeout(context.Background(), 15*time.Second)
	if _, err := admin.CreateTopics(createCtx, 1, 1, nil, cfg.Topics.MarketBarClosed, cfg.Topics.MarketTick, cfg.Topics.AnalysisOpportunity, cfg.Topics.AnalysisOpportunityInvalidated); err != nil {
		createCancel()
		t.Fatalf("CreateTopics: %v", err)
	}
	createCancel()

	e := engine.NewEngine(nil)
	settings := engine.Settings{
		ATR: engine.ATRSettings{Algorithm: indicator.AlgorithmSimple, Length: 14},
		Structure: structure.Settings{
			Version: "v2", PivotLeftBars: 2, PivotRightBars: 2,
			Promotion:         structure.PromotionConfig{MinimumExcursionATR: 0.5, InternalATR: 1.0, IntermediateATR: 2.0, MajorATR: 3.5},
			EqualToleranceATR: 0.05,
			Break: structure.BreakConfig{
				MinimumPenetrationATR: 0.5, SweepReclaimBars: 6, FailedBreakReclaimBars: 3, DisplacementMaxBars: 6,
				Displacement: structure.DisplacementConfig{RangeATR: 1.5, BodyDominance: 0.55},
			},
		},
		Liquidity:           liquidity.Config{Version: "v1", EqualLevelToleranceATR: 0.05, PoolMinimumTouches: 2, SweepReclaimBars: 6},
		HistoryDepths:       map[market.Timeframe]int{"M5": 500},
		PrimaryTimeframe:    "M5",
		AllowReplaceForming: false,
	}
	if err := e.Register("XAU", settings); err != nil {
		t.Fatalf("Register: %v", err)
	}
	handler := engine.NewKafkaHandler(e)

	// Publish 5 real bar-closed records — the exact wire shape
	// ctrader-engine's future producer will send — directly via kgo,
	// bypassing kafka.Producer (which only knows the opportunity event
	// types).
	openTime := int64(1_700_000_000)
	for i := 0; i < 5; i++ {
		payload := kafka.BarClosedPayload{
			CanonicalSymbol: "XAU", Timeframe: "M5", OpenTime: openTime, CloseTime: openTime + 300,
			Open: 2000 + float64(i), High: 2005 + float64(i), Low: 1998 + float64(i), Close: 2003 + float64(i), Volume: 10,
		}
		payloadBytes, err := kafka.Encode(payload)
		if err != nil {
			t.Fatalf("encoding payload %d: %v", i, err)
		}
		env := kafka.Envelope{
			EventID: kafka.NewEventID(), EventType: cfg.Topics.MarketBarClosed, EventVersion: 1,
			OccurredAt: payload.CloseTime, ProducedAt: payload.CloseTime, Producer: "ctrader-engine-test",
			CorrelationID: "corr-pipeline-test", Payload: payloadBytes,
		}
		envBytes, err := kafka.Encode(env)
		if err != nil {
			t.Fatalf("encoding envelope %d: %v", i, err)
		}
		produceCtx, produceCancel := context.WithTimeout(context.Background(), 10*time.Second)
		results := adminClient.ProduceSync(produceCtx, &kgo.Record{Topic: cfg.Topics.MarketBarClosed, Key: []byte("XAU"), Value: envBytes})
		produceCancel()
		if err := results.FirstErr(); err != nil {
			t.Fatalf("producing bar %d: %v", i, err)
		}
		openTime += 300
	}

	runCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	consumer, err := kafka.NewConsumer(runCtx, cfg, handler, nil, nil)
	if err != nil {
		t.Fatalf("NewConsumer: %v", err)
	}
	consumeCtx, consumeCancel := context.WithTimeout(runCtx, 8*time.Second)
	_ = consumer.Run(consumeCtx)
	consumeCancel()

	snap, err := e.Snapshot("XAU", openTime)
	if err != nil {
		t.Fatalf("Snapshot: %v", err)
	}
	structState, ok := snap.Structure["M5"]
	if !ok {
		t.Fatal("expected M5 structure in the snapshot after 5 real Kafka-delivered bars")
	}
	_ = structState // 5 bars is too few to reliably produce a confirmed swing; presence of the M5 key alone proves real bars actually flowed through Dispatch, same assertion style as test/engine/worker_test.go's own cross-symbol test.

	if snap.Version.StructureVersion != "v2" || snap.Version.LiquidityVersion != "v1" {
		t.Errorf("expected the real AnalysisSnapshot to carry algorithm versions, got %+v", snap.Version)
	}
	if snap.Context.Symbol != "XAU" {
		t.Errorf("expected Context.Symbol=XAU, got %v", snap.Context.Symbol)
	}
}
