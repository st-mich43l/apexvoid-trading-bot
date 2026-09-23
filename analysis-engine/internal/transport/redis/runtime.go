package redis

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	redisv9 "github.com/redis/go-redis/v9"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
)

// Dispatch hands a validated, transport-independent closed bar into the
// engine and returns its canonical market-history disposition.
type Dispatch func(context.Context, marketdata.BarEvent) (marketdata.AppendResult, error)

// Runtime owns one coalescing queue per configured symbol/timeframe. The
// engine's SymbolWorker still serializes same-symbol state; separate series
// may fetch concurrently without unbounded goroutine creation.
type Runtime struct {
	client   redisv9.UniversalClient
	cfg      Config
	series   map[Series]struct{}
	dispatch Dispatch
	health   *Health
	metrics  *Metrics

	mu      sync.Mutex
	cursors map[Series]int64
	dirty   map[Series]map[int64]struct{}
	queues  map[Series]chan struct{}
}

func NewRuntime(cfg Config, series []Series, dispatch Dispatch, health *Health, metrics *Metrics) (*Runtime, error) {
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	options, err := redisv9.ParseURL(cfg.URL)
	if err != nil {
		return nil, fmt.Errorf("redis: parse transport.redis.url: %w", err)
	}
	return NewRuntimeWithClient(redisv9.NewClient(options), cfg, series, dispatch, health, metrics)
}

// NewRuntimeWithClient supports deterministic tests while production uses
// NewRuntime. The client is still the real go-redis protocol client.
func NewRuntimeWithClient(client redisv9.UniversalClient, cfg Config, series []Series, dispatch Dispatch, health *Health, metrics *Metrics) (*Runtime, error) {
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	if client == nil || dispatch == nil {
		return nil, fmt.Errorf("redis: client and dispatch are required")
	}
	if health == nil {
		health = NewHealth(true)
	}
	if metrics == nil {
		metrics = NewMetrics()
	}
	known := make(map[Series]struct{}, len(series))
	for _, item := range series {
		// RedisBarSink writes canonical uppercase symbols and ParseNotification
		// normalizes the existing bars:new payload the same way. Normalize the
		// configured key once so a caller cannot create a ZSET/notification
		// mismatch through incidental casing.
		item.Symbol = market.Symbol(strings.ToUpper(string(item.Symbol)))
		if err := item.Validate(); err != nil {
			return nil, err
		}
		if _, exists := known[item]; exists {
			return nil, fmt.Errorf("redis: duplicate configured series %s", item)
		}
		known[item] = struct{}{}
	}
	if len(known) == 0 {
		return nil, fmt.Errorf("redis: no configured market series")
	}
	return &Runtime{
		client: client, cfg: cfg, series: known, dispatch: dispatch, health: health, metrics: metrics,
		cursors: make(map[Series]int64, len(known)), dirty: make(map[Series]map[int64]struct{}, len(known)), queues: make(map[Series]chan struct{}, len(known)),
	}, nil
}

func (r *Runtime) Close() error      { return r.client.Close() }
func (r *Runtime) Health() *Health   { return r.health }
func (r *Runtime) Metrics() *Metrics { return r.metrics }

// Run subscribes before bootstrap. Buffered notifications plus a full ZSET
// reconciliation after bootstrap remove the load-history/subscribe race;
// periodic reconciliation covers the intentionally non-durable pub/sub path.
func (r *Runtime) Run(ctx context.Context) error {
	first := true
	for ctx.Err() == nil {
		sub, messages, err := r.subscribe(ctx)
		if err != nil {
			r.health.MarkError(err, time.Now())
			if !waitContext(ctx, time.Second) {
				break
			}
			continue
		}
		if first {
			if err := r.bootstrap(ctx); err != nil {
				_ = sub.Close()
				r.health.SetSubscriptionActive(false)
				return err
			}
			r.startWorkers(ctx)
			first = false
		}
		// Capture everything written after the bootstrap scan (and after any
		// prior disconnected subscription) before relying on notifications.
		r.scheduleAll()
		if err := r.pump(ctx, messages); err != nil && ctx.Err() == nil {
			r.health.MarkError(err, time.Now())
			slog.Warn("redis: subscription interrupted; reconnecting", "error", err)
		}
		r.health.SetSubscriptionActive(false)
		_ = sub.Close()
		if !waitContext(ctx, time.Second) {
			break
		}
	}
	return nil
}

