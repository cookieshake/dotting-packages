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
| `spleen-32x64` | `assets/fonts/spleen-32x64.bdf` | 32x64; BDF bounding box 32x64, ascent 52, descent 12 | 978 | BSD-2-Clause | Spleen 2.2.0, Frederic Cambus |
| `unifont-16.0.04` | `assets/fonts/unifont-16.0.04.bdf` | 16x16; BDF bounding box 16x16, ascent 14, descent 2 | 57,086 | GPL-2.0-or-later WITH Font-exception-2.0 (also dual-licensed OFL 1.1 upstream) | GNU Unifont 16.0.04 |

All four files are the unmodified upstream BDFs. Exact SHA-256, byte count, font
header metrics, glyph count, license, and source URL are emitted in
`inventory/fonts.json` and `inventory/fonts.csv` by `python3 tools/import_fonts.py`.
The importer reads the Unifont BDF and complete `COPYING` from the sibling
runtime checkout read-only; it does not alter that checkout. The complete
redistribution texts are in `licenses/`. This four-font slice is not a complete
font catalog; see `inventory/fonts-todo.json` for explicitly pending candidates.
