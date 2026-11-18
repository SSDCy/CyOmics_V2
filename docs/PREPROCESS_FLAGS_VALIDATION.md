# Preprocessing flag filter: validation boundary

Status on 2026-11-18: reviewed scientific source; not registered or connected to
the website. R script SHA256:
`801fa1f096b8cd0659ee02403b7fe47b38febd8d39b369b904b6377a561a190c`.

The script accepts explicit Reverse, Potential contaminant and Only identified
by site switches, preserving zero values and applying no log2 transform,
normalization or imputation. It keeps all exclusion reasons and source row/ID
lineage; exclusion is their union. Missing enabled flags refuse the input.

Source/fixture review passed 7 author and 5 independent checks. A separately
reviewed execution wrapper passed 15 distinct pure checks. One actual isolated
R 4.6.1 parse and one all-enabled eight-row Intensity fixture both exited 0;
the parent and SSH exited 0 with empty stderr. Ten outputs and two process
evidence files were read back with their exact expected hashes. Independent
scientific review checked all 251 cells, including headers, of nine tables
against the literal specifications and a separate model. Source rows 1/6/8
remain; five rows are excluded, including two with overlapping causes. Six
retained intensity cells contain one blank, two zeros and three positive values;
only the blank becomes working NA. Imputed cells remain zero.

The observed total elapsed time was about 1.42 seconds; this is not a CPU
measurement or a hard aggregate containment guarantee. The wrapper's maximum
of two `/dev/null` opens applies only to the added O_RDWR writable exception.
The existing public read policy is retained.

No real user input, LFQ, all-disabled, empty, negative or mixed-switch case was
executed in this unit. Persistent source/configuration lineage, quota and Owner
checks, admission/cancellation handling, the new ten-output execution profile,
worker and API registration, and website end-to-end validation remain pending.
The existing raw-QC seven-output profile is not reused implicitly. The earlier
ordinary raw-QC worker attempt stopped at database wire readiness; this unit
does not mark that attempt successful or replay its consumed latch.

Private audit references (not deployed dependencies):
- PREPROCESS-R-UNIT-001-R1 actual parse and fixture receipts.
- QA-PREPROCESS-R-UNIT-ACTUAL-001 independent receipt SHA256:
  `afb9ae139acef377d143ed4a18efbbcf17718903af390f2e43f56f1a83645e08`.

These source files contain no user data, credentials or runtime activation.
