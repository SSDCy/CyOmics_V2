# Fixed synthetic log2 validator fixtures

The `fixtures/` subtree preserves the sealed R1 validator fixture paths and bytes.
It contains six literal/source/oracle files and22 actual native R readback files,
eleven each for Intensity and LFQ. Inputs are fixed synthetic eight-row records,
including duplicate IDs, excluded flag overlaps, zeros, blanks and all-NA rows.
No actual user protein file or sequence is included.

`fixture-provenance.json` records the original core14-case source review and the
independent literal/Fraction oracle. `actual-byte-provenance.json` records the
existing approved native R collection pin and all22 readback byte pins. These
files are preserved verbatim, including the distinction between literal expected
cells and actual synthetic R output bytes. Copying and validating those outputs
does not itself prove new R execution, scientific applicability, Owner approval,
publication, or full pipeline readiness.

The extra preserved inner `fixtures` layer is intentional: tests adjust only the
global fixture root, keeping every original test method AST unchanged. No new
R output was generated or reformatted for this source-only promotion.
