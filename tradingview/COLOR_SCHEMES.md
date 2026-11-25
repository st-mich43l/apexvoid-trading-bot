# ApexVoid colour schemes

Colour guide for `apexvoid_price_action.pine` (v2.27): the indicator's built-in schemes and the chart (candle and background) settings that go with them. Every value here is also in the Pine source (`f_palette`).

## Choosing a scheme

`06 Visuals & Performance`:

- **Colour scheme**: ApexVoid (default), Classic, Colorblind safe, Ocean, Muted, or Custom. Custom uses the per-category colour inputs (bullish, bearish, FVG, demand, supply, sessions, liquidity, key level, volume profile) exactly as set. Those inputs do nothing with any other scheme, so a chart that used customised colours before v2.27 needs `Custom` to keep them.
- **Chart theme**: Auto (default) reads the chart background and picks the dark or light variant of the scheme; Dark / Light force one (use it when a gradient or unusual background is misread).

Every scheme has a **dark-chart** and a **light-chart** variant. Each colour is at least **4.5:1** (WCAG AA for normal text) against `#131722` (TradingView dark) or `#FFFFFF` (white), because these colours are also used as caption text. Contrast against pure black is listed too. Caption backgrounds, outlines and neutral text follow the theme in every scheme, Custom included:

| Theme neutral | Dark | Light | Used for |
| --- | --- | --- | --- |
| Primary text | `#CBD5E1` (12.1:1) | `#1E293B` (14.6:1) | key-level / volume-profile / pool rows on the right-edge rail |
| Secondary text | `#94A3B8` (7.0:1) | `#475569` (7.6:1) | tier-3 neutral rows, connectors |
| Label background | `#161923` at 18 % transparency | `#F1F5F9` at 8 % | right-edge rail |
| Text outline | `#090D18` | `#FFFFFF` | BOS / CHoCH / event captions, MAJOR HIGH / LOW |

The Legacy (v2.26) right-edge column keeps its v2.26 dark label background in both themes.

## Indicator palettes

### ApexVoid

The house look and the default. Dark is exactly the v2.26 palette: teal / rose structure, blue / amber gaps, violet Asia.

| Role | Dark chart | vs #131722 | vs black | Light chart | vs white |
| --- | --- | --- | --- | --- | --- |
| Bullish | `#00C7AE` | 8.3 | 9.8 | `#00796B` | 5.3 |
| Bearish | `#F26B85` | 6.2 | 7.2 | `#C2294A` | 5.7 |
| Bullish FVG | `#5194FF` | 6.0 | 7.0 | `#1D5FD1` | 5.8 |
| Bearish FVG | `#FFA957` | 9.4 | 11.1 | `#A85400` | 5.3 |
| Demand | `#36D399` | 9.3 | 10.9 | `#12784A` | 5.5 |
| Supply | `#FF7892` | 7.1 | 8.4 | `#B8245A` | 6.1 |
| Asia | `#B48AFF` | 6.8 | 8.0 | `#6B3FD4` | 6.4 |
| London | `#5AA9E6` | 7.0 | 8.3 | `#1C6AA8` | 5.7 |
| New York | `#E6B84A` | 9.6 | 11.3 | `#805E00` | 6.0 |
| Liquidity | `#AEB8CC` | 9.0 | 10.5 | `#525C6E` | 6.7 |
| Key level | `#8A93A6` | 5.8 | 6.8 | `#5A6273` | 6.1 |
| Volume profile | `#8A93A6` | 5.8 | 6.8 | `#5A6273` | 6.1 |

### Classic

TradingView-style green / red with Material accents, for traders used to the platform defaults.

| Role | Dark chart | vs #131722 | vs black | Light chart | vs white |
| --- | --- | --- | --- | --- | --- |
| Bullish | `#26A69A` | 6.0 | 7.0 | `#00796B` | 5.3 |
| Bearish | `#EF5350` | 5.1 | 6.0 | `#C62828` | 5.6 |
| Bullish FVG | `#42A5F5` | 6.8 | 7.9 | `#1565C0` | 5.7 |
| Bearish FVG | `#FFB74D` | 10.3 | 12.1 | `#A04A00` | 6.0 |
| Demand | `#66BB6A` | 7.6 | 8.9 | `#2E7D32` | 5.1 |
| Supply | `#E57373` | 6.0 | 7.0 | `#AD1457` | 7.0 |
| Asia | `#BA68C8` | 5.0 | 5.9 | `#7B1FA2` | 8.2 |
| London | `#4FC3F7` | 8.9 | 10.5 | `#0B6A9E` | 5.9 |
| New York | `#FFD54F` | 12.7 | 14.9 | `#7A6000` | 6.0 |
| Liquidity | `#B0BEC5` | 9.4 | 11.0 | `#546E7A` | 5.4 |
| Key level | `#90A4AE` | 6.9 | 8.1 | `#56707C` | 5.2 |
| Volume profile | `#90A4AE` | 6.9 | 8.1 | `#56707C` | 5.2 |

