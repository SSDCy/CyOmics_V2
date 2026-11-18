# Explicit two-group limma scientific core

This unregistered R core consumes an already validated log2 matrix and an
explicit, confirmed biological sample design. It supports 4–64 samples with at
least two observed biological replicates per group. It never infers groups,
applies another log transform, imputes values or changes the user's raw file.
See CONTRACT.md for the complete input, method and five-output contract.

The implementation calls official limma::lmFit, limma::contrasts.fit and
limma::eBayes, followed by stats::p.adjust(method="BH") over every final finite
tested P value for the one contrast. Original row ordinals and protein-group
text remain intact; non-testable rows retain explicit reasons and NA statistics.
No DEP threshold, identifier deduplication or top-k selection changes that family.

Pinned validation environment: R 4.6.1, limma 3.68.5, statmod 1.5.2. Statistical
parameters are explicit in the script and contract. The script SHA256 is
9807467ba2ae54efd5ef77e5c15dbe0525d205f4d2988ba24995ea932024c908.

An actual isolated unit completed parse, forward and reverse native R processes,
all exit0, and produced ten result files. Independent readback verified those
files and three process observations: eight source rows, six testable rows,
two non-testable rows with preserved reasons, counts/means/FC/residual df,
all-six-P BH correction and direction-reversal invariants. Session provenance
matches the pinned environment. These are synthetic fixtures, not a real study.

The preceding failure was caused by appending an extra newline to reconstructed
TSV text before R's textConnection appended its own newline. A separate actual
comparison proved the extra empty record and its removal. Only that reader line
changed; strict empty-field/blank-line handling and statistical expressions
remain unchanged. Original failure evidence is retained in review records.

Absolute moderated-t/P and empirical-Bayes prior reference validation, four
additional reader edge regressions, authoritative preprocessing lineage,
real experimental design, trusted worker publication and Owner/API/browser
acceptance remain pending. This core is not wired to a task, API or page.
The bundled arithmetic fixture and five-value BH example are transparent
synthetic checks, not a biological golden or independent moderated-t/P golden.
Do not feed the raw preserve/none QC matrix directly to this log2 core.
