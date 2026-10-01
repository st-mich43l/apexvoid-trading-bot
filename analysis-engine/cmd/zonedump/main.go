// Throwaway diagnostic (not committed): dump the engine's M5 zones at chosen closes.
package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"

	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/config"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/engine"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/market"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/marketdata"
	"github.com/st-mich43l/apexvoid-trading-bot/analysis-engine/internal/replaycapture"
)

func main() {
	doc, _ := config.ResolveDocument(os.Args[1])
	capture, err := replaycapture.Load(os.Args[2])
	if err != nil {
		panic(err)
	}
	var stops []int64
	for _, a := range os.Args[3:] {
		v, _ := strconv.ParseInt(a, 10, 64)
		stops = append(stops, v)
	}
	settings, _ := engine.LoadSettings(doc, market.M5, false)
	_ = engine.ApplyInstrument(&settings, doc, string(capture.Symbol))
	byTF := map[market.Timeframe][]market.Candle{}
	for name := range capture.Timeframes {
		c, _ := capture.Candles(market.Timeframe(name))
		byTF[market.Timeframe(name)] = c
	}
	events, _ := replaycapture.Events(capture.Symbol, byTF, marketdata.EventOriginReplay)
	e := engine.NewEngine(nil)
	_ = e.Register(capture.Symbol, settings)
	type z struct {
		Kind, Side, State string
		Low, High         float64
		Created           int64
		Strength          float64
	}
	out := map[string][]z{}
	next := 0
	for _, ev := range events {
		snap, err := e.Dispatch(ev)
		if err != nil {
			panic(err)
		}
		if ev.Timeframe == market.M5 && next < len(stops) && ev.Candle.Time+300 >= stops[next] {
			var zs []z
			for _, zz := range snap.Zones[market.M5].Zones {
				zs = append(zs, z{zz.Kind.String(), zz.Side.String(), zz.State.String(), float64(zz.Low), float64(zz.High), zz.CreatedAt, zz.Strength})
			}
			out[strconv.FormatInt(stops[next], 10)] = zs
			next++
		}
	}
	b, _ := json.Marshal(out)
	fmt.Println(string(b))
}