### Colorblind safe

Okabe-Ito hues: bullish blue and bearish orange instead of green / red, so direction never depends on red-green vision. Demand is bluish green, supply vermilion.

| Role | Dark chart | vs #131722 | vs black | Light chart | vs white |
| --- | --- | --- | --- | --- | --- |
| Bullish | `#56B4E9` | 7.8 | 9.1 | `#0067A3` | 6.1 |
| Bearish | `#E69F00` | 7.9 | 9.3 | `#A35A00` | 5.2 |
| Bullish FVG | `#9AD0F5` | 10.8 | 12.7 | `#2B6CB0` | 5.4 |
| Bearish FVG | `#F0E442` | 13.5 | 15.9 | `#6E6400` | 6.0 |
| Demand | `#2FBF9B` | 7.7 | 9.0 | `#00745A` | 5.8 |
| Supply | `#F08A4B` | 7.2 | 8.4 | `#B34A00` | 5.4 |
| Asia | `#CC79A7` | 5.8 | 6.9 | `#99446F` | 6.2 |
| London | `#7FB8DE` | 8.4 | 9.8 | `#2D6E99` | 5.5 |
| New York | `#D9C25A` | 10.1 | 11.8 | `#73661A` | 5.8 |
| Liquidity | `#AEB8CC` | 9.0 | 10.5 | `#525C6E` | 6.7 |
| Key level | `#8A93A6` | 5.8 | 6.8 | `#5A6273` | 6.1 |
| Volume profile | `#8A93A6` | 5.8 | 6.8 | `#5A6273` | 6.1 |

### Ocean

Cooler and brighter: aqua / pink structure, sky / amber gaps. Good on very dark backgrounds and on projectors.

| Role | Dark chart | vs #131722 | vs black | Light chart | vs white |
| --- | --- | --- | --- | --- | --- |
| Bullish | `#4FD1C5` | 9.6 | 11.3 | `#0F766E` | 5.5 |
| Bearish | `#F687B3` | 7.7 | 9.0 | `#B83280` | 5.5 |
| Bullish FVG | `#63B3ED` | 7.8 | 9.2 | `#2563A8` | 6.1 |
| Bearish FVG | `#F6AD55` | 9.4 | 11.0 | `#A0520B` | 5.7 |
| Demand | `#68D391` | 9.6 | 11.3 | `#25784A` | 5.4 |
| Supply | `#FC8181` | 7.3 | 8.6 | `#C53030` | 5.5 |
| Asia | `#B794F4` | 7.3 | 8.6 | `#6B46C1` | 6.4 |
| London | `#76E4F7` | 12.1 | 14.2 | `#0E7490` | 5.4 |
| New York | `#F6E05E` | 13.4 | 15.7 | `#7A6500` | 5.7 |
| Liquidity | `#A0AEC0` | 7.9 | 9.3 | `#4A5568` | 7.5 |
| Key level | `#8A9BB0` | 6.3 | 7.4 | `#55657A` | 6.0 |
| Volume profile | `#8A9BB0` | 6.3 | 7.4 | `#55657A` | 6.0 |

### Muted

Low saturation for long sessions and dense charts; everything recedes a step so candles lead.

| Role | Dark chart | vs #131722 | vs black | Light chart | vs white |
| --- | --- | --- | --- | --- | --- |
| Bullish | `#6FBFA6` | 8.2 | 9.7 | `#3E7D6B` | 4.8 |
| Bearish | `#D98A94` | 6.8 | 8.0 | `#9E4A57` | 5.9 |
| Bullish FVG | `#8EA9D8` | 7.5 | 8.8 | `#47669C` | 5.8 |
| Bearish FVG | `#D9B38C` | 9.2 | 10.8 | `#8A6033` | 5.5 |
| Demand | `#7FC8A0` | 9.1 | 10.7 | `#3F7A58` | 5.1 |
| Supply | `#E0A0AA` | 8.3 | 9.8 | `#A24E5C` | 5.6 |
| Asia | `#A99BD6` | 7.1 | 8.3 | `#62559A` | 6.4 |
| London | `#8FB3D9` | 8.2 | 9.6 | `#486F96` | 5.3 |
| New York | `#D6BE8A` | 9.9 | 11.6 | `#7A6536` | 5.6 |
| Liquidity | `#A3ACBA` | 7.8 | 9.2 | `#5B6474` | 6.0 |
| Key level | `#8A93A6` | 5.8 | 6.8 | `#5F6777` | 5.7 |
| Volume profile | `#8A93A6` | 5.8 | 6.8 | `#5F6777` | 5.7 |

