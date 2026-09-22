// Command kafka-smoke is an operational/debug tool only (source task
// §67) — no strategy behavior. It exercises internal/transport/kafka
// against a real broker for manual verification:
//
//	go run ./cmd/kafka-smoke -config ../config/apexvoid.yml -mode health
//	go run ./cmd/kafka-smoke -config ../config/apexvoid.yml -mode produce
//	go run ./cmd/kafka-smoke -config ../config/apexvoid.yml -mode consume
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/opportunity"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

func main() {
	configPath := flag.String("config", "", "path to config/apexvoid.yml (Configuration V3 root)")
	mode := flag.String("mode", "health", "health | produce | consume")
	flag.Parse()

	if *configPath == "" {
		fmt.Fprintln(os.Stderr, "usage: kafka-smoke -config <apexvoid.yml> -mode health|produce|consume")
		os.Exit(2)
	}
	if err := run(*configPath, *mode); err != nil {
		fmt.Fprintln(os.Stderr, "kafka-smoke:", err)
		os.Exit(1)
	}
}

func run(configPath, mode string) error {
	doc, err := config.ResolveDocument(configPath)
	if err != nil {
		return fmt.Errorf("loading config: %w", err)
	}
	cfg, err := engine.KafkaConfigFromConfig(doc)
	if err != nil {
		return fmt.Errorf("loading Kafka config: %w", err)
	}
	if !cfg.Enabled {
		return fmt.Errorf("transport.kafka.enabled=false — nothing to smoke-test (this repo's checked-in default; pass a config with it enabled and a real broker)")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	switch mode {
	case "health":
		return runHealth(ctx, cfg)
	case "produce":
		return runProduce(ctx, doc, cfg)
	case "consume":
		return runConsume(cfg)
	default:
		return fmt.Errorf("unrecognized -mode %q (want health|produce|consume)", mode)
	}
}

func runHealth(ctx context.Context, cfg kafka.Config) error {
	health := kafka.NewHealth(true)
	producer, err := kafka.NewProducer(ctx, cfg, kafka.ConfigProvenance{}, nil, health)
	if err != nil {
		return fmt.Errorf("broker unreachable: %w", err)
	}
	defer producer.Close(ctx)
	snap := health.Snapshot()
	fmt.Printf("brokers=%v configured=%v connected=%v producer_ready=%v ready=%v\n",
		cfg.Brokers, snap.Configured, snap.Connected, snap.ProducerReady, snap.Ready())
	return nil
}

func runProduce(ctx context.Context, doc *config.Document, cfg kafka.Config) error {
	provenance, err := engine.ConfigProvenanceFromConfig(doc)
	if err != nil {
		return fmt.Errorf("computing config provenance: %w", err)
	}
	producer, err := kafka.NewProducer(ctx, cfg, provenance, nil, nil)
	if err != nil {
		return err
	}
	defer producer.Close(ctx)

	candidate := opportunity.Candidate{
		ID: "smoke-" + kafka.NewEventID(), Strategy: "smoketest", Symbol: "XAU",
		Direction:    "BUY",
		Entry:        opportunity.EntryZone{Low: 2000, High: 2002},
		Invalidation: market.PriceLevel{Price: 1990, Label: "smoke"},
		Targets:      []opportunity.Target{{Price: market.PriceLevel{Price: 2020}}},
		Evidence:     []opportunity.Evidence{{Code: "kafka_smoke_test"}},
		Quality:      opportunity.StrategyQuality{Overall: 1},
		CreatedAt:    time.Now().Unix(), ExpiresAt: time.Now().Add(time.Hour).Unix(),
	}
	algo := kafka.AlgorithmVersion{Structure: "v2", Liquidity: "v1"}
	if err := producer.PublishOpportunity(ctx, kafka.NewEventID(), "", candidate, algo, time.Now()); err != nil {
		return fmt.Errorf("publishing sample opportunity: %w", err)
	}
	fmt.Printf("published sample opportunity id=%s to topic=%s\n", candidate.ID, cfg.Topics.AnalysisOpportunity)
	return nil
}

// consumeOnceHandler prints the first bar-closed event it sees and
// signals done.
type consumeOnceHandler struct {
	done chan marketdata.BarEvent
}

func (h *consumeOnceHandler) HandleBarClosed(ctx context.Context, event marketdata.BarEvent) error {
	select {
	case h.done <- event:
	default:
	}
	return nil
}
func (h *consumeOnceHandler) HandleTick(ctx context.Context, tick kafka.TickPayload) error {
	return nil
}

func runConsume(cfg kafka.Config) error {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	handler := &consumeOnceHandler{done: make(chan marketdata.BarEvent, 1)}
	consumer, err := kafka.NewConsumer(ctx, cfg, handler, nil, nil)
	if err != nil {
		return err
	}
	fmt.Printf("consuming topic=%s consumer_group=%s — waiting for one bar (Ctrl+C to stop)...\n", cfg.Topics.MarketBarClosed, cfg.ConsumerGroup)

	go func() {
		event := <-handler.done
		fmt.Printf("received: symbol=%s timeframe=%s time=%d close=%v\n", event.Symbol, event.Timeframe, event.Candle.Time, event.Candle.Close)
		// Let processOne's own commit (which runs immediately after
		// HandleBarClosed returns, using this same ctx) complete before
		// cancelling — stopping right away would race the shutdown
		// signal against that in-flight commit.
		time.Sleep(500 * time.Millisecond)
		stop()
	}()
	err = consumer.Run(ctx)
	if err != nil && ctx.Err() != nil {
		return nil // a shutdown-triggered error after we already got what we came for is not a failure
	}
	return err
}