func (r *Runtime) ping(ctx context.Context) error {
	if err := r.client.Ping(ctx).Err(); err != nil {
		r.health.SetConnected(false)
		r.health.MarkError(err, time.Now())
		return fmt.Errorf("redis: ping: %w", err)
	}
	r.health.SetConnected(true)
	return nil
}

func (r *Runtime) subscribe(ctx context.Context) (*redisv9.PubSub, <-chan *redisv9.Message, error) {
	if err := r.ping(ctx); err != nil {
		return nil, nil, err
	}
	sub := r.client.Subscribe(ctx, r.cfg.BarsChannel)
	if _, err := sub.ReceiveTimeout(ctx, 5*time.Second); err != nil {
		_ = sub.Close()
		return nil, nil, fmt.Errorf("redis: subscribe %s: %w", r.cfg.BarsChannel, err)
	}
	r.health.SetSubscriptionActive(true)
	return sub, sub.Channel(redisv9.WithChannelSize(128)), nil
}

func (r *Runtime) bootstrap(ctx context.Context) error {
	started := time.Now()
	defer func() { r.metrics.Duration(MetricBootstrapMS, time.Since(started).Milliseconds()) }()
	for series := range r.series {
		values, err := r.client.ZRevRange(ctx, series.Key(), 0, int64(series.Depth-1)).Result()
		if err != nil {
			return r.readError(series, err)
		}
		for left, right := 0, len(values)-1; left < right; left, right = left+1, right-1 {
			values[left], values[right] = values[right], values[left]
		}
		for _, raw := range values {
			if err := r.process(ctx, series, raw, marketdata.EventOriginBootstrap, false); err != nil {
				return err
			}
		}
	}
	return nil
}

func (r *Runtime) startWorkers(ctx context.Context) {
	for series := range r.series {
		queue := make(chan struct{}, 1)
		r.queues[series] = queue
		go r.runSeries(ctx, series, queue)
	}
}

func (r *Runtime) runSeries(ctx context.Context, series Series, queue <-chan struct{}) {
	for {
		select {
		case <-ctx.Done():
			return
		case <-queue:
			if err := r.recover(ctx, series); err != nil && ctx.Err() == nil {
				r.health.MarkError(err, time.Now())
				slog.Warn("redis: series recovery failed", "series", series.String(), "error", err)
			}
		}
	}
}

func (r *Runtime) pump(ctx context.Context, messages <-chan *redisv9.Message) error {
	ticker := time.NewTicker(r.cfg.ReconciliationInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			r.scheduleAll()
		case message, ok := <-messages:
			if !ok {
				return fmt.Errorf("redis: subscription channel closed")
			}
			n, err := ParseNotification(message.Payload)
			if err != nil {
				r.health.MarkError(err, time.Now())
				slog.Warn("redis: ignored malformed bars notification", "error", err)
				continue
			}
			series, tracked := r.tracked(Series{Symbol: n.Symbol, Timeframe: n.Timeframe})
			if !tracked {
				continue
			}
			r.metrics.Count(MetricNotification, 1)
			r.health.MarkNotification(time.Now())
			r.schedule(series, n.Timestamp)
		}
	}
}

func (r *Runtime) scheduleAll() {
	for series := range r.series {
		r.schedule(series, 0)
	}
}

func (r *Runtime) tracked(candidate Series) (Series, bool) {
	for series := range r.series {
		if series.Symbol == candidate.Symbol && series.Timeframe == candidate.Timeframe {
			return series, true
		}
	}
	return Series{}, false
}

func (r *Runtime) schedule(series Series, correctionTimestamp int64) {
	r.mu.Lock()
	if correctionTimestamp > 0 && correctionTimestamp <= r.cursors[series] {
		if r.dirty[series] == nil {
			r.dirty[series] = map[int64]struct{}{}
		}
		r.dirty[series][correctionTimestamp] = struct{}{}
	}
	queue := r.queues[series]
	r.mu.Unlock()
	if queue == nil {
		return
	}
	select {
	case queue <- struct{}{}:
	default:
	}
}

