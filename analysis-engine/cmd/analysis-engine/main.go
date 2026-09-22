// Command analysis-engine is the analysis service composition root.
// Wires Configuration V3 -> the Analysis Engine V2 pipeline
// (internal/engine) -> the Kafka transport (internal/transport/kafka),
// when transport.kafka.enabled=true — source task §42: "update
// cmd/analysis-engine/main.go only as necessary to wire Configuration V3
// -> Kafka client -> market consumer -> engine event handler." When
// Kafka is disabled, the
// process validates configuration and exits — there is nothing else for
// it to do yet: no strategy exists to produce opportunities, and no
// other event source is wired in this task.
package main

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

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

	server := &http.Server{Addr: ":8080", Handler: healthHandler(health)}
	serverErr := make(chan error, 1)
	go func() {
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			serverErr <- err
		}
	}()
	consumerErr := make(chan error, 1)
	go func() { consumerErr <- consumer.Run(ctx) }()
	select {
	case err := <-serverErr:
		stop()
		return fmt.Errorf("health server: %w", err)
	case err := <-consumerErr:
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdownCtx)
		return err
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdownCtx)
		return <-consumerErr
	}
}

func healthHandler(health *kafka.Health) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/health/live", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	mux.HandleFunc("/health/ready", func(w http.ResponseWriter, _ *http.Request) {
		if !health.Snapshot().Ready() {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
	})
	return mux
}
