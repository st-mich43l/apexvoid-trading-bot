// Command analysis-engine is the analysis service composition root.
// Wires Configuration V3 -> the Analysis Engine V2 pipeline
// (internal/engine) -> the Kafka transport (internal/transport/kafka),
// when transport.kafka.enabled=true — source task §42: "update
// cmd/analysis-engine/main.go only as necessary to wire Configuration V3
// -> Kafka client -> market consumer -> engine event handler." When
// Kafka is disabled (the checked-in default, config/transport.yml), the
// process validates configuration and exits — there is nothing else for
// it to do yet: no strategy exists to produce opportunities, and no
// other event source is wired in this task.
package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
)

// primaryTimeframe is the timeframe internal/context.Build treats as
// "primary" for a registered symbol's single-value Structure/Liquidity
// fields (every configured timeframe is still tracked independently via
// MarketContext.Timeframes — see internal/context/market.go). M5 matches
// cmd/replay's own existing default; no config key for this exists yet
// (a genuine future addition, not fabricated here to look configurable).
const primaryTimeframe = market.M5

func main() {
	path := os.Getenv(config.RootFileEnv)
	if path == "" {
		fmt.Fprintf(os.Stderr, "analysis-engine: %s not set, nothing to load.\n", config.RootFileEnv)
		os.Exit(1)
	}
	if err := run(path); err != nil {
		fmt.Fprintln(os.Stderr, "analysis-engine:", err)
		os.Exit(1)
	}
}

func run(configPath string) error {
	doc, err := config.ResolveDocument(configPath)
	if err != nil {
		return fmt.Errorf("loading configuration: %w", err)
	}
	live, err := doc.LiveInstruments()
	if err != nil {
		return fmt.Errorf("reading live instruments: %w", err)
	}
	environment, _ := doc.Get("runtime.environment")

	e := engine.NewEngine(nil)
	for _, symbol := range live {
		settings, err := engine.LoadSettings(doc, primaryTimeframe, false)
		if err != nil {
			return fmt.Errorf("loading Analysis Engine V2 settings for %s: %w", symbol, err)
		}
		if err := e.Register(market.Symbol(symbol), settings); err != nil {
			return fmt.Errorf("registering %s: %w", symbol, err)
		}
	}
	fmt.Fprintf(os.Stderr, "analysis-engine: loaded config_root=%s environment=%v live_instruments=%v\n",
		configPath, environment, live)

	kafkaCfg, err := engine.KafkaConfigFromConfig(doc)
	if err != nil {
		return fmt.Errorf("loading Kafka transport config: %w", err)
	}
	if !kafkaCfg.Enabled {
		fmt.Fprintln(os.Stderr, "analysis-engine: transport.kafka.enabled=false — nothing to consume, exiting.")
		return nil
	}

	// Kafka enabled: fail closed at startup if the broker is
	// unreachable or configuration is otherwise unusable (source task
	// §43) — never advertise ready while actually disconnected.
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	health := kafka.NewHealth(true)
	metrics := kafka.NewMetrics()
	handler := engine.NewKafkaHandler(e)

	consumer, err := kafka.NewConsumer(ctx, kafkaCfg, handler, metrics, health)
	if err != nil {
		return fmt.Errorf("starting Kafka consumer: %w", err)
	}
	fmt.Fprintf(os.Stderr, "analysis-engine: Kafka consumer started brokers=%v topics=%s,%s consumer_group=%s\n",
		kafkaCfg.Brokers, kafkaCfg.Topics.MarketBarClosed, kafkaCfg.Topics.MarketTick, kafkaCfg.ConsumerGroup)

	// Run blocks until ctx is cancelled (SIGINT/SIGTERM) or a transient
	// handler failure stalls past its retry budget (source task §37) —
	// Run's own deferred client.Close() handles producer-side shutdown;
	// this composition root does not construct a Producer (nothing
	// publishes yet — no strategy exists, source task §42's own "do not
	// fabricate fake strategy output just to demonstrate publishing").
	return consumer.Run(ctx)
}
