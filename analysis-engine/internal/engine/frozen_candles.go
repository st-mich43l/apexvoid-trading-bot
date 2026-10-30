package engine

// WithFrozenSessionWindows makes every frame compute its session levels (PDH/PDL, session extremes, PWH/PWL) from its detection window (150 M5, 250 M15,
// 400 H1 bars), as the frozen Python analysis did. In the live engine they come from the frame's whole in-memory history, so yesterday's extremes are
// yesterday's, not the part of yesterday the window still holds.
func WithFrozenSessionWindows(settings *Settings) { settings.LegacyRead.FrozenSessionWindows = true }

// WithFrozenBinding makes the strategies that find a structure on a higher frame (fvg, order_block, supply) drop a structure whose origin precedes the M5
// execution window, as the frozen analysis did. In the live engine such a structure stays tradable for as long as its own frame holds it.
func WithFrozenBinding(settings *Settings) {
	for i := range settings.Strategies {
		switch settings.Strategies[i].ID {
		case "fvg", "order_block", "supply":
			params := make(map[string]any, len(settings.Strategies[i].Parameters)+1)
			for k, v := range settings.Strategies[i].Parameters {
				params[k] = v
			}
			params["bind_within_execution_window"] = true
			settings.Strategies[i].Parameters = params
		}
	}
}

// WithFrozenWindows is both of the above: the candle windows of the frozen Python analysis. The frozen-Python parity replays use it
// (replaycapture.Options.WithoutStopBudget implies it).
func WithFrozenWindows(settings *Settings) {
	WithFrozenSessionWindows(settings)
	WithFrozenBinding(settings)
}