func (r *Runtime) recover(ctx context.Context, series Series) error {
	started := time.Now()
	defer func() { r.metrics.Duration(MetricRecoveryMS, time.Since(started).Milliseconds()) }()
	r.metrics.Count(MetricRecovery, 1)
	last := r.cursor(series)
	values, err := r.client.ZRangeByScore(ctx, series.Key(), &redisv9.ZRangeBy{Min: "(" + strconv.FormatInt(last, 10), Max: "+inf"}).Result()
	if err != nil {
		return r.readError(series, err)
	}
	for _, raw := range values {
		if err := r.process(ctx, series, raw, marketdata.EventOriginLive, true); err != nil {
			return err
		}
	}
	for _, timestamp := range r.takeDirty(series) {
		values, err := r.client.ZRangeByScore(ctx, series.Key(), &redisv9.ZRangeBy{Min: strconv.FormatInt(timestamp, 10), Max: strconv.FormatInt(timestamp, 10)}).Result()
		if err != nil {
			return r.readError(series, err)
		}
		for _, raw := range values {
			if err := r.process(ctx, series, raw, marketdata.EventOriginLive, true); err != nil {
				return err
			}
		}
	}
	r.health.MarkRecovery(time.Now())
	return nil
}

func (r *Runtime) process(ctx context.Context, series Series, raw string, origin marketdata.EventOrigin, recovered bool) error {
	event, err := DecodeBar(raw, series)
	if err != nil {
		return r.readError(series, err)
	}
	event.Origin = origin
	result, err := r.dispatch(ctx, event)
	if err != nil {
		return fmt.Errorf("redis: dispatch %s at %d: %w", series, event.Candle.Time, err)
	}
	r.metrics.Count(MetricBarRead, 1)
	if recovered {
		r.metrics.Count(MetricRecoveredBar, 1)
	}
	switch result {
	case marketdata.AppendDuplicate:
		r.metrics.Count(MetricDuplicateBar, 1)
	case marketdata.AppendConflict:
		r.metrics.Count(MetricConflictBar, 1)
	}
	r.advance(series, event.Candle.Time)
	r.health.MarkBarRead(time.Now())
	return nil
}

func (r *Runtime) readError(series Series, err error) error {
	r.metrics.Count(MetricBarReadError, 1)
	r.health.MarkError(err, time.Now())
	return fmt.Errorf("redis: read %s: %w", series, err)
}

func (r *Runtime) cursor(series Series) int64 {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.cursors[series]
}
func (r *Runtime) advance(series Series, timestamp int64) {
	r.mu.Lock()
	if timestamp > r.cursors[series] {
		r.cursors[series] = timestamp
	}
	r.mu.Unlock()
}
func (r *Runtime) takeDirty(series Series) []int64 {
	r.mu.Lock()
	defer r.mu.Unlock()
	set := r.dirty[series]
	delete(r.dirty, series)
	values := make([]int64, 0, len(set))
	for timestamp := range set {
		values = append(values, timestamp)
	}
	sort.Slice(values, func(i, j int) bool { return values[i] < values[j] })
	return values
}

// ReadSpot exposes the existing transient price:{SYMBOL}:spot state without
// introducing Kafka ticks. It is intentionally not part of candle structure.
func (r *Runtime) ReadSpot(ctx context.Context, symbol market.Symbol) (SpotDTO, error) {
	canonical := market.Symbol(strings.ToUpper(string(symbol)))
	raw, err := r.client.Get(ctx, "price:"+string(canonical)+":spot").Result()
	if err != nil {
		return SpotDTO{}, fmt.Errorf("redis: read spot %s: %w", canonical, err)
	}
	decoder := json.NewDecoder(strings.NewReader(raw))
	decoder.DisallowUnknownFields()
	var spot SpotDTO
	if err := decoder.Decode(&spot); err != nil {
		return SpotDTO{}, fmt.Errorf("redis: decode spot %s: %w", canonical, err)
	}
	if err := spot.Validate(); err != nil {
		return SpotDTO{}, err
	}
	return spot, nil
}

func waitContext(ctx context.Context, duration time.Duration) bool {
	select {
	case <-ctx.Done():
		return false
	case <-time.After(duration):
		return true
	}
}
