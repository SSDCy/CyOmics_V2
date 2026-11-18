Preprocessing execution module (full Linux tests)

Use the existing CPython environment with the repository dependencies available.
From a complete checkout root (backend/ and frontend/ alongside each other):

    python -I -B backend/tests/test_preprocess_execution.py

A complete candidate tree with the same backend/ + frontend/ layout uses the same
command from its candidate root. Sparse test-only overrides must first be applied
to that complete tree; they do not search an external baseline or audit folder.
The module reads public contract metadata from
frontend/tests/fixtures/preprocess-v2-contract, including R1-addendum and
final-failed-state, and existing helpers under backend/tests. No fixture or helper
needs to be copied from an external audit. The existing reference_preprocess_http.py
is part of the preprocessing implementation test sources, not a new helper here.

The 36 original top-level tests and two parent-mask regression tests stay enabled.
Two wrapper tests also run the 18 original runner cases and 2 RawQC HTTP cases:
38 - 2 + 18 + 2 = 56 leaf cases; the private repair copies are not added again.
These are full Linux fake-I/O/pure-validation tests, not Windows subset, real
R/Auth/DB validation or permission to activate a service. Use -B so runtime source
checks stay inert and no bytecode files are written.
