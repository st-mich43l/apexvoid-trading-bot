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
	"sync"
	"syscall"
	"time"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/barrier"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/logging"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/kafka"
	redistransport "github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/transport/redis"
)

const primaryTimeframe = market.M5

var log = logging.New("analysis-engine")

func init() {
	engine.SetLogger(log)
}

func main() {
	path := os.Getenv(config.RootFileEnv)
	if path == "" {
		log.Error("configuration path is not set", "env", config.RootFileEnv)
		os.Exit(1)
	}
	if err := run(path); err != nil {
		log.Error("service stopped", "error", err)
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

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	e := engine.NewEngine(nil)

	// Kafka is a producer capability only. Failure to initialize it is visible
	// in Kafka health/logs but never prevents Redis from maintaining analysis
	// state. Phase S9: a strategy's opportunity lifecycle transitions are
	// retained and retried (OpportunityPublisher, off the ingestion hot
	// path — see its own doc comment) rather than silently discarded;
	// SetPublisher below must run BEFORE the registration loop, since
	// Engine.Register reads the current publisher once at worker-
	// construction time, not on every event.
	kafkaCfg, err := engine.KafkaConfigFromConfig(doc)
	if err != nil {
		return fmt.Errorf("loading Kafka transport config: %w", err)
	}
	replayMaxAge, err := engine.OpportunityReplayMaxAgeFromConfig(doc)
	if err != nil {
		return fmt.Errorf("loading opportunity replay freshness: %w", err)
	}
	kafkaHealth := kafka.NewHealth(kafkaCfg.Enabled)
	var provenance kafka.ConfigProvenance
	var producer *kafka.Producer
	if kafkaCfg.Enabled {
		provenance, err = engine.ConfigProvenanceFromConfig(doc)
		if err != nil {
			return fmt.Errorf("reading Kafka configuration provenance: %w", err)
		}
		producer, err = kafka.NewProducer(ctx, kafkaCfg, provenance, kafka.NewMetrics(), kafkaHealth)
		if err != nil {
			log.Warn("Kafka producer unavailable; Redis market ingestion continues", "error", err)
		}
	}
	var producerMu sync.Mutex
	defer func() {
		producerMu.Lock()
		current := producer
		producerMu.Unlock()
		if current != nil {
			shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			_ = current.Close(shutdown)
		}
	}()
	var client engine.OpportunityKafkaClient
	if producer != nil {
		client = producer
	}
	publisher, err := engine.NewDurableOpportunityPublisher(client, e.Telemetry(), kafkaCfg.OutboxPath)
	if err != nil {
		return fmt.Errorf("opening opportunity publication outbox: %w", err)
	}
	publisher.DiscardStaleLifecycleJobs(time.Now().UTC(), replayMaxAge)
	e.SetPublisher(publisher)
	go publisher.Run(ctx)
	if kafkaCfg.Enabled && producer == nil {
		// NewProducer performs a bounded broker ping. A Kafka container can
		// legitimately become ready after analysis/Redis during a compose
		// rollout; keep the durable publisher alive and attach the producer
		// when that broker becomes reachable instead of dropping the startup
		// backlog with a permanently nil transport.
		go func() {
			ticker := time.NewTicker(5 * time.Second)
			defer ticker.Stop()
			for {
				select {
				case <-ctx.Done():
					return
				case <-ticker.C:
				}
				candidate, connectErr := kafka.NewProducer(ctx, kafkaCfg, provenance, kafka.NewMetrics(), kafkaHealth)
				if connectErr != nil {
					log.Warn("Kafka producer still unavailable; durable outbox retained", "error", connectErr)
					continue
				}
				producerMu.Lock()
				producer = candidate
				producerMu.Unlock()
				publisher.SetClient(candidate)
				log.Info("Kafka producer attached after cold start")
				return
			}
		}()
	}

	series := make([]redistransport.Series, 0)
	barrierConfigs := make(map[market.Symbol]barrier.Config, len(live))
	for _, symbol := range live {
		settings, err := engine.LoadSettings(doc, primaryTimeframe, false)
		if err != nil {
			return fmt.Errorf("loading analysis settings for %s: %w", symbol, err)
		}
		if err := engine.ApplyInstrument(&settings, doc, symbol); err != nil {
			return err
		}
		canonical := market.Symbol(symbol)
		if err := e.Register(canonical, settings); err != nil {
			return fmt.Errorf("registering %s: %w", symbol, err)
		}
		barrierCfg, err := engine.BarrierConfigFromConfig(doc, settings.Geometry)
		if err != nil {
			return fmt.Errorf("loading opposing-barrier policy for %s: %w", symbol, err)
		}
		barrierConfigs[canonical] = barrierCfg
		for timeframe, depth := range settings.HistoryDepths {
			series = append(series, redistransport.Series{Symbol: canonical, Timeframe: timeframe, Depth: depth})
		}
	}
	if len(series) == 0 {
		return fmt.Errorf("no live market series configured")
	}

	redisCfg, err := engine.RedisConfigFromConfig(doc)
	if err != nil {
		return fmt.Errorf("loading Redis transport config: %w", err)
	}
	redisHealth := redistransport.NewHealth(true)
	var runtime *redistransport.Runtime
	publishDerivedState := func(ctx context.Context, symbol market.Symbol, snapshot engine.AnalysisSnapshot) {
		// Best-effort: Algo Bot's execution-time opposing-barrier check
		// reads this to recheck a Go-origin plan against live structure
		// (see internal/transport/redis/zonebook.go's own doc comment).
		// A publish failure must never interrupt candle ingestion, same
		// principle this file already applies to the Kafka producer.
		if pubErr := runtime.PublishZoneBook(ctx, symbol, snapshot.Zones, time.Now().UTC()); pubErr != nil {
			log.Warn("zone book publish failed", "symbol", symbol, "error", pubErr)
		}
		// Algo Bot executes only opportunities the engine still holds
		// live; see internal/transport/redis/liveopportunities.go.
		if pubErr := runtime.PublishLiveOpportunities(ctx, symbol, snapshot.Opportunities, time.Now().UTC()); pubErr != nil {
			log.Warn("live opportunities publish failed", "symbol", symbol, "error", pubErr)
		}
	}
	// The bootstrap replays every retained bar (about 3,000 per symbol). Writing
	// the whole zone book and live set to Redis after each one kept the engine
	// busy for ~10 minutes after every restart with detection silent; only the
	// state after the last replayed bar is worth publishing, so it is kept and
	// published once when the bootstrap completes.
	var bootstrapMu sync.Mutex
	bootstrapLatest := map[market.Symbol]engine.AnalysisSnapshot{}
	runtime, err = redistransport.NewRuntime(redisCfg, series, func(ctx context.Context, event marketdata.BarEvent) (marketdata.AppendResult, error) {
		snapshot, result, err := e.DispatchWithResult(event)
		if err == nil && result == marketdata.AppendAccepted {
			if event.Origin == marketdata.EventOriginBootstrap {
				bootstrapMu.Lock()
				bootstrapLatest[event.Symbol] = snapshot
				bootstrapMu.Unlock()
				return result, err
			}
			publishDerivedState(ctx, event.Symbol, snapshot)
		}
		return result, err
	}, redisHealth, redistransport.NewMetrics())
	if err != nil {
		return fmt.Errorf("initializing Redis market runtime: %w", err)
	}
	defer runtime.Close()
	runtime.SetOnBootstrapComplete(func(ctx context.Context) {
		publisher.Flush()
		bootstrapMu.Lock()
		latest := bootstrapLatest
		bootstrapLatest = nil
		bootstrapMu.Unlock()
		for symbol, snapshot := range latest {
			algo := kafka.AlgorithmVersion{
				Structure: snapshot.Version.StructureVersion,
				Liquidity: snapshot.Version.LiquidityVersion,
			}
			// The recovered lifecycle event is guarded by Algo Bot against the
			// Redis live-opportunity projection. Publish that projection first;
			// otherwise the asynchronous Kafka publisher can outrun Redis and
			// permanently classify every recovered setup as not-live.
			publishDerivedState(ctx, symbol, snapshot)
			publisher.BackfillLive(symbol, algo, snapshot.Opportunities, time.Now().UTC())
		}
	})
	for symbol, cfg := range barrierConfigs {
		runtime.SetBarrierConfig(symbol, cfg)
	}

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
