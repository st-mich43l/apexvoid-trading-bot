# Snap-Back Strategy V2

`snap_back` is a structural reversal after M5 price extends by configured ATR
from the latest impulse swing (or the configured zone source). It selects the
best valid canonical demand/supply zone, with a valid-side key-level fallback,
then requires premium/discount location, an A/B liquidity grab, and the shared
closed-bar structural reaction. The candidate carries the source ID, grab
grade, reaction type and timestamps. Under-extension, EQ/wrong-side location,
missing grab, or missing reaction rejects.
