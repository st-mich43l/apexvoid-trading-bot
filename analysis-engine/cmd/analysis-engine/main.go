// Command analysis-engine composes Configuration V3, Redis market ingestion
// and the producer-only Kafka event transport. Redis advances technical state;
// a Kafka outage never becomes a candle-ingestion outage.
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
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
	redistransport "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/redis"
)

const primaryTimeframe = market.M5

func main() {
	path := os.Getenv(config.RootFileEnv)
	if path == "" {
		fmt.Fprintf(os.Stderr, "analysis-engine: %s not set\n", config.RootFileEnv)
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

	e := engine.NewEngine(nil)
	series := make([]redistransport.Series, 0)
	for _, symbol := range live {
		settings, err := engine.LoadSettings(doc, primaryTimeframe, false)
		if err != nil {
			return fmt.Errorf("loading analysis settings for %s: %w", symbol, err)
		}
		canonical := market.Symbol(symbol)
		if err := e.Register(canonical, settings); err != nil {
			return fmt.Errorf("registering %s: %w", symbol, err)
		}
		for timeframe, depth := range settings.HistoryDepths {
			series = append(series, redistransport.Series{Symbol: canonical, Timeframe: timeframe, Depth: depth})
		}
	}
	if len(series) == 0 {
		return fmt.Errorf("no live market series configured")
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	redisCfg, err := engine.RedisConfigFromConfig(doc)
	if err != nil {
		return fmt.Errorf("loading Redis transport config: %w", err)
	}
	redisHealth := redistransport.NewHealth(true)
	runtime, err := redistransport.NewRuntime(redisCfg, series, func(ctx context.Context, event marketdata.BarEvent) (marketdata.AppendResult, error) {
		_, result, err := e.DispatchWithResult(event)
		return result, err
	}, redisHealth, redistransport.NewMetrics())
	if err != nil {
		return fmt.Errorf("initializing Redis market runtime: %w", err)
	}
	defer runtime.Close()

	// Kafka is a producer capability only. Failure to initialize it is visible
	// in Kafka health/logs but never prevents Redis from maintaining analysis
	// state. A future strategy must retain/retry a failed opportunity publish;
	// it must never silently discard an opportunity.
	kafkaCfg, err := engine.KafkaConfigFromConfig(doc)
	if err != nil {
		return fmt.Errorf("loading Kafka transport config: %w", err)
	}
	kafkaHealth := kafka.NewHealth(kafkaCfg.Enabled)
	var producer *kafka.Producer
	if kafkaCfg.Enabled {
		provenance, err := engine.ConfigProvenanceFromConfig(doc)
		if err != nil {
			return fmt.Errorf("reading Kafka configuration provenance: %w", err)
		}
		producer, err = kafka.NewProducer(ctx, kafkaCfg, provenance, kafka.NewMetrics(), kafkaHealth)
		if err != nil {
			fmt.Fprintf(os.Stderr, "analysis-engine: Kafka producer unavailable; Redis market ingestion continues: %v\n", err)
		}
	}
	defer func() {
		if producer != nil {
			shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			_ = producer.Close(shutdown)
		}
	}()

	server := &http.Server{Addr: ":8080", Handler: healthHandler(redisHealth)}
	serverErr := make(chan error, 1)
	go func() {
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			serverErr <- err
		}
	}()
	runtimeErr := make(chan error, 1)
	go func() { runtimeErr <- runtime.Run(ctx) }()

	select {
	case err := <-serverErr:
		stop()
		return fmt.Errorf("health server: %w", err)
	case err := <-runtimeErr:
		if err != nil {
			return err
		}
	case <-ctx.Done():
		<-runtimeErr
	}
	shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	return server.Shutdown(shutdown)
}

func healthHandler(redisHealth *redistransport.Health) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/health/live", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	mux.HandleFunc("/health/ready", func(w http.ResponseWriter, _ *http.Request) {
		if !redisHealth.Snapshot().Ready() {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
	})
	return mux
}
