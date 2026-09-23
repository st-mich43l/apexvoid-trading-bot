// Package session owns UTC session windows (Asia/London/NY), prior-day
// (PDH/PDL) and prior-week (PWH/PWL) extreme levels, their sweep status,
// and which session is active as of the latest closed bar.
//
// Ported from algo-bot/app/analysis/session_liquidity.py
// (session_levels/previous_week_levels and their private helpers), per
// Phase S4's plan (apexvoid-bot-prompts/rebuild-strategies.md §23-26) and
// docs/analysis/shared-primitives-v2.md. Production combines
// session_levels(df, cfg) (Asia/London/NY extremes + PDH/PDL) with
// previous_week_levels(df) (PWH/PWL) into one flat list before scoring
// (algo-bot/app/analysis/engine.py::_analyze_tf) — this package's Update
// does the same in one pass, per timeframe, matching every other domain
// package's pure Update(candles, ...) contract (structure.Update,
// liquidity.Update, zone.Update). One difference from Python: production
// computes PWH/PWL once per symbol from a single representative
// timeframe's frame (engine.py::_weekly_session_levels), not once per
// timeframe. This package computes it per timeframe instead, consistent
// with every other field on SessionState and with how every other Go
// domain package in this codebase is structured — a higher timeframe
// with less history simply produces fewer/no PWH/PWL levels, the same
// honest behavior Python's own single-frame call would have if that
// frame lacked two weeks of history.
//
// The active-session classifier (SessionState.Active, naming which of
// ASIA/LONDON/NY contains the latest closed bar) has no Python
// equivalent — session_liquidity.py never classifies "now," only past
// session extremes. It is new logic here, added because
// context.SessionContext{Name string} has been an honest empty
// placeholder since Phase S3's own foundation task specifically pending
// this work (see context/market.go's SessionContext doc comment, updated
// alongside this package).
//
// Dependency rule: session depends on nothing but market — it needs no
// structure.Swing/StructureBreak (session_liquidity.py itself never
// imports swings.py or structure.py), so it sits at rank 1 alongside
// indicator/marketdata/config, not rank 3 with zone/liquidity/fib/
// keylevel/trendline — see docs/architecture/dependency-rules.md's fifth
// amendment.
package session
