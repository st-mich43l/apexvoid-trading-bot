# Range Edge Strategy V2

`range_edge` builds causal M5 support/resistance barriers from clustered touch
episodes. It tracks wick-rejection history, consecutive accepted closes and
last-touch age, selects a valid two-sided range, and widens only the touch
window to reach the established barrier while keeping confirmation recent. A
Grade-A grab may satisfy the legacy touch/wick exception. The first target is
equilibrium and the second is the opposing edge. Accepted breaks, stale or
weak barriers, insufficient room, or missing shared reaction reject.
