Preprocessing V2 interface tests

From a complete frontend checkout with its existing reviewed dependencies:

    node tests/preprocess-v2.test.mjs

Run directly to execute the seven cases in this file only. Fixtures resolve from
import.meta.url to tests/fixtures/preprocess-v2-contract, including the existing
R1-addendum and final-failed-state. No external audit directory is needed.
Do not install/upgrade dependencies for this replay. Record the SDK identity:
a TEST-only double replay is not a real-SDK, live Auth, DB or native R result.
The actual-A real-SDK29 replay remains Root's separate next phase of this item.
