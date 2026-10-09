# Dotting packages

Packages in this repository use the native, plain-JavaScript Dotting SDK.
`packages/core` is the first bounded package: generic text and number widgets,
a UTC wall-clock widget, and two small upstream BDF bitmap fonts. The package
ID is `core`, independent of any GitHub owner or repository name.

## Core package

Run the pinned-source importer/inventory check from the repository root:

```sh
python3 tools/import_fonts.py
```

It fetches exactly two pinned upstream BDF files and the Spleen license notice,
checks each download SHA-256, verifies BDF metrics and glyph counts, checks
the Tom Thumb notice, and writes the font files and deterministic JSON/CSV
inventory. It never converts or synthesizes fonts. The Spleen 5x8 proof font
is Git LFS tracked in `.gitattributes`; Tom Thumb remains a regular Git file.

`packages/core/manifest.json` records package/resource IDs, license and
provenance, upstream versions/URLs, and BDF hashes. `packages/core/README.md`
documents the dimensions, inventory, and rendering behavior. Text and number
widgets draw at the supplied origin using the selected BDF; they do not
measure, resize, wrap, reflow, or format numbers. The clock reads `ctx.time`
(Unix milliseconds) and renders UTC `HH:MM`; render functions are synchronous
and pure and do not access sources or ambient clocks. The runtime's separate
`ctx.elapsed` value remains available for deterministic animation widgets but
is not needed for this wall-clock widget.

Font redistribution notices are included under `packages/core/licenses/`.
The first two fonts are a small proof set only; do not import the larger font
catalog until the LFS path has been verified through an actual remote object
upload and download.