## Chart colours that match

The indicator draws structure, zones and levels in strong category colours; the candles should be **one step quieter** so zones, captions and the right-edge rail stay readable on top of them. The rules behind the values below:

- Up / down candles take the hue of the scheme's bullish / bearish colour but less saturated, so a candle is never mistaken for a zone or a caption.
- No orange, gold or violet candles: those hues are the bearish FVG, New York and Asia colours (an orange up candle inside an orange FVG box disappears).
- Candle colours keep at least 3:1 against the background (WCAG for graphical objects), and up and down differ in hue and lightness, so direction still reads in greyscale.
- Borders and wicks use the body colour; one colour per direction keeps small candles crisp on M1-M15.
- Grid lines are almost invisible: zones and levels already give the chart its horizontal structure.

### Background and canvas

| Setting | Dark | Light |
| --- | --- | --- |
| Background (solid) | `#0F1117` | `#FFFFFF` |
| Grid lines (vertical and horizontal) | `#1A1E27`, or off | `#EEF0F3`, or off |
| Scale text | `#8A93A6` | `#5A6273` |
| Scale lines | `#232836` | `#D9DDE3` |
| Crosshair | `#5A6273`, dashed | `#94A3B8`, dashed |
| Watermark | off | off |

`#0F1117` is slightly darker than TradingView's `#131722` so translucent zone fills keep their colour; every dark palette colour is at least 4.5:1 against it as well. The light background is plain white; avoid tinted light backgrounds, the light palettes were checked against white.

### Candles

| Scheme | Dark: up | Dark: down | Light: up | Light: down |
| --- | --- | --- | --- | --- |
| ApexVoid | `#4FA89B` (6.7) | `#C96A7C` (5.2) | `#2F8C7F` (4.1) | `#B4566A` (4.7) |
| Classic | `#3D9A8F` (5.6) | `#D2575A` (4.7) | `#1E8A7D` (4.2) | `#C24A4D` (4.8) |
| Colorblind safe | `#4D9FD1` (6.5) | `#D08A1F` (6.6) | `#2B77AB` (4.9) | `#B06A10` (4.3) |
| Ocean | `#4BB3AA` (7.5) | `#D27C9F` (6.4) | `#1E7F77` (4.8) | `#B04A7A` (5.1) |
| Muted | `#7FA99C` (7.2) | `#B98790` (6.2) | `#5E8C7E` (3.8) | `#9C6871` (4.5) |
| Neutral (any scheme) | `#C9CED8` (12.0) | `#6B7383` (4.0) | hollow: body `#FFFFFF`, border and wick `#64748B` (4.8) | `#475569` (7.6) |

Contrast in brackets is against the background above (`#0F1117` dark, `#FFFFFF` light). Use the same colour for body, border and wick of each direction.

**Neutral candles** (graphite up, slate down) suit the busiest charts: with every zone, level and caption in colour, grey candles leave the colour to the indicator. Direction stays clear from lightness (and on the light theme from hollow vs filled).

### Applying them in TradingView

1. Right-click the chart → **Settings** (the menu names are from memory of the TradingView UI and were not checked in this repository).
2. **Symbol**: Body, Borders and Wick: set the up and down colours from the candle table; tick all three.
3. **Canvas** (older versions: Appearance): Background solid colour, Grid lines, Scale text and lines, Crosshair from the background table.
4. In the indicator: `Colour scheme` to the matching scheme, `Chart theme` Auto.
5. Optional: save it as a chart template (**Template** → **Save chart template**) so each scheme and theme pair is one click.

## Contrast method

WCAG 2.x relative luminance and contrast ratio, `(L1 + 0.05) / (L2 + 0.05)`. Targets: 4.5:1 for colours that can be caption text, 3:1 for candles and lines. The values were computed from the hex codes, not measured on screen. Translucent fills (zones at 92 % transparency, session boxes at 93 %) are tints and are not text; their colour is the role's.
