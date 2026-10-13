// Package structuralconfluence decides where genuinely independent structures
// support the same price, and when each of them became known.
//
// It is the pure geometry and provenance layer under Confluence Zone v3. It
// owns no strategy thesis, reads no configuration and never looks at a
// reaction: a Fact is a structure that exists independently of any candle that
// later reacts to it. Three questions are answered here, each deterministically
// and independent of input order:
//
//   - which facts are the same underlying event (provenance atoms, union-find);
//   - where, in price, at least the required number of independent facts
//     simultaneously support (an exact interval sweep over the facts' bounds);
//   - which facts were already confirmable at a given instant (closed-candle
//     availability), so a structure created by the very candle that reacts, or
//     later, can never be counted retroactively.
//
// See docs/strategies/confluence-zone-v3-technical-spec.md.
package structuralconfluence
