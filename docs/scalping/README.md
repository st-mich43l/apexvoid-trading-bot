# Scalping

Scalping is a separate execution-policy lane in Algo Bot. Analysis Engine
supplies the technical opportunity; Algo Bot owns scalping mode, entry
location, sizing, exposure, and TradePlan publication.

The lane consumes closed-bar context through the shared bar-event dispatcher
and records outcome, risk, and telemetry data in the normal persistence path.
It does not create a competing technical-analysis authority.

Current operational references:

- [Controlled live operation](CONTROLLED_LIVE.md)
- [MAD context](MAD.md)
- [MAD sources](MAD_SOURCES.md)
- [Own breakout technique](OWN_BREAKOUT_TECHNIQUE.md)
- [Own scalp mechanism](OWN_SCALP_MECHANISM.md)
- [XAU fixed-RR policy](XAU_STRUCTURE_FIXED_RR.md)
