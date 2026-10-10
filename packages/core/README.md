# `core` 0.1.0

Native SDK package ID: `core` (independent of GitHub ownership).

## Widgets

| Widget | Inputs | Config | Behavior |
| --- | --- | --- | --- |
| `text` | `value: string` | optional `color`, `size` (16 or 32), `font` | Draws the supplied string from local origin. |
| `number` | `value: number` | optional `color`, `size` (16 or 32), `font` | Uses JavaScript `String(value)` without locale, rounding, grouping, or other numeric formatting. |
| `clock` | none | optional `color`, `font` | Reads `ctx.time` (Unix milliseconds) and draws UTC `HH:MM`; it does not call the ambient clock. |

Each widget has its own named plain-JavaScript QuickJS handler and strict
input/config schemas in `manifest.json`. Drawing uses the placement's clipped
Canvas. Text starts at `(0, 0)` and is clipped by the host; the package does
not measure or negotiate dimensions, resize, wrap, or reflow. The runtime's
independent `ctx.elapsed` (placement-visible logical milliseconds) is
available to future deterministic animation but is not conflated with the
wall-time clock.

## Bundled BDF fonts

| Resource ID | File | Cell metrics | Glyphs | License | Upstream |
| --- | --- | --- | ---: | --- | --- |
| `tom-thumb-4x6` | `assets/fonts/tom-thumb-4x6.bdf` | 4x6 cell; BDF bounding box 3x6, ascent 5, descent 1 | 203 | MIT | Robey Pointer, upstream BDF URL and notice in the manifest/license file |
| `spleen-5x8` | `assets/fonts/spleen-5x8.bdf` | 5x8; BDF bounding box 5x8, ascent 7, descent 1 | 472 | BSD-2-Clause | Spleen 2.2.0, Frederic Cambus |
| `unifont-16.0.04` | `assets/fonts/unifont-16.0.04.bdf` | 16x16; BDF bounding box 16x16, ascent 14, descent 2 | 57,086 | GPL-2.0-or-later WITH Font-exception-2.0 (also dual-licensed OFL 1.1 upstream) | GNU Unifont 16.0.04 |
| `fonts-fusion-pixel-8-mono-e4e1a8b5cf7f` | `fonts/fusion-pixel/8px-mono/fusion-pixel-8px-monospaced-latin.bdf` | Latin, 8px monospaced | — | OFL-1.1 | Fusion Pixel |
| `fonts-fusion-pixel-10-mono-e71b599d29d3` | `fonts/fusion-pixel/10px-mono/fusion-pixel-10px-monospaced-latin.bdf` | Latin, 10px monospaced | — | OFL-1.1 | Fusion Pixel |
| `fonts-fusion-pixel-12-mono-6b2a5a087d97` | `fonts/fusion-pixel/12px-mono/fusion-pixel-12px-monospaced-latin.bdf` | Latin, 12px monospaced | — | OFL-1.1 | Fusion Pixel |

All six files are unmodified upstream BDFs. Exact SHA-256, byte count, font
header metrics, glyph count, license, and source URL are recorded in the compact
manifest and package inventory; full importer inventory is kept in ignored
`.source-cache/inventory/`.
The importer reads the Unifont BDF and complete `COPYING` from the sibling
runtime checkout read-only; it does not alter that checkout. The complete
redistribution texts are in `licenses/`. Core contains only these six BDF
resources; other English families and Spleen sizes are in `fonts-extra`.
