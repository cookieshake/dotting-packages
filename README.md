# Dotting packages

Packages use the native, plain-JavaScript Dotting SDK. Package IDs are independent
of GitHub ownership. Fonts are original native BDFs, never converted or synthesized.

| Package path / ID | BDF entries | Original BDF bytes | Scope |
| --- | ---: | ---: | --- |
| `packages/core` / `core` | 81 | 62,123,296 | Core widgets; Tom Thumb, Spleen, Unifont, Tamzen/Powerline, Bitocra/Bitbuntu, Gohu/HiDPI, Scientifica, Galmuri, Misaki, k8x12, and six Fusion Pixel Latin variants |
| `packages/font-fusion-pixel` / `font-fusion-pixel` | 42 | 162,339,786 | All native 8/10/12px monospaced/proportional regional variants |
| `packages/font-ark-pixel` / `font-ark-pixel` | 42 | 59,321,705 | Native 10/12px variants plus the official archived 16px release; both release notices retained |

There are 165 entries and 159 unique original SHA-256 hashes. Silver is excluded.
Ark is a separate resource package, not an addition to core. Every BDF except
Tom Thumb is Git LFS tracked. Install a pinned hydrated subtree; a Git checkout
containing LFS pointer text is not runnable.

## Catalog assembly and audit

Family source inventories and full coverage/provenance are under
`packages/core/inventory/families/`; package-level inventory is under each
package's `inventory/`. Runtime manifests contain only compact metadata to fit
the 64 KiB bound. Source BDFs and unused notices are staged under ignored
`.source-cache/`, outside every installed package. Never stage this cache.

```sh
python3 tools/assemble_catalog.py
python3 tools/validate_catalog.py
python3 tools/test_catalog_recovery.py
python3 tools/verify_runtime_readiness.py --binary /path/to/dotting
```

Validation rejects undeclared BDFs/notices, checks actual counts/bytes and
original/license hashes. Recovery tests cover idempotence, undeclared-file
rejection, offline original-byte importer routing, and LFS attributes. Offline
replay is not evidence of upstream downloads or remote LFS object availability.
Proofs stay in ignored cache directories, not in public package content.

The runtime's 288 MiB disk bound applies per selected package subtree, including
inventory/notices for installation; it is not a sum across independent installs.
Per-font, decoded-cache, and render preparation limits are independent. The CLI
`app package` exports declared runtime files only, so its snapshot intentionally
differs from the full source subtree snapshot used for repository installation.

### Gohu provenance distinction

The 12 captured BDFs match upstream Git commit
`cc36b8c9fed7141763e55dcee0a97abffcf08224` byte-for-byte. The official website's
`https://font.gohu.org/gohufont-2.1.tar.gz` is a PCF-only archive (88,915 bytes,
SHA-256 `758d62c9350d51ae3738aff4bbcefa9ea6d173baf5b169232c895b6de3a1ba81`),
not a native BDF source. No PCF conversion was performed. Six BDFs match the
official Git `2.1` tag `3cf9ce5771b7a81a20ff49cb11708736fcd9a5d0`; the two
Unicode 14px files differ, and HiDPI files are absent from that tag. Therefore
the full 12-file set is **not represented as an unchanged 2.1 BDF release**.
The preserved WTFPL notice matches that official tag.

## Core package

Run the pinned-source importer/inventory check from the repository root:

```sh
python3 tools/import_fonts.py
```

It fetches pinned Tom Thumb/Spleen originals and the Spleen license notice,
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
The older core README describes the initial four-font/widget proof slice; the
manifests and family inventories are authoritative for the expanded catalog.
Verify uploaded LFS objects through an independent remote readback before
claiming a publication is installable.
