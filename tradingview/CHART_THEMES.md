# TradingView chart colour packs

14 complete chart looks for `apexvoid_price_action.pine` v2.27: the TradingView chart settings (canvas and candles) plus the indicator settings that go with them. Each pack was checked for contrast on its own background, so the indicator's captions stay readable. The indicator palettes themselves are in [`COLOR_SCHEMES.md`](COLOR_SCHEMES.md).

## Packs at a glance

| # | Pack | Theme | Background | Colour scheme | Candle colours | Mood |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | [ApexVoid Midnight](#1-apexvoid-midnight) | dark | `#0F1117` | ApexVoid | Scheme | The house look: near-black with the default palette. Start here. |
| 2 | [Obsidian](#2-obsidian) | dark | `#000000` | Classic | Scheme | Pure black for OLED screens and dark rooms, platform-style green / red. |
| 3 | [Graphite Pro](#3-graphite-pro) | dark | `#121417` | Muted | Neutral | Grey candles, soft palette: the indicator carries all the colour. Best on dense M5 / M15 charts. |
| 4 | [Deep Ocean](#4-deep-ocean) | dark | `#0A121C` | Ocean | Scheme | Navy background with aqua / pink structure; calm for long sessions. |
| 5 | [Night Owl Focus](#5-night-owl-focus) | dark | `#0D1016` | Muted | Structure bias | Only with-bias candles carry colour, so the external bias reads at a glance. |
| 6 | [Colorblind Night](#6-colorblind-night) | dark | `#101318` | Colorblind safe | Scheme | Blue / orange direction, no red-green dependency. |
| 7 | [Nord Aurora](#7-nord-aurora) | dark | `#11151C` | Ocean | Scheme | Cool Nordic greys with frosted green / rose candles. |
| 8 | [Carbon Teal](#8-carbon-teal) | dark | `#0B0D10` | ApexVoid | Structure bias | Very dark carbon with brighter teal / rose with-bias candles for momentum reading. |
| 9 | [Paper White](#9-paper-white) | light | `#FFFFFF` | ApexVoid | Scheme | The light twin of ApexVoid Midnight; printing and daylight. |
| 10 | [Daylight Classic](#10-daylight-classic) | light | `#FFFFFF` | Classic | Scheme | Platform-style green / red on white. |
| 11 | [Porcelain](#11-porcelain) | light | `#FBFBFC` | Muted | Neutral | Soft off-white with grey candles; the quietest light pack. |
| 12 | [Arctic](#12-arctic) | light | `#FAFCFE` | Ocean | Scheme | Cool white with a blue tint, matching Deep Ocean. |
| 13 | [Colorblind Day](#13-colorblind-day) | light | `#FFFFFF` | Colorblind safe | Scheme | Blue / orange on white. |
| 14 | [Presentation](#14-presentation) | light | `#FFFFFF` | Classic | Structure bias | High-contrast white for screenshots, slides and screen sharing; colour only on with-bias candles. |

## How to apply a pack

The menu names are from memory of the TradingView UI and were not checked in this repository.

1. Right-click the chart → **Settings** → **Canvas** (older versions: Appearance): set Background (solid), Grid lines, Scales text and lines, Crosshair, and switch the watermark off.
2. **Symbol**: set Body, Borders and Wick, up and down, from the pack. The indicator recolours the candles itself while `Candle colours` is not Off; the Symbol colours are what you see with the indicator hidden or removed, so keep them the same.
3. Indicator settings, `06 Visuals & Performance`: `Colour scheme`, `Chart theme` = Auto, `Candle colours`, and `Up candle colour` / `Down candle colour` only where the pack lists an override (leave them at their defaults otherwise, so they follow the scheme).
4. Save it: chart **Settings** → **Template** → **Save as**, named after the pack, and save the indicator settings as an indicator template (Defaults → Save as). Switching packs is then two clicks.

Packs keep the indicator's default per-category colours. If you changed one, it overrides the scheme in every pack; reset it to its default to let the pack's scheme apply.

## 1. ApexVoid Midnight

The house look: near-black with the default palette. Start here.

| Where | Setting | Value |
| --- | --- | --- |
| Canvas | Background (solid) | `#0F1117` |
| Canvas | Grid lines, vertical and horizontal | `#1A1E27` |
| Canvas | Scales text | `#8A93A6` |
| Canvas | Scales lines | `#232836` |
| Canvas | Crosshair (dashed) | `#5A6273` |
| Canvas | Watermark | off |
| Symbol | Body / Borders / Wick, up | `#4FA89B` |
| Symbol | Body / Borders / Wick, down | `#C96A7C` |
| Indicator | Colour scheme | ApexVoid |
| Indicator | Chart theme | Auto |
| Indicator | Candle colours | Scheme |
| Indicator | Up / Down candle colour | defaults (follow the scheme) |

Contrast on `#0F1117`: indicator colours at least 6.1:1, candles at least 5.2:1, scale text 6.1:1, grid 1.13:1 (deliberately faint).

## 2. Obsidian

Pure black for OLED screens and dark rooms, platform-style green / red.

| Where | Setting | Value |
| --- | --- | --- |
| Canvas | Background (solid) | `#000000` |
| Canvas | Grid lines, vertical and horizontal | `#14161C` |
| Canvas | Scales text | `#8A93A6` |
| Canvas | Scales lines | `#1F232C` |
| Canvas | Crosshair (dashed) | `#5A6273` |
| Canvas | Watermark | off |
| Symbol | Body / Borders / Wick, up | `#3D9A8F` |
| Symbol | Body / Borders / Wick, down | `#D2575A` |
| Indicator | Colour scheme | Classic |
| Indicator | Chart theme | Auto |
| Indicator | Candle colours | Scheme |
| Indicator | Up / Down candle colour | defaults (follow the scheme) |

Contrast on `#000000`: indicator colours at least 5.9:1, candles at least 5.3:1, scale text 6.8:1, grid 1.16:1 (deliberately faint).

## 3. Graphite Pro

Grey candles, soft palette: the indicator carries all the colour. Best on dense M5 / M15 charts.

| Where | Setting | Value |
| --- | --- | --- |
| Canvas | Background (solid) | `#121417` |
| Canvas | Grid lines, vertical and horizontal | `#1C1F24` |
| Canvas | Scales text | `#9AA3B2` |
| Canvas | Scales lines | `#2A2E35` |
| Canvas | Crosshair (dashed) | `#6B7383` |
| Canvas | Watermark | off |
| Symbol | Body / Borders / Wick, up | `#C9CED8` |
| Symbol | Body / Borders / Wick, down | `#6B7383` |
| Indicator | Colour scheme | Muted |
| Indicator | Chart theme | Auto |
| Indicator | Candle colours | Neutral |
| Indicator | Up / Down candle colour | defaults (follow the scheme) |

Contrast on `#121417`: indicator colours at least 6.0:1, candles at least 3.9:1, scale text 7.3:1, grid 1.12:1 (deliberately faint).

## 4. Deep Ocean

Navy background with aqua / pink structure; calm for long sessions.

| Where | Setting | Value |
| --- | --- | --- |
| Canvas | Background (solid) | `#0A121C` |
| Canvas | Grid lines, vertical and horizontal | `#122030` |
| Canvas | Scales text | `#8FA3BC` |
| Canvas | Scales lines | `#1B2B3D` |
| Canvas | Crosshair (dashed) | `#4F6B8A` |
| Canvas | Watermark | off |
| Symbol | Body / Borders / Wick, up | `#4BB3AA` |
| Symbol | Body / Borders / Wick, down | `#D27C9F` |
| Indicator | Colour scheme | Ocean |
| Indicator | Chart theme | Auto |
| Indicator | Candle colours | Scheme |
| Indicator | Up / Down candle colour | defaults (follow the scheme) |

Contrast on `#0A121C`: indicator colours at least 6.6:1, candles at least 6.4:1, scale text 7.3:1, grid 1.14:1 (deliberately faint).

## 5. Night Owl Focus

Only with-bias candles carry colour, so the external bias reads at a glance.

| Where | Setting | Value |
| --- | --- | --- |
| Canvas | Background (solid) | `#0D1016` |
| Canvas | Grid lines, vertical and horizontal | `#171B23` |
| Canvas | Scales text | `#8A93A6` |
| Canvas | Scales lines | `#222733` |
| Canvas | Crosshair (dashed) | `#5A6273` |
| Canvas | Watermark | off |
| Symbol | Body / Borders / Wick, up | `#7FA99C` |
| Symbol | Body / Borders / Wick, down | `#B98790` |
| Indicator | Colour scheme | Muted |
| Indicator | Chart theme | Auto |
| Indicator | Candle colours | Structure bias |
| Indicator | Up / Down candle colour | defaults (follow the scheme) |

Contrast on `#0D1016`: indicator colours at least 6.2:1, candles at least 6.3:1; counter-bias greys `#8A93A6` 6.2:1, `#5A6273` 3.1:1, scale text 6.2:1, grid 1.10:1 (deliberately faint).

## 6. Colorblind Night

Blue / orange direction, no red-green dependency.

| Where | Setting | Value |
| --- | --- | --- |
| Canvas | Background (solid) | `#101318` |
| Canvas | Grid lines, vertical and horizontal | `#1A1E26` |
| Canvas | Scales text | `#9AA3B2` |
| Canvas | Scales lines | `#252A33` |
| Canvas | Crosshair (dashed) | `#5A6273` |
| Canvas | Watermark | off |
| Symbol | Body / Borders / Wick, up | `#4D9FD1` |
| Symbol | Body / Borders / Wick, down | `#D08A1F` |
| Indicator | Colour scheme | Colorblind safe |
| Indicator | Chart theme | Auto |
| Indicator | Candle colours | Scheme |
| Indicator | Up / Down candle colour | defaults (follow the scheme) |

Contrast on `#101318`: indicator colours at least 6.0:1, candles at least 6.4:1, scale text 7.3:1, grid 1.11:1 (deliberately faint).

## 7. Nord Aurora

Cool Nordic greys with frosted green / rose candles.

| Where | Setting | Value |
| --- | --- | --- |
| Canvas | Background (solid) | `#11151C` |
| Canvas | Grid lines, vertical and horizontal | `#1B212B` |
| Canvas | Scales text | `#8C9AB0` |
| Canvas | Scales lines | `#262D39` |
| Canvas | Crosshair (dashed) | `#5E6B80` |
| Canvas | Watermark | off |
| Symbol | Body / Borders / Wick, up | `#7FB5A8` |
| Symbol | Body / Borders / Wick, down | `#C98A93` |
| Indicator | Colour scheme | Ocean |
| Indicator | Chart theme | Auto |
| Indicator | Candle colours | Scheme |
| Indicator | Up candle colour | `#7FB5A8` (override) |
| Indicator | Down candle colour | `#C98A93` (override) |

Contrast on `#11151C`: indicator colours at least 6.4:1, candles at least 6.6:1, scale text 6.4:1, grid 1.13:1 (deliberately faint).

## 8. Carbon Teal

Very dark carbon with brighter teal / rose with-bias candles for momentum reading.

| Where | Setting | Value |
| --- | --- | --- |
| Canvas | Background (solid) | `#0B0D10` |
| Canvas | Grid lines, vertical and horizontal | `#16191E` |
| Canvas | Scales text | `#8A93A6` |
| Canvas | Scales lines | `#20242B` |
| Canvas | Crosshair (dashed) | `#55606F` |
| Canvas | Watermark | off |
| Symbol | Body / Borders / Wick, up | `#3FB8A6` |
| Symbol | Body / Borders / Wick, down | `#E07A8E` |
| Indicator | Colour scheme | ApexVoid |
| Indicator | Chart theme | Auto |
| Indicator | Candle colours | Structure bias |
| Indicator | Up candle colour | `#3FB8A6` (override) |
| Indicator | Down candle colour | `#E07A8E` (override) |

Contrast on `#0B0D10`: indicator colours at least 6.3:1, candles at least 6.8:1; counter-bias greys `#8A93A6` 6.3:1, `#5A6273` 3.2:1, scale text 6.3:1, grid 1.10:1 (deliberately faint).

## 9. Paper White

The light twin of ApexVoid Midnight; printing and daylight.

| Where | Setting | Value |
| --- | --- | --- |
| Canvas | Background (solid) | `#FFFFFF` |
| Canvas | Grid lines, vertical and horizontal | `#EEF0F3` |
| Canvas | Scales text | `#5A6273` |
| Canvas | Scales lines | `#D9DDE3` |
| Canvas | Crosshair (dashed) | `#94A3B8` |
| Canvas | Watermark | off |
| Symbol | Body / Borders / Wick, up | `#2F8C7F` |
| Symbol | Body / Borders / Wick, down | `#B4566A` |
| Indicator | Colour scheme | ApexVoid |
| Indicator | Chart theme | Auto |
| Indicator | Candle colours | Scheme |
| Indicator | Up / Down candle colour | defaults (follow the scheme) |

Contrast on `#FFFFFF`: indicator colours at least 5.3:1, candles at least 4.1:1, scale text 6.1:1, grid 1.14:1 (deliberately faint).

## 10. Daylight Classic

Platform-style green / red on white.

| Where | Setting | Value |
| --- | --- | --- |
| Canvas | Background (solid) | `#FFFFFF` |
| Canvas | Grid lines, vertical and horizontal | `#F0F2F5` |
| Canvas | Scales text | `#5A6273` |
| Canvas | Scales lines | `#DADFE6` |
| Canvas | Crosshair (dashed) | `#94A3B8` |
| Canvas | Watermark | off |
| Symbol | Body / Borders / Wick, up | `#1E8A7D` |
| Symbol | Body / Borders / Wick, down | `#C24A4D` |
| Indicator | Colour scheme | Classic |
| Indicator | Chart theme | Auto |
| Indicator | Candle colours | Scheme |
| Indicator | Up / Down candle colour | defaults (follow the scheme) |

Contrast on `#FFFFFF`: indicator colours at least 5.1:1, candles at least 4.2:1, scale text 6.1:1, grid 1.12:1 (deliberately faint).

## 11. Porcelain

Soft off-white with grey candles; the quietest light pack.

| Where | Setting | Value |
| --- | --- | --- |
| Canvas | Background (solid) | `#FBFBFC` |
| Canvas | Grid lines, vertical and horizontal | `#EEF0F2` |
| Canvas | Scales text | `#5F6777` |
| Canvas | Scales lines | `#DDE0E5` |
| Canvas | Crosshair (dashed) | `#A3ACBA` |
| Canvas | Watermark | off |
| Symbol | Body / Borders / Wick, up | `#7C8799` |
| Symbol | Body / Borders / Wick, down | `#334155` |
| Indicator | Colour scheme | Muted |
| Indicator | Chart theme | Auto |
| Indicator | Candle colours | Neutral |
| Indicator | Up / Down candle colour | defaults (follow the scheme) |

Contrast on `#FBFBFC`: indicator colours at least 4.7:1, candles at least 3.5:1, scale text 5.5:1, grid 1.10:1 (deliberately faint).

## 12. Arctic

Cool white with a blue tint, matching Deep Ocean.

| Where | Setting | Value |
| --- | --- | --- |
| Canvas | Background (solid) | `#FAFCFE` |
| Canvas | Grid lines, vertical and horizontal | `#EBF1F7` |
| Canvas | Scales text | `#55657A` |
| Canvas | Scales lines | `#D5DEE8` |
| Canvas | Crosshair (dashed) | `#8FA3BC` |
| Canvas | Watermark | off |
| Symbol | Body / Borders / Wick, up | `#1E7F77` |
| Symbol | Body / Borders / Wick, down | `#B04A7A` |
| Indicator | Colour scheme | Ocean |
| Indicator | Chart theme | Auto |
| Indicator | Candle colours | Scheme |
| Indicator | Up / Down candle colour | defaults (follow the scheme) |

Contrast on `#FAFCFE`: indicator colours at least 5.2:1, candles at least 4.7:1, scale text 5.8:1, grid 1.11:1 (deliberately faint).

## 13. Colorblind Day

Blue / orange on white.

| Where | Setting | Value |
| --- | --- | --- |
| Canvas | Background (solid) | `#FFFFFF` |
| Canvas | Grid lines, vertical and horizontal | `#EEF0F3` |
| Canvas | Scales text | `#5A6273` |
| Canvas | Scales lines | `#D9DDE3` |
| Canvas | Crosshair (dashed) | `#94A3B8` |
| Canvas | Watermark | off |
| Symbol | Body / Borders / Wick, up | `#2B77AB` |
| Symbol | Body / Borders / Wick, down | `#B06A10` |
| Indicator | Colour scheme | Colorblind safe |
| Indicator | Chart theme | Auto |
| Indicator | Candle colours | Scheme |
| Indicator | Up / Down candle colour | defaults (follow the scheme) |

Contrast on `#FFFFFF`: indicator colours at least 5.2:1, candles at least 4.3:1, scale text 6.1:1, grid 1.14:1 (deliberately faint).

## 14. Presentation

High-contrast white for screenshots, slides and screen sharing; colour only on with-bias candles.

| Where | Setting | Value |
| --- | --- | --- |
| Canvas | Background (solid) | `#FFFFFF` |
| Canvas | Grid lines, vertical and horizontal | `#F3F4F6` |
| Canvas | Scales text | `#334155` |
| Canvas | Scales lines | `#CBD5E1` |
| Canvas | Crosshair (dashed) | `#64748B` |
| Canvas | Watermark | off |
| Symbol | Body / Borders / Wick, up | `#1E8A7D` |
| Symbol | Body / Borders / Wick, down | `#C24A4D` |
| Indicator | Colour scheme | Classic |
| Indicator | Chart theme | Auto |
| Indicator | Candle colours | Structure bias |
| Indicator | Up / Down candle colour | defaults (follow the scheme) |

Contrast on `#FFFFFF`: indicator colours at least 5.1:1, candles at least 4.2:1; counter-bias greys `#8A95A6` 3.0:1, `#64748B` 4.8:1, scale text 10.4:1, grid 1.10:1 (deliberately faint).

## Checks

WCAG 2.x contrast `(L1 + 0.05) / (L2 + 0.05)`, computed from the hex values, not measured on screen. Targets per pack: every indicator colour of the selected scheme and theme at least 4.5:1 (they are caption text), candle colours (including the Structure bias greys) at least 3:1, scale text at least 4.5:1, grid lines below 1.2:1 so they stay in the background. Every pack meets all four. Dark backgrounds are at or below the luminance of `#131722`; tinted light backgrounds were checked individually.

A pack's colours are suggestions; any change keeps the same rules: indicator colours 4.5:1 on the background, candles 3:1, no orange, gold or violet candles (those are the FVG and session colours).
